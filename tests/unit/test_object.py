import pytest

from app.data import AppDb, ObjectError, ProjectObject
from app.data import schema


def test_create_open_and_flag(tmp_path):
    f = tmp_path / "obj"
    o = ProjectObject.create(f, "Аэропорт")
    assert (f / "object.db").exists() and (f / "sources").is_dir() and (f / "reports").is_dir()
    o.set_only_local(True)
    o.close()
    o = ProjectObject.open(f)
    assert o.name == "Аэропорт" and o.only_local
    hist = o.con.execute("SELECT field, old, new FROM history").fetchall()
    assert [tuple(h) for h in hist] == [("only_local", "False", "True")]
    o.close()


def test_create_twice_refused(tmp_path):
    ProjectObject.create(tmp_path, "x").close()
    with pytest.raises(ObjectError):
        ProjectObject.create(tmp_path, "y")


def test_open_without_db(tmp_path):
    with pytest.raises(ObjectError):
        ProjectObject.open(tmp_path)


def test_duplicate_and_unsupported(tmp_path, sample_pdf):
    o = ProjectObject.create(tmp_path / "o", "o")
    r1, new1 = o.add_source(sample_pdf)
    r2, new2 = o.add_source(sample_pdf)
    assert new1 and not new2 and r1.id == r2.id
    bad = tmp_path / "a.txt"
    bad.write_text("x")
    with pytest.raises(ObjectError):
        o.add_source(bad)
    o.close()


def test_modified_source_detected(tmp_path, sample_pdf):
    o = ProjectObject.create(tmp_path / "o", "o")
    row, _ = o.add_source(sample_pdf)
    p = o.file_path(row)
    o.close()
    p.write_bytes(p.read_bytes() + b"\n%changed")
    o = ProjectObject.open(tmp_path / "o")
    assert o.files()[0].status == "изменён"
    o.close()


def test_newer_schema_refused(tmp_path):
    o = ProjectObject.create(tmp_path / "o", "o")
    with o.con:
        o.con.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    o.close()
    with pytest.raises(RuntimeError):
        ProjectObject.open(tmp_path / "o")


def test_appdb_recent(tmp_path):
    db = AppDb(tmp_path / "app.db")
    db.remember_object(tmp_path / "a", "A")
    db.remember_object(tmp_path / "a", "A2")
    assert [r["name"] for r in db.recent_objects()] == ["A2"]
    db.set("k", "v")
    assert db.get("k") == "v" and db.get("none", "d") == "d"
    assert schema.schema_version(db.con) == max(schema.APP_MIGRATIONS)
    db.close()
