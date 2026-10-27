"""In-memory registry of connected data sources for this server session."""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..config import Settings
from .base import DataSourceAdapter, DataSourceError
from .spreadsheet import EXCEL_SUFFIXES, SPREADSHEET_SUFFIXES, SpreadsheetSource
from .sql_adapter import SQLiteAdapter, adapter_from_url

SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db3"}
ALLOWED_FILE_SUFFIXES = SQLITE_SUFFIXES | SPREADSHEET_SUFFIXES

SAMPLES = {
    "education": {
        "title": "Education (sample)",
        "file": "education.db",
        "description": "Students, courses, subjects, attendance and marks.",
        "prompts": [
            "Find all students whose attendance is below 50%.",
            "Find students who scored below 40 in any subject. Export their names, roll numbers, subjects and marks.",
            "Calculate average marks for each course.",
            "Find students whose attendance is below 50% and export their names, roll numbers, and attendance percentages to Excel.",
            "List the top 5 students by average final exam marks in each course.",
            "Find students enrolled in 2015.",
        ],
    },
    "hospital": {
        "title": "Hospital (sample)",
        "file": "hospital.db",
        "description": "Departments, doctors, patients and appointments (dates relative to today).",
        "prompts": [
            "Find hospital appointments scheduled for tomorrow and export appointment IDs, departments, and dates.",
            "Count appointments per department for the next 7 days.",
            "Which doctors have the most completed appointments this month?",
        ],
    },
}


@dataclass
class SourceEntry:
    id: str
    kind: str  # sample | sqlite | excel | csv | server
    adapter: DataSourceAdapter
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        d = self.adapter.describe()
        d.update({"id": self.id, "kind": self.kind, "created_at": self.created_at,
                  "editable": bool(getattr(self.adapter, "editable", False))})
        return d


def safe_filename(name: str, default: str = "file") -> str:
    base = Path(name.replace("\\", "/")).name  # strips any directory components
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .")
    return base[:120] or default


def normalize_user_path(raw: str) -> Path:
    """Accept what users typically paste: quoted paths ("Copy as path"), file:/// URIs, ~ paths."""
    s = (raw or "").strip().strip('"').strip("'").strip()
    if not s:
        raise DataSourceError("Please enter a file path.")
    low = s.lower()
    if low.startswith(("http://", "https://")):
        if any(h in low for h in ("sharepoint.com", "onedrive", "1drv.ms", "docs.google.com", "drive.google.com")):
            raise DataSourceError(
                "Cloud links (OneDrive/SharePoint/Google) can't be opened directly. Use the local path of the "
                "synced file (e.g. C:\\Users\\you\\OneDrive\\file.xlsx) or download the file and upload it.")
        raise DataSourceError("Web links are not supported. Give a local file path or upload the file.")
    if low.startswith("file:"):
        parsed = urlparse(s)
        p = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", p):
            p = p[1:]
        s = (f"//{parsed.netloc}{p}" if parsed.netloc else p)
    return Path(s).expanduser()


class SourceRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._sources: dict[str, SourceEntry] = {}

    def _add(self, kind: str, adapter: DataSourceAdapter) -> SourceEntry:
        entry = SourceEntry(id=uuid.uuid4().hex[:12], kind=kind, adapter=adapter)
        with self._lock:
            self._sources[entry.id] = entry
        return entry

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._sources.values()]

    def get(self, source_id: str) -> SourceEntry:
        with self._lock:
            entry = self._sources.get(source_id)
        if entry is None:
            raise KeyError(source_id)
        return entry

    def remove(self, source_id: str) -> None:
        with self._lock:
            entry = self._sources.pop(source_id, None)
        if entry:
            entry.adapter.close()

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._sources.values())
            self._sources.clear()
        for e in entries:
            e.adapter.close()

    # ---- constructors ----------------------------------------------------------------------
    def add_sample(self, name: str) -> SourceEntry:
        if name not in SAMPLES:
            raise DataSourceError(f"Unknown sample database '{name}'.")
        path = self.settings.samples_dir / SAMPLES[name]["file"]
        if not path.exists():
            from ..sample_data.generate import generate_all
            generate_all(self.settings.samples_dir)
        return self._add("sample", SQLiteAdapter(path, SAMPLES[name]["title"]))

    def add_file(self, path: Path, origin: str = "path", display_name: str | None = None) -> SourceEntry:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".xls":
            raise DataSourceError("Legacy .xls files are not supported. Open it in Excel and 'Save As' .xlsx.")
        if suffix not in ALLOWED_FILE_SUFFIXES:
            raise DataSourceError(
                f"Unsupported file type '{suffix or '(none)'}'. Supported: Excel (.xlsx, .xlsm), CSV, "
                "SQLite (.db, .sqlite, .sqlite3).")
        if not path.is_file():
            raise DataSourceError(f"File not found: {path}")
        if suffix in SQLITE_SUFFIXES:
            return self._add("sqlite", SQLiteAdapter(path, display_name or path.name))
        src = SpreadsheetSource(path, self.settings.workspaces_dir, display_name or path.name, origin=origin)
        return self._add("excel" if suffix in EXCEL_SUFFIXES else "csv", src)

    def add_local_path(self, raw_path: str) -> SourceEntry:
        if not self.settings.allow_local_file_paths:
            raise DataSourceError("Opening files by local path is disabled on this server. Upload the file instead.")
        path = normalize_user_path(raw_path)
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise DataSourceError(f"File not found: {path}") from None
        return self.add_file(path, origin="path")

    def add_upload(self, filename: str, content: bytes) -> SourceEntry:
        name = safe_filename(filename)
        if Path(name).suffix.lower() not in ALLOWED_FILE_SUFFIXES:
            return self.add_file(Path(name))  # raises the friendly unsupported-type error
        if len(content) > self.settings.max_upload_mb * 1024 * 1024:
            raise DataSourceError(f"File is larger than the {self.settings.max_upload_mb} MB upload limit.")
        dest_dir = self.settings.uploads_dir / uuid.uuid4().hex
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        dest.write_bytes(content)
        return self.add_file(dest, origin="upload", display_name=name)

    def add_url(self, url: str, display_name: str | None = None) -> SourceEntry:
        return self._add("server", adapter_from_url(url.strip(), display_name))
