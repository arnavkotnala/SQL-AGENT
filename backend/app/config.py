"""Central application settings. Every tunable value lives here (model name included)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore", validate_assignment=True
    )

    # --- Gemini ---
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"
    gemini_temperature: float = 0.1
    gemini_timeout_seconds: float = 60.0
    gemini_max_retries: int = 3

    # --- Agent ---
    agent_max_iterations: int = Field(default=5, ge=1, le=10)
    approval_timeout_seconds: int = 1800
    max_concurrent_runs: int = 4
    # Data sent to Gemini: a few sample rows per table (helps it match value formats) and the first result
    # rows for evaluation. Set both to 0 if your data must not leave the machine beyond the schema.
    schema_sample_rows: int = Field(default=3, ge=0, le=20)
    llm_result_preview_rows: int = Field(default=10, ge=0, le=50)

    # --- Query execution ---
    query_timeout_seconds: float = 15.0
    max_result_rows: int = 50_000
    fetch_chunk_size: int = 1_000
    max_modified_rows: int = 5_000

    # --- Files ---
    data_dir: Path = BACKEND_DIR / "data"
    max_upload_mb: int = 100
    # Uploads and generated exports older than this are deleted at startup (backups are kept).
    file_retention_hours: int = 24
    # The app is designed to run locally (backend bound to 127.0.0.1), so users may
    # point it at files on their own disk. Set to false when deploying for other users.
    allow_local_file_paths: bool = True

    # --- Server / deployment ---
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # In local mode (ALLOW_LOCAL_FILE_PATHS=true) only these Host headers are accepted, which blocks
    # DNS-rebinding pages from reaching the file-path API. Add your LAN name/IP if you expose it.
    trusted_hosts: list[str] = ["127.0.0.1", "localhost", "[::1]"]
    # Optional shared access key. When set, every /api call (except /api/health) needs
    # "Authorization: Bearer <key>". Strongly recommended for any internet-facing deployment.
    app_access_token: SecretStr | None = None
    # Built frontend served by FastAPI in production (single process, single port).
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value().strip())

    def ensure_dirs(self) -> None:
        for d in (self.samples_dir, self.uploads_dir, self.workspaces_dir, self.exports_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
