"""Destructive SQL and injection attempts through the full agent: nothing harmful may execute."""
import sqlite3

import pytest

from app.agent.run import RunStatus

from .conftest import FakeLLM, goal, proposal


def _student_count(h):
    with sqlite3.connect(h.settings.samples_dir / "education.db") as c:
        return c.execute("SELECT COUNT(*) FROM students").fetchone()[0]


@pytest.mark.parametrize("sql", [
    "DELETE FROM students",
    "DROP TABLE students",
    "UPDATE students SET first_name = 'x'",
    "INSERT INTO courses (course_id, course_code, course_name, department, duration_years) VALUES (9,'a','b','c',1)",
    "ALTER TABLE students ADD COLUMN pwned TEXT",
    "SELECT * FROM students; DELETE FROM students",
    "ATTACH DATABASE 'x.db' AS x",
])
def test_destructive_sql_is_blocked_and_never_executed(make_harness, sql):
    llm = FakeLLM({"interpret": [goal()], "generate_sql": [proposal(sql)]})
    h = make_harness(llm)
    before = _student_count(h)
    run = h.run("Show students", h.source())
    assert run.status == RunStatus.BLOCKED, (sql, run.errors)
    assert "security" in run.errors[-1].lower()
    assert run.rows == [] and run.exports == []
    assert llm.prompts("evaluate") == []  # never reached execution
    assert _student_count(h) == before


def test_injection_in_user_text_is_bound_as_a_parameter(make_harness):
    evil = "x'; DROP TABLE students; --"
    sql = "SELECT roll_number, first_name FROM students WHERE first_name = :name"
    llm = FakeLLM({"interpret": [goal()], "generate_sql": [proposal(sql, {"name": evil})]})
    h = make_harness(llm)
    run = h.run(f"Find the student named {evil}", h.source())
    assert run.status == RunStatus.COMPLETED
    assert run.rows == []  # treated as a literal value
    assert _student_count(h) == 150
    # the request reaches the model only inside a quoted data block
    assert "<<<\nFind the student named x'; DROP TABLE students; --\n>>>" in llm.prompts("interpret")[0]


def test_injection_inlined_into_sql_is_rejected(make_harness):
    sql = "SELECT * FROM students WHERE first_name = 'x'; DROP TABLE students; --'"
    h = make_harness(FakeLLM({"interpret": [goal()], "generate_sql": [proposal(sql)]}))
    run = h.run("Find x'; DROP TABLE students; --", h.source())
    assert run.status == RunStatus.BLOCKED
    assert _student_count(h) == 150


def test_union_based_exfiltration_of_internal_tables_is_rejected(make_harness):
    sql = "SELECT first_name FROM students UNION SELECT sql FROM sqlite_master"
    llm = FakeLLM({"interpret": [goal()], "generate_sql": [proposal(sql)] * 5})
    h = make_harness(llm)
    run = h.run("names", h.source())
    assert run.status == RunStatus.FAILED  # recoverable (unknown table) but never succeeds
    assert all("sqlite_master" in a["error"] or a["outcome"] == "duplicate" for a in run.attempts)
    assert run.rows == []


def test_database_layer_blocks_writes_even_if_validator_were_bypassed(make_harness):
    h = make_harness(FakeLLM())
    adapter = h.registry.get(h.source()).adapter
    from app.datasources.base import QueryExecutionError
    with pytest.raises(QueryExecutionError):
        adapter.execute_read("DELETE FROM students", {}, 10, 5)
    with pytest.raises(QueryExecutionError):
        adapter.execute_read("SELECT load_extension('x')", {}, 10, 5)
    with pytest.raises(QueryExecutionError, match="timeout"):
        adapter.execute_read("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
                             "SELECT COUNT(*) FROM c", {}, 10, 0.3)
    assert _student_count(h) == 150
