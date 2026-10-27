"""SQLAlchemy-backed adapters. SQLite is fully implemented and tested; PostgreSQL/MySQL reuse the same
code path with dialect-specific read-only/timeout hooks (they need the matching driver installed)."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import ArgumentError, DBAPIError, NoSuchModuleError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from .base import (
    ColumnInfo,
    DataSourceAdapter,
    DataSourceError,
    ForeignKeyInfo,
    QueryExecutionError,
    QueryResult,
    SchemaInfo,
    TableInfo,
)

# SQLite functions that can touch the filesystem or load native code.
_SQLITE_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "zipfile"}
_SQLITE_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}


def _sqlite_read_only_authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
    if action in _SQLITE_ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        name = (arg2 or "").lower()
        return sqlite3.SQLITE_DENY if name in _SQLITE_DENIED_FUNCTIONS else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return value


class SQLAlchemyAdapter(DataSourceAdapter):
    dialect = "sqlite"

    def __init__(self, engine: Engine, display_name: str, display_target: str, count_rows: bool = True):
        self.engine = engine
        self.display_name = display_name
        self.display_target = display_target  # already secret-free
        self.count_rows = count_rows
        self.schema_sample_rows = 3

    # ---- description / introspection -------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {"name": self.display_name, "dialect": self.dialect, "target": self.display_target,
                "editable": self.editable}

    def _table_note(self, table: str) -> str | None:
        return None

    def _column_note(self, table: str, column: str) -> str | None:
        return None

    def inspect_schema(self) -> SchemaInfo:
        try:
            insp = inspect(self.engine)
            tables: list[TableInfo] = []
            names = [(n, "table") for n in insp.get_table_names()] + [(n, "view") for n in insp.get_view_names()]
            for name, kind in names:
                if name.startswith("sqlite_"):
                    continue
                pk_cols = set((insp.get_pk_constraint(name) or {}).get("constrained_columns") or [])
                cols = [
                    ColumnInfo(
                        name=c["name"],
                        type=str(c["type"]) if c.get("type") is not None else "TEXT",
                        nullable=bool(c.get("nullable", True)),
                        primary_key=c["name"] in pk_cols,
                        note=self._column_note(name, c["name"]),
                    )
                    for c in insp.get_columns(name)
                ]
                fks = [
                    ForeignKeyInfo(fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
                    for fk in (insp.get_foreign_keys(name) if kind == "table" else [])
                    if fk.get("referred_table")
                ]
                tables.append(TableInfo(name=name, kind=self._table_kind(kind), columns=cols, foreign_keys=fks,
                                        note=self._table_note(name)))
            self._add_counts_and_samples(tables)
            return SchemaInfo(dialect=self.dialect, tables=tables)
        except SQLAlchemyError as e:
            raise DataSourceError(f"Could not inspect schema: {type(e).__name__}: {_safe_db_error(e)}") from None

    def _table_kind(self, kind: str) -> str:
        return kind

    def _add_counts_and_samples(self, tables: list[TableInfo]) -> None:
        preparer = self.engine.dialect.identifier_preparer
        with self.engine.connect() as conn:
            for t in tables:
                q = preparer.quote(t.name)
                try:
                    if self.count_rows:
                        t.row_count = conn.execute(text(f"SELECT COUNT(*) FROM {q}")).scalar()
                    if self.schema_sample_rows:
                        res = conn.execute(text(f"SELECT * FROM {q} LIMIT {int(self.schema_sample_rows)}"))
                        cols = list(res.keys())
                        t.sample_rows = [{c: _jsonable(v) for c, v in zip(cols, row)} for row in res]
                except SQLAlchemyError:
                    conn.rollback()

    # ---- execution -------------------------------------------------------------------------
    def _before_execute(self, conn: Connection, timeout_s: float) -> None:
        """Dialect hook: enforce read-only + timeout on this connection."""

    def _after_execute(self, conn: Connection) -> None:
        """Dialect hook: undo per-execution connection state."""

    def execute_read(self, sql: str, params: dict[str, Any], max_rows: int, timeout_s: float) -> QueryResult:
        start = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                self._before_execute(conn, timeout_s)
                try:
                    result = conn.execute(text(sql), params)
                    columns = list(result.keys())
                    rows: list[tuple[Any, ...]] = []
                    truncated = False
                    while True:
                        chunk = result.fetchmany(1000)
                        if not chunk:
                            break
                        for r in chunk:
                            if len(rows) >= max_rows:
                                truncated = True
                                break
                            rows.append(tuple(_jsonable(v) for v in r))
                        if truncated:
                            break
                    result.close()
                finally:
                    self._after_execute(conn)
                    conn.rollback()  # never persist anything
        except DBAPIError as e:
            msg = _safe_db_error(e)
            if "interrupted" in msg.lower() or "timeout" in msg.lower() or "canceling statement" in msg.lower():
                raise QueryExecutionError(f"Query exceeded the {timeout_s:g}s execution timeout.", recoverable=True) from None
            if "not authorized" in msg.lower() or "read-only" in msg.lower() or "readonly" in msg.lower():
                raise QueryExecutionError(f"Blocked by database permissions: {msg}", recoverable=False) from None
            raise QueryExecutionError(msg, recoverable=True) from None
        except SQLAlchemyError as e:
            raise QueryExecutionError(_safe_db_error(e), recoverable=True) from None
        elapsed = (time.perf_counter() - start) * 1000
        return QueryResult(columns=columns, column_types=[None] * len(columns), rows=rows,
                           truncated=truncated, elapsed_ms=round(elapsed, 2))

    def close(self) -> None:
        self.engine.dispose()


def _safe_db_error(e: Exception) -> str:
    """Driver error text without the SQL statement/params SQLAlchemy appends (those can be long)."""
    orig = getattr(e, "orig", None)
    msg = str(orig) if orig is not None else str(e).split("\n[SQL:")[0]
    return msg.split("\n(Background on this error")[0].strip()


class SQLiteAdapter(SQLAlchemyAdapter):
    dialect = "sqlite"

    def __init__(self, path: Path, display_name: str):
        path = Path(path).resolve()
        if not path.is_file():
            raise DataSourceError(f"SQLite file not found: {path.name}")
        uri = path.as_uri() + "?mode=ro"

        def creator() -> sqlite3.Connection:
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            return conn

        engine = create_engine("sqlite://", creator=creator, poolclass=NullPool)
        super().__init__(engine, display_name, display_target=path.name)
        self.path = path
        try:
            with engine.connect() as conn:  # fail fast on non-SQLite files
                conn.execute(text("SELECT name FROM sqlite_master LIMIT 1")).fetchall()
        except SQLAlchemyError as e:
            raise DataSourceError(f"Not a readable SQLite database: {_safe_db_error(e)}") from None

    def _before_execute(self, conn: Connection, timeout_s: float) -> None:
        raw: sqlite3.Connection = conn.connection.dbapi_connection  # type: ignore[assignment]
        deadline = time.monotonic() + timeout_s
        raw.set_authorizer(_sqlite_read_only_authorizer)
        raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2_000)

    def _after_execute(self, conn: Connection) -> None:
        raw: sqlite3.Connection = conn.connection.dbapi_connection  # type: ignore[assignment]
        raw.set_authorizer(None)
        raw.set_progress_handler(None, 0)


class PostgresAdapter(SQLAlchemyAdapter):
    dialect = "postgres"

    def _before_execute(self, conn: Connection, timeout_s: float) -> None:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")


class MySQLAdapter(SQLAlchemyAdapter):
    dialect = "mysql"

    def _before_execute(self, conn: Connection, timeout_s: float) -> None:
        conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_s * 1000)}")
        conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")


def mask_url(url: str | URL) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except (ArgumentError, ValueError):
        return "<invalid connection URL>"


def adapter_from_url(url: str, display_name: str | None = None) -> SQLAlchemyAdapter:
    """Build an adapter for a server database URL, e.g. postgresql+psycopg://user:pw@host/db."""
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        raise DataSourceError("Invalid connection URL. Expected e.g. postgresql+psycopg://user:password@host:5432/dbname") from None
    backend = parsed.get_backend_name()
    masked = mask_url(parsed)
    cls: type[SQLAlchemyAdapter]
    if backend == "postgresql":
        cls = PostgresAdapter
    elif backend in ("mysql", "mariadb"):
        cls = MySQLAdapter
    elif backend == "sqlite":
        raise DataSourceError("For SQLite, upload the .db file or give its local file path instead of a URL.")
    else:
        raise DataSourceError(f"Unsupported database type '{backend}'. Supported: SQLite, PostgreSQL, MySQL.")
    try:
        engine = create_engine(parsed, poolclass=NullPool, connect_args=_connect_args(backend))
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except NoSuchModuleError:
        raise DataSourceError(f"Driver for '{parsed.drivername}' is not installed on the server "
                              "(e.g. pip install 'psycopg[binary]' or pymysql).") from None
    except ModuleNotFoundError as e:
        raise DataSourceError(f"Database driver not installed: {e.name}. Install it in the backend venv.") from None
    except SQLAlchemyError as e:
        raise DataSourceError(f"Could not connect to {masked}: {_safe_db_error(e)}") from None
    return cls(engine, display_name or (parsed.database or backend), display_target=masked, count_rows=False)


def _connect_args(backend: str) -> dict[str, Any]:
    if backend == "postgresql":
        return {"connect_timeout": 10}
    if backend in ("mysql", "mariadb"):
        return {"connect_timeout": 10}
    return {}
