"""Adapter interface shared by every data source (SQLite, PostgreSQL, MySQL, Excel/CSV mirrors)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class DataSourceError(Exception):
    """Raised for connection/introspection failures. Messages must never contain secrets."""


class QueryExecutionError(Exception):
    """Raised when the database rejects or aborts a query. `recoverable` hints whether a retry may help."""

    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message)
        self.recoverable = recoverable


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    note: str | None = None  # e.g. "percentage stored as fraction 0-1"


@dataclass
class ForeignKeyInfo:
    columns: list[str]
    referred_table: str
    referred_columns: list[str]


@dataclass
class TableInfo:
    name: str
    kind: str  # "table" | "view" | "sheet"
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    row_count: int | None = None
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None


@dataclass
class SchemaInfo:
    dialect: str  # sqlglot dialect name
    tables: list[TableInfo]

    def table_map(self) -> dict[str, TableInfo]:
        return {t.name.lower(): t for t in self.tables}

    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {t.name: {c.name: (c.type or "TEXT") for c in t.columns} for t in self.tables}

    def to_prompt(self, max_sample_rows: int = 3) -> str:
        """Compact, model-friendly description of the schema."""
        lines: list[str] = [f"SQL dialect: {self.dialect}"]
        for t in self.tables:
            header = f"\n{t.kind.upper()} {t.name}"
            if t.row_count is not None:
                header += f" (~{t.row_count} rows)"
            if t.note:
                header += f" -- {t.note}"
            lines.append(header)
            for c in t.columns:
                bits = [c.name, c.type or "TEXT"]
                if c.primary_key:
                    bits.append("PRIMARY KEY")
                if not c.nullable:
                    bits.append("NOT NULL")
                if c.note:
                    bits.append(f"-- {c.note}")
                lines.append("  - " + " ".join(bits))
            for fk in t.foreign_keys:
                lines.append(
                    f"  FK ({', '.join(fk.columns)}) -> {fk.referred_table}({', '.join(fk.referred_columns)})"
                )
            if t.sample_rows and max_sample_rows:
                lines.append("  sample rows:")
                for r in t.sample_rows[:max_sample_rows]:
                    lines.append(f"    {r}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dialect": self.dialect,
            "tables": [
                {
                    "name": t.name,
                    "kind": t.kind,
                    "row_count": t.row_count,
                    "note": t.note,
                    "columns": [c.__dict__ for c in t.columns],
                    "foreign_keys": [fk.__dict__ for fk in t.foreign_keys],
                }
                for t in self.tables
            ],
        }


@dataclass
class QueryResult:
    columns: list[str]
    column_types: list[str | None]  # declared types where the driver knows them
    rows: list[tuple[Any, ...]]
    truncated: bool  # True when more rows existed than max_rows
    elapsed_ms: float


class DataSourceAdapter(ABC):
    """A connected, read-only data source."""

    dialect: str = "sqlite"
    editable: bool = False  # only spreadsheet mirrors support approved modifications

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Display-safe description (no passwords)."""

    @abstractmethod
    def inspect_schema(self) -> SchemaInfo: ...

    @abstractmethod
    def execute_read(self, sql: str, params: dict[str, Any], max_rows: int, timeout_s: float) -> QueryResult: ...

    def close(self) -> None:  # pragma: no cover - default no-op
        pass
