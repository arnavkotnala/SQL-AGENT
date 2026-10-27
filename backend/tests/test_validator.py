"""SQL security: the validator must reject anything other than a single safe read (or, for spreadsheets,
a single scoped write) - parsed with an SQL parser, not regexes."""
import pytest

from app.datasources.base import ColumnInfo, SchemaInfo, TableInfo
from app.security.sql_validator import normalize_for_dedupe, validate_read_query, validate_write_statement

SCHEMA = SchemaInfo("sqlite", [
    TableInfo("students", "table", [ColumnInfo("student_id", "INTEGER"), ColumnInfo("name", "TEXT"),
                                    ColumnInfo("course_id", "INTEGER")]),
    TableInfo("attendance", "table", [ColumnInfo("student_id", "INTEGER"), ColumnInfo("pct", "REAL")]),
])


@pytest.mark.parametrize("sql", [
    "SELECT name FROM students WHERE student_id = :sid",
    "WITH low AS (SELECT student_id, AVG(pct) AS p FROM attendance GROUP BY student_id) "
    "SELECT s.name, low.p FROM students s JOIN low ON low.student_id = s.student_id WHERE low.p < :t ORDER BY low.p",
    "SELECT name AS n FROM students ORDER BY n",
    "SELECT COUNT(*) AS total FROM students",
    "SELECT name FROM students UNION SELECT CAST(pct AS TEXT) FROM attendance",
    "SELECT name, RANK() OVER (PARTITION BY course_id ORDER BY student_id) AS r FROM students",
    "SELECT NAME FROM STUDENTS",
])
def test_valid_reads_pass(sql):
    r = validate_read_query(sql, SCHEMA, {"sid": 1, "t": 50})
    assert r.ok, r.errors
    assert r.sql and r.statement_type == "select"


@pytest.mark.parametrize("sql", [
    "DELETE FROM students",
    "UPDATE students SET name = 'x'",
    "INSERT INTO students (name) VALUES ('x')",
    "DROP TABLE students",
    "ALTER TABLE students ADD COLUMN x INT",
    "TRUNCATE TABLE students",
    "CREATE TABLE x (a INT)",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'other.db' AS other",
    "SELECT * FROM students; DROP TABLE students",
    "SELECT 1; SELECT 2",
    "SELECT load_extension('evil')",
    "SELECT name INTO copy_table FROM students",
    "SELECT * FROM other.students",
    "SELECT rowid FROM students",
    "SELECT * FROM pragma_table_info('students')",
])
def test_destructive_or_unsafe_sql_is_a_security_violation(sql):
    r = validate_read_query(sql, SCHEMA)
    assert not r.ok
    assert r.security_violation, (sql, r.errors)


@pytest.mark.parametrize("sql,fragment", [
    ("SELECT nme FROM students", "nme"),
    ("SELECT * FROM ghosts", "Unknown table 'ghosts'"),
    ("SELECT * FROM sqlite_master", "Unknown table"),
    ("SELEC name FROM students", "syntax"),
    ("SELECT name FROM students WHERE name = ?", "named parameters"),
    ("SELECT name FROM students WHERE student_id = :missing", "Missing values"),
])
def test_recoverable_errors_are_reported(sql, fragment):
    r = validate_read_query(sql, SCHEMA, {})
    assert not r.ok and not r.security_violation
    assert fragment.lower() in " ".join(r.errors).lower()


def test_comments_are_stripped_from_executed_sql():
    r = validate_read_query("SELECT name FROM students -- ; DROP TABLE students", SCHEMA)
    assert r.ok
    assert "DROP" not in r.sql and "--" not in r.sql


def test_write_mode_scoped_to_editable_table_and_columns():
    ok = validate_write_statement("UPDATE students SET name = :n WHERE student_id IN (SELECT student_id FROM attendance WHERE pct < 50)",
                                  SCHEMA, {"students"}, {}, {"n": "x"})
    assert ok.ok and ok.target_table == "students" and ok.target_columns == ["name"]
    assert validate_write_statement("UPDATE attendance SET pct = 0", SCHEMA, {"students"}).security_violation
    assert not validate_write_statement("UPDATE students SET nope = 1", SCHEMA, {"students"}).ok
    assert "formula" in validate_write_statement("UPDATE students SET name = 'a'", SCHEMA, {"students"},
                                                 {"students": {"name"}}).errors[0]
    assert validate_write_statement("DROP TABLE students", SCHEMA, {"students"}).security_violation
    assert validate_write_statement("UPDATE students SET rowid = 1", SCHEMA, {"students"}).security_violation
    assert not validate_write_statement("INSERT INTO students VALUES (1, 'a', 2)", SCHEMA, {"students"}).ok
    assert not validate_write_statement("SELECT * FROM students", SCHEMA, {"students"}).ok


def test_dedupe_normalization_ignores_formatting():
    a = normalize_for_dedupe("select name from students where student_id = :x", "sqlite", {"x": 1})
    b = normalize_for_dedupe("SELECT  name\nFROM students WHERE student_id = :x -- hi", "sqlite", {"x": 1})
    c = normalize_for_dedupe("SELECT name FROM students WHERE student_id = :x", "sqlite", {"x": 2})
    assert a == b and a != c
