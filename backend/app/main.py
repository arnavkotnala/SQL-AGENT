"""FastAPI application factory. Run: uvicorn app.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import hmac
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .agent.loop import Agent
from .agent.manager import RunManager
from .api.routes import router
from .config import Settings, get_settings
from .datasources.registry import SourceRegistry
from .export.exporter import ExportStore
from .llm.provider import LLMProvider, build_provider
from .sample_data.generate import generate_all


def purge_stale_files(settings: Settings) -> None:
    """Workspace mirrors belong to in-memory sources and are useless after a restart; uploads and exports
    expire after `file_retention_hours`. Backups of users' files are never deleted automatically."""
    cutoff = time.time() - settings.file_retention_hours * 3600
    for child in settings.workspaces_dir.iterdir():
        _remove(child)
    for root in (settings.uploads_dir, settings.exports_dir):
        for child in root.iterdir():
            try:
                if child.stat().st_mtime < cutoff:
                    _remove(child)
            except OSError:
                pass


def _remove(path: Path) -> None:
    try:
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    except OSError:
        logging.getLogger("loopsql").warning("Could not remove stale file %s", path.name)


class _RedactSecrets(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            msg = record.getMessage()
            for s in self.secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, ()
        return True


def create_app(settings: Settings | None = None, llm_factory: Callable[[], LLMProvider] | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    secrets = [x.get_secret_value() for x in (settings.gemini_api_key, settings.app_access_token) if x]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(_RedactSecrets(secrets))

    registry = SourceRegistry(settings)
    exports = ExportStore(settings.exports_dir)
    agent = Agent(settings, registry, exports, llm_factory or (lambda: build_provider(settings)))
    runs = RunManager(settings, agent)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        purge_stale_files(settings)
        # Regenerated at every start so date-relative sample data (e.g. "tomorrow") stays meaningful.
        generate_all(settings.samples_dir)
        yield
        runs.shutdown()
        registry.close_all()

    app = FastAPI(title="LoopSQL AI", version="1.0.0", lifespan=lifespan)
    app.state.settings, app.state.registry, app.state.exports, app.state.runs = settings, registry, exports, runs
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    if settings.allow_local_file_paths:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    token = settings.app_access_token.get_secret_value() if settings.app_access_token else ""

    @app.middleware("http")
    async def access_gate(request: Request, call_next):
        path = request.url.path
        if token and path.startswith("/api") and path != "/api/health" and request.method != "OPTIONS":
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not supplied and path.startswith("/api/exports/"):
                supplied = request.query_params.get("key", "")  # plain <a href> downloads
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse({"detail": "Access key required."}, status_code=401)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    app.include_router(router)

    dist = settings.frontend_dist
    if (dist / "index.html").is_file():
        dist_root = dist.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = (dist_root / full_path).resolve()
            if full_path and candidate.is_file() and dist_root in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist_root / "index.html")

    return app


app = create_app()
