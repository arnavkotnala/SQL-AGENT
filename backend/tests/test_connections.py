"""User-provided data sources: valid files, invalid connections, unsafe inputs."""
import sqlite3

import pytest

from app.datasources.base import DataSourceError
from app.datasources.registry import SourceRegistry, normalize_user_path, safe_filename
from app.datasources.sql_adapter import adapter_from_url, mask_url

from .conftest import make_student_workbook


@pytest.fixture
def reg(settings):
    return SourceRegistry(settings)


def test_user_sqlite_file_by_path_and_quotes(reg, tmp_path):
    db = tmp_path / "My Data.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute("INSERT INTO items VALUES (1, 'pen')")
    e = reg.add_local_path(f'"{db}"')  # Windows "Copy as path" adds quotes
    schema = e.adapter.inspect_schema()
    assert [t.name for t in schema.tables] == ["items"]
    assert e.to_dict()["editable"] is False


def test_excel_and_csv_sources(reg, tmp_path):
    x = reg.add_local_path(str(make_student_workbook(tmp_path / "s.xlsx")))
    assert x.kind == "excel" and x.to_dict()["editable"] is True
    assert {t.name for t in x.adapter.inspect_schema().tables} == {"students", "notes"}
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("Region,Amount,Date\nNorth,10.5,2026-01-02\nSouth,7,2026-01-03\n", encoding="utf-8")
    c = reg.add_local_path(csv_path.as_uri())  # file:/// links work too
    assert c.kind == "csv" and c.to_dict()["editable"] is False
    r = c.adapter.execute_read("SELECT region, amount FROM sales ORDER BY amount", {}, 10, 5)
    assert r.rows == [("South", 7.0), ("North", 10.5)]


@pytest.mark.parametrize("value,msg", [
    ("C:/definitely/not/here.xlsx", "not found"),
    ("https://onedrive.live.com/edit?id=123", "OneDrive"),
    ("https://example.com/data.xlsx", "Web links"),
    ("", "enter a file path"),
])
def test_invalid_paths(reg, value, msg):
    with pytest.raises(DataSourceError, match=msg):
        reg.add_local_path(value)


def test_unsupported_and_corrupt_files(reg, tmp_path):
    (tmp_path / "a.xls").write_bytes(b"old")
    with pytest.raises(DataSourceError, match="Save As"):
        reg.add_local_path(str(tmp_path / "a.xls"))
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(DataSourceError, match="Unsupported file type"):
        reg.add_local_path(str(tmp_path / "a.txt"))
    (tmp_path / "fake.db").write_text("this is not sqlite")
    with pytest.raises(DataSourceError, match="Not a readable SQLite"):
        reg.add_local_path(str(tmp_path / "fake.db"))
    (tmp_path / "fake.xlsx").write_text("not a zip")
    with pytest.raises(DataSourceError, match="Could not open Excel"):
        reg.add_local_path(str(tmp_path / "fake.xlsx"))


def test_local_paths_can_be_disabled_for_hosted_deployments(settings):
    settings.allow_local_file_paths = False
    with pytest.raises(DataSourceError, match="disabled"):
        SourceRegistry(settings).add_local_path("C:/x.xlsx")


def test_server_urls_never_leak_passwords():
    with pytest.raises(DataSourceError) as ei:
        adapter_from_url("postgresql+psycopg://admin:SuperSecret123@127.0.0.1:1/db")
    assert "SuperSecret123" not in str(ei.value)
    assert "SuperSecret123" not in mask_url("mysql+pymysql://u:SuperSecret123@h/db")
    with pytest.raises(DataSourceError, match="Unsupported database type"):
        adapter_from_url("oracle://u:p@h/db")
    with pytest.raises(DataSourceError, match="Invalid connection URL"):
        adapter_from_url("not a url")


def test_upload_filename_is_sanitized_and_size_limited(reg, settings, tmp_path):
    assert safe_filename("..\\..\\windows\\evil.xlsx") == "evil.xlsx"
    assert safe_filename("../../etc/passwd") == "passwd"
    data = make_student_workbook(tmp_path / "u.xlsx").read_bytes()
    e = reg.add_upload("../../sneaky.xlsx", data)
    assert e.adapter.file_path.parent.parent == settings.uploads_dir.resolve()
    settings.max_upload_mb = 0
    with pytest.raises(DataSourceError, match="upload limit"):
        reg.add_upload("big.xlsx", data)


def test_normalize_user_path_handles_file_uri():
    assert str(normalize_user_path("file:///C:/Users/me/a%20b.xlsx")).replace("\\", "/") == "C:/Users/me/a b.xlsx"
