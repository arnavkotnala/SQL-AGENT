"""HTTP API. Secrets (API keys, DB passwords) never appear in responses."""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..agent.run import RunOptions, RunStatus
from ..datasources.base import DataSourceError
from ..datasources.registry import SAMPLES
from ..export.exporter import XLSX_MEDIA, CSV_MEDIA, ExportMetadata, slugify, write_csv, write_xlsx

router = APIRouter(prefix="/api")


def _state(request: Request):
    return request.app.state


# ---------------------------------------------------------------------------------------------
@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    s = _state(request).settings
    return {
        "status": "ok",
        "gemini_configured": s.gemini_configured,
        "model": s.gemini_model,
        "max_iterations": s.agent_max_iterations,
        "max_result_rows": s.max_result_rows,
        "local_paths_enabled": s.allow_local_file_paths,
        "auth_required": bool(s.app_access_token and s.app_access_token.get_secret_value()),
    }


@router.get("/samples")
def samples() -> list[dict[str, Any]]:
    return [{"id": k, "title": v["title"], "description": v["description"], "prompts": v["prompts"]}
            for k, v in SAMPLES.items()]


# ---------------------------------------------------------------------------------------------
class ConnectRequest(BaseModel):
    type: Literal["sample", "path", "url"]
    value: str = Field(max_length=4096)
    name: Optional[str] = Field(default=None, max_length=120)


def _connect_error(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/sources")
def list_sources(request: Request) -> list[dict[str, Any]]:
    return _state(request).registry.list()


@router.post("/sources")
def connect_source(body: ConnectRequest, request: Request) -> dict[str, Any]:
    reg = _state(request).registry
    try:
        if body.type == "sample":
            entry = reg.add_sample(body.value)
        elif body.type == "path":
            entry = reg.add_local_path(body.value)
        else:
            entry = reg.add_url(body.value, body.name)
    except DataSourceError as e:
        raise _connect_error(e) from None
    return entry.to_dict()


@router.post("/sources/upload")
async def upload_source(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    s = _state(request).settings
    limit = s.max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"File is larger than the {s.max_upload_mb} MB limit.")
    try:
        entry = _state(request).registry.add_upload(file.filename or "upload", content)
    except DataSourceError as e:
        raise _connect_error(e) from None
    return entry.to_dict()


@router.get("/sources/{source_id}/schema")
def source_schema(source_id: str, request: Request) -> dict[str, Any]:
    try:
        entry = _state(request).registry.get(source_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Source not found.") from None
    try:
        adapter = entry.adapter
        if hasattr(adapter, "refresh_if_changed"):
            adapter.refresh_if_changed()
        adapter.schema_sample_rows = 0  # type: ignore[attr-defined]
        return adapter.inspect_schema().to_dict()
    except DataSourceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.delete("/sources/{source_id}")
def remove_source(source_id: str, request: Request) -> dict[str, str]:
    _state(request).registry.remove(source_id)
    return {"status": "removed"}


# ---------------------------------------------------------------------------------------------
class RunRequest(BaseModel):
    request: str = Field(min_length=3, max_length=4000)
    source_id: Optional[str] = None
    require_approval: bool = False
    write_mode: Literal["copy", "in_place", "browser"] = "copy"


@router.post("/runs")
def start_run(body: RunRequest, request: Request) -> dict[str, Any]:
    opts = RunOptions(source_id=body.source_id, require_approval=body.require_approval, write_mode=body.write_mode)
    run = _state(request).runs.start(body.request.strip(), opts)
    return run.to_dict()


@router.get("/runs")
def list_runs(request: Request) -> list[dict[str, Any]]:
    return _state(request).runs.list()


def _get_run(request: Request, run_id: str):
    run = _state(request).runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request, since: int = Query(0, ge=0)) -> dict[str, Any]:
    return _get_run(request, run_id).to_dict(since_seq=since)


class RespondRequest(BaseModel):
    type: Literal["source", "clarification", "sql_approval", "change_approval"]
    source_id: Optional[str] = None
    answer: Optional[str] = Field(default=None, max_length=2000)
    approved: Optional[bool] = None
    sql: Optional[str] = Field(default=None, max_length=20000)
    mode: Optional[Literal["copy", "in_place", "browser"]] = None


@router.post("/runs/{run_id}/respond")
def respond(run_id: str, body: RespondRequest, request: Request) -> dict[str, Any]:
    run = _get_run(request, run_id)
    try:
        run.respond(body.type, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"status": "accepted"}


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, request: Request) -> dict[str, str]:
    _get_run(request, run_id).cancel()
    return {"status": "cancelling"}


@router.get("/runs/{run_id}/rows")
def run_rows(run_id: str, request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
             search: str = Query("", max_length=200)) -> dict[str, Any]:
    run = _get_run(request, run_id)
    rows = run.rows
    if search:
        needle = search.lower()
        rows = [r for r in rows if any(needle in str(v).lower() for v in r if v is not None)]
    total = len(rows)
    start = (page - 1) * page_size
    return {"columns": run.columns, "rows": [list(r) for r in rows[start:start + page_size]], "total": total,
            "page": page, "page_size": page_size, "all_rows": len(run.rows)}


@router.post("/runs/{run_id}/export")
def export_partial(run_id: str, request: Request, fmt: Literal["xlsx", "csv"] = Query("xlsx")) -> dict[str, Any]:
    """Export the latest retrieved result on demand (e.g. after a run stopped without full success).
    The workbook's info sheet records the run status so it is never mistaken for a verified result."""
    st = _state(request)
    run = _get_run(request, run_id)
    if not run.columns:
        raise HTTPException(status_code=400, detail="This run has no query result to export.")
    base = f"loopsql_{slugify(run.request)}_{run.id[:6]}"
    export_id, path = st.exports.new_path(f"{base}.{fmt}")
    if fmt == "csv":
        n = write_csv(path, run.columns, run.rows)
        media = CSV_MEDIA
    else:
        meta = ExportMetadata(request=run.request, goal=(run.goal or {}).get("interpreted_goal", ""),
                              source=str((run.source or {}).get("name")), sql=run.sql or "", params=run.params,
                              truncated=bool((run.result_meta or {}).get("truncated")),
                              row_limit=st.settings.max_result_rows,
                              summary=(run.summary or "") + ("" if run.status == RunStatus.COMPLETED else
                                                             f" [Run status: {run.status}; result not confirmed]"))
        n = write_xlsx(path, run.columns, run.rows, meta, run.column_hints)
        media = XLSX_MEDIA
    return st.exports.register(export_id, path, media, n).to_dict()


@router.get("/exports/{export_id}")
def download(export_id: str, request: Request) -> FileResponse:
    ef = _state(request).exports.get(export_id)
    if ef is None:
        raise HTTPException(status_code=404, detail="Export not found or expired.")
    return FileResponse(ef.path, media_type=ef.media_type, filename=ef.filename)
