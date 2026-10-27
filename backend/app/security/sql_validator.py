"""AST-based validation of model-generated SQL. The model's SQL is untrusted input.

Read mode (all databases): exactly one SELECT / set-operation query, only known tables and columns,
no DDL/DML/commands, no dangerous functions, named parameters only.

Write mode (spreadsheet mirrors only): exactly one UPDATE / INSERT / DELETE against an editable sheet
table. The statement is never run against the user's file directly: it is dry-run on a mirror inside a
transaction that is rolled back, and the resulting diff is shown for approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from ..datasources.base import SchemaInfo


def _node_types(*names: str) -> tuple[type, ...]:
    """Resolve sqlglot expression classes by name, tolerating renames across sqlglot versions."""
    return tuple(getattr(exp, n) for n in names if isinstance(getattr(exp, n, None), type))


_ALWAYS_FORBIDDEN = _node_types(
    "Drop", "Alter", "AlterTable", "Create", "Command", "Pragma", "Attach", "Detach", "Merge",
    "TruncateTable", "Transaction", "Commit", "Rollback", "Set", "Use", "Copy", "LoadData", "Grant",
    "Revoke", "Analyze", "Into", "Lock", "Describe", "Show", "Cache", "Uncache", "Refresh",
)
_WRITE_NODES = _node_types("Insert", "Update", "Delete")
_READ_ROOTS = _node_types("Select", "SetOperation", "Union", "Intersect", "Except")

DENIED_FUNCTIONS = {
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "zipfile",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_sleep", "pg_terminate_backend",
    "pg_cancel_backend", "lo_import", "lo_export", "dblink", "dblink_exec", "set_config", "current_setting",
    "sleep", "benchmark", "load_file", "sys_exec", "sys_eval", "get_lock", "xp_cmdshell", "openrowset",
}
ROWID_NAMES = {"rowid", "_rowid_", "oid"}


@dataclass
class ValidationResult:
    ok: bool
    statement_type: str | None = None  # "select" | "update" | "insert" | "delete"
    sql: str | None = None  # normalized SQL that will actually be executed
    tables: list[str] = field(default_factory=list)
    target_table: str | None = None
    target_columns: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    security_violation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _fail(msg: str, security: bool = False, **kw: Any) -> ValidationResult:
    return ValidationResult(ok=False, errors=[msg], security_violation=security, **kw)


def _parse_single(sql: str, dialect: str) -> exp.Expression | ValidationResult:
    if not sql or not sql.strip():
        return _fail("Empty SQL.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        return _fail(f"SQL syntax error: {_first_line(str(e))}")
    except Exception as e:  # tokenizer errors etc.
        return _fail(f"SQL could not be parsed: {_first_line(str(e))}")
    if len(statements) != 1:
        return _fail(f"Exactly one SQL statement is allowed; found {len(statements)}.", security=len(statements) > 1)
    return statements[0]


def _first_line(s: str) -> str:
    return s.strip().splitlines()[0][:300] if s.strip() else s


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        return node.sql_name().lower()
    except Exception:
        return type(node).__name__.lower()


def _common_checks(root: exp.Expression, schema: SchemaInfo, result: ValidationResult) -> None:
    """Forbidden nodes, functions, table existence, rowid usage, placeholders. Mutates `result`."""
    for node in root.walk():
        if _ALWAYS_FORBIDDEN and isinstance(node, _ALWAYS_FORBIDDEN):
            result.errors.append(f"Forbidden SQL operation: {type(node).__name__.upper()}.")
            result.security_violation = True
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in DENIED_FUNCTIONS:
                result.errors.append(f"Function '{name}' is not allowed.")
                result.security_violation = True

    known = schema.table_map()
    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    tables: list[str] = []
    for t in root.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            result.errors.append("Table-valued functions are not allowed in FROM clauses.")
            result.security_violation = True
            continue
        name = t.name.lower()
        if name in cte_names:
            continue
        db = (t.args.get("db").name.lower() if t.args.get("db") else "")
        if db and schema.dialect == "sqlite" and db not in ("main",):
            result.errors.append(f"Access to attached/other database '{db}' is not allowed.")
            result.security_violation = True
            continue
        if name not in known:
            result.errors.append(
                f"Unknown table '{t.name}'. Available tables: {', '.join(sorted(x.name for x in schema.tables))}."
            )
            continue
        if known[name].name not in tables:
            tables.append(known[name].name)
    result.tables = tables

    for col in root.find_all(exp.Column):
        if col.name.lower() in ROWID_NAMES and col.name.lower() not in _all_columns(schema):
            result.errors.append("Referencing internal row identifiers (rowid/oid) is not allowed.")
            result.security_violation = True

    names: list[str] = []
    for p in root.find_all(exp.Placeholder):
        if not p.name or p.name == "?":
            result.errors.append("Use named parameters (e.g. :threshold), not positional '?' placeholders.")
        elif p.name not in names:
            names.append(p.name)
    result.placeholders = names


def _all_columns(schema: SchemaInfo) -> set[str]:
    return {c.name.lower() for t in schema.tables for c in t.columns}


def _verify_columns(query: exp.Expression, schema: SchemaInfo, result: ValidationResult) -> None:
    try:
        qualify(query.copy(), schema=schema.sqlglot_schema(), dialect=schema.dialect,
                validate_qualify_columns=True, quote_identifiers=False)
    except OptimizeError as e:
        result.errors.append(f"Invalid column reference: {_first_line(str(e))}")
    except Exception as e:  # qualifier limitation, not a user error: the DB will still verify at execution
        result.warnings.append(f"Column verification skipped ({type(e).__name__}).")


def _check_params(result: ValidationResult, params: dict[str, Any] | None) -> None:
    params = params or {}
    missing = [p for p in result.placeholders if p not in params]
    if missing:
        result.errors.append(f"Missing values for SQL parameters: {', '.join(':' + m for m in missing)}.")
    extra = [k for k in params if k not in result.placeholders]
    if extra:
        result.warnings.append(f"Ignoring unused parameters: {', '.join(extra)}.")


def validate_read_query(sql: str, schema: SchemaInfo, params: dict[str, Any] | None = None) -> ValidationResult:
    parsed = _parse_single(sql, schema.dialect)
    if isinstance(parsed, ValidationResult):
        return parsed
    root = parsed
    if isinstance(root, _WRITE_NODES) or (_ALWAYS_FORBIDDEN and isinstance(root, _ALWAYS_FORBIDDEN)):
        return _fail(f"Only read-only SELECT queries are permitted; got {type(root).__name__.upper()}.",
                     security=True)
    if not isinstance(root, _READ_ROOTS):
        return _fail(f"Only SELECT queries are permitted; got {type(root).__name__}.", security=True)

    result = ValidationResult(ok=False, statement_type="select")
    _common_checks(root, schema, result)
    for node in root.walk():
        if isinstance(node, _WRITE_NODES):
            result.errors.append(f"Data modification ({type(node).__name__.upper()}) is not permitted.")
            result.security_violation = True
    if not result.errors:
        _verify_columns(root, schema, result)
    _check_params(result, params)
    result.sql = root.sql(dialect=schema.dialect, comments=False)
    result.ok = not result.errors
    return result


def validate_write_statement(
    sql: str,
    schema: SchemaInfo,
    editable_tables: set[str],
    read_only_columns: dict[str, set[str]] | None = None,
    params: dict[str, Any] | None = None,
) -> ValidationResult:
    """Validate an UPDATE/INSERT/DELETE intended for a spreadsheet mirror (see module docstring)."""
    parsed = _parse_single(sql, schema.dialect)
    if isinstance(parsed, ValidationResult):
        return parsed
    root = parsed
    if not isinstance(root, _WRITE_NODES):
        if isinstance(root, _READ_ROOTS):
            return _fail("A modification statement (UPDATE, INSERT or DELETE) was expected for this request.")
        return _fail(f"Statement type {type(root).__name__.upper()} is not permitted.", security=True)

    kind = type(root).__name__.lower()
    result = ValidationResult(ok=False, statement_type=kind)
    _common_checks(root, schema, result)
    for bad in ("returning", "conflict", "from", "joins"):
        if root.args.get(bad):
            result.errors.append(f"{bad.upper()} clauses are not supported in modifications; use a WHERE subquery.")

    target_expr = root.this.this if isinstance(root.this, exp.Schema) else root.this
    if not isinstance(target_expr, exp.Table):
        return _fail("Could not determine the target table of the modification.")
    known = schema.table_map()
    target = known.get(target_expr.name.lower())
    editable = {t.lower() for t in editable_tables}
    if target is None or target.name.lower() not in editable:
        result.errors.append(f"Table '{target_expr.name}' cannot be modified. Editable: {', '.join(sorted(editable_tables))}.")
        result.security_violation = target is not None
        result.sql = root.sql(dialect=schema.dialect, comments=False)
        return result
    result.target_table = target.name
    target_cols = {c.name.lower(): c.name for c in target.columns}
    locked = {c.lower() for c in (read_only_columns or {}).get(target.name, set())}

    def check_target_column(name: str) -> None:
        if name.lower() in ROWID_NAMES:
            result.errors.append("Internal row identifiers cannot be modified.")
            result.security_violation = True
        elif name.lower() not in target_cols:
            result.errors.append(f"Unknown column '{name}' in table '{target.name}'. "
                                 f"Columns: {', '.join(target_cols.values())}.")
        elif name.lower() in locked:
            result.errors.append(f"Column '{target_cols[name.lower()]}' contains spreadsheet formulas and cannot be overwritten.")
        else:
            result.target_columns.append(target_cols[name.lower()])

    probe: exp.Expression | None = None
    if isinstance(root, exp.Update):
        assignments = root.args.get("expressions") or []
        rhs: list[exp.Expression] = []
        for a in assignments:
            if not isinstance(a, exp.EQ) or not isinstance(a.this, exp.Column):
                result.errors.append("Unsupported SET clause.")
                continue
            check_target_column(a.this.name)
            rhs.append(a.expression.copy())
        probe = exp.select(*(rhs or [exp.Literal.number(1)])).from_(target_expr.copy())
        if root.args.get("where"):
            probe = probe.where(root.args["where"].this.copy())
    elif isinstance(root, exp.Delete):
        probe = exp.select(exp.Literal.number(1)).from_(target_expr.copy())
        if root.args.get("where"):
            probe = probe.where(root.args["where"].this.copy())
        else:
            result.warnings.append("DELETE without WHERE removes every data row of the sheet.")
    elif isinstance(root, exp.Insert):
        if not isinstance(root.this, exp.Schema) or not root.this.expressions:
            result.errors.append("INSERT must list target columns explicitly, e.g. INSERT INTO t (a, b) VALUES (...).")
        else:
            for ident in root.this.expressions:
                check_target_column(ident.name)
        source = root.expression
        if isinstance(source, _READ_ROOTS):
            probe = source.copy()
    if probe is not None and not result.errors:
        _verify_columns(probe, schema, result)
    _check_params(result, params)
    result.sql = root.sql(dialect=schema.dialect, comments=False)
    result.ok = not result.errors
    return result


def normalize_for_dedupe(sql: str, dialect: str, params: dict[str, Any] | None) -> str:
    """Canonical form used to detect repeated identical attempts."""
    try:
        canon = sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect, comments=False, normalize=True,
                                                         pretty=False)
    except Exception:
        canon = " ".join(sql.lower().split())
    return canon + "|" + repr(sorted((params or {}).items()))
