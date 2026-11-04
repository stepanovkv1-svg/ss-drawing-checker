"""Конвейер проверок на тестовом наборе: все этапы получают статус, места со скриншотами,
повторный запуск даёт те же замечания (П1-30)."""
from app.data import ProjectObject
from app.documents.intake import add_files
from app.findings import store
from app.pipeline.run import last_run, run_checks


def test_run_checks_repeatable(tmp_path, sample_pdf, sample_xlsx):
    obj = ProjectObject.create(tmp_path / "о", "т")
    add_files(obj, [sample_pdf, sample_xlsx])
    steps = []
    run_checks(obj, progress=lambda n, t, name: steps.append(name))
    run, stages = last_run(obj.con)
    assert run["status"] == "готово" and len(stages) == 10 and len(steps) == 11
    assert all(s["status"] != "ошибка" for s in stages), [dict(s) for s in stages]
    first = [(f["fingerprint"], f["text"], f["cls"]) for f in store.findings(obj.con)]
    assert {f[0] for f in first} and any(t.startswith("Документ") for _, t, _ in first)
    shots = obj.con.execute("SELECT COUNT(*) FROM place WHERE page_id IS NOT NULL AND shot IS NULL").fetchone()[0]
    assert shots == 0
    run_checks(obj)
    assert [(f["fingerprint"], f["text"], f["cls"]) for f in store.findings(obj.con)] == first
    obj.close()


def test_run_checks_cancel(tmp_path, sample_pdf):
    obj = ProjectObject.create(tmp_path / "о", "т")
    add_files(obj, [sample_pdf])
    run_checks(obj, cancelled=lambda: True)
    run, _ = last_run(obj.con)
    assert run["status"] == "остановлено" and store.findings(obj.con) == []
    obj.close()
