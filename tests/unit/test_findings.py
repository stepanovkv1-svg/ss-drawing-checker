"""Хранение замечаний: повторная проверка, «требуется пересмотр», ручные правки, разделение и объединение (ФТ-24, П1-13)."""
from app.data import ProjectObject
from app.findings import store
from app.findings.model import CONFIRMED, POSSIBLE, REVIEW, Draft, PlaceDraft


def d(key, text="т", cls=CONFIRMED, n=2):
    return Draft("БП-03", key, 1, cls, text, "осн", "док",
                 [PlaceDraft(None, "А.pdf", k + 1, str(k + 1), (1, 2, 3, 4)) for k in range(n)])


def obj(tmp_path):
    return ProjectObject.create(tmp_path / "о", "т")


def run(con, drafts):
    rid = con.execute("INSERT INTO check_run(started_at, status, versions) VALUES ('t','готово','{}')").lastrowid
    return store.apply_drafts(con, rid, drafts)


def test_repeat_run_keeps_same_findings(tmp_path):
    con = obj(tmp_path).con
    assert run(con, [d("a"), d("b"), d("a")])["new"] == 2          # одна причина — одно замечание
    ids = [f["id"] for f in store.findings(con)]
    st = run(con, [d("a", text="новый"), d("b")])
    assert st == {"new": 0, "updated": 2, "kept": 0, "removed": 0, "review": 0}
    assert [f["id"] for f in store.findings(con)] == ids
    assert store.findings(con)[0]["text"] == "новый"


def test_user_work_survives_and_gets_review(tmp_path):
    con = obj(tmp_path).con
    run(con, [d("a"), d("b"), d("c")])
    fa, fb, fc = store.findings(con)
    store.update_finding(con, fa["id"], text="моя формулировка", cls=POSSIBLE)
    store.update_finding(con, fb["id"], decision="принято")
    st = run(con, [d("a", text="авто")])
    assert st["kept"] == 1 and st["review"] == 1 and st["removed"] == 1
    rows = {f["id"]: f for f in store.findings(con)}
    assert rows[fa["id"]]["text"] == "моя формулировка" and rows[fa["id"]]["cls"] == POSSIBLE
    assert rows[fb["id"]]["review"] == REVIEW and fc["id"] not in rows
    store.update_finding(con, fb["id"], decision="отклонено")       # решение снимает пометку
    assert store.findings(con)[1]["review"] == ""
    hist = con.execute("SELECT field FROM history WHERE entity='finding'").fetchall()
    assert {"text", "cls", "decision", "review"} <= {h[0] for h in hist}


def test_manual_place_not_overwritten(tmp_path):
    con = obj(tmp_path).con
    run(con, [d("a")])
    f = store.findings(con)[0]
    p = store.places(con, f["id"])[0]
    store.set_place_rect(con, p["id"], (5, 6, 7, 8), b"png")
    run(con, [d("a", n=3)])
    ps = store.places(con, f["id"])
    assert len(ps) == 2 and (ps[0]["x0"], ps[0]["method"], ps[0]["shot"]) == (5, "вручную", b"png")


def test_split_and_merge(tmp_path):
    con = obj(tmp_path).con
    run(con, [d("a", n=3), d("b", n=1)])
    fa, fb = store.findings(con)
    pa = [p["id"] for p in store.places(con, fa["id"])]
    assert store.split_places(con, fa["id"], pa) is None             # все места не переносятся
    new = store.split_places(con, fa["id"], pa[1:])
    assert len(store.places(con, fa["id"])) == 1 and len(store.places(con, new)) == 2
    keep = store.merge_findings(con, [fb["id"], new])
    assert keep == fb["id"] and len(store.places(con, keep)) == 3
    assert len(store.findings(con)) == 2
    # разделённое и объединённое переживает повторную проверку
    run(con, [d("a", n=3), d("b", n=1)])
    assert len(store.findings(con)) == 2
    store.delete_place(con, store.places(con, keep)[0]["id"])
    assert len(store.places(con, keep)) == 2


def test_split_part_alive_while_source_found(tmp_path):
    con = obj(tmp_path).con
    run(con, [d("a", n=3)])
    fa = store.findings(con)[0]
    part = store.split_places(con, fa["id"], [store.places(con, fa["id"])[0]["id"]])
    st = run(con, [d("a", n=3)])
    rows = {f["id"]: f for f in store.findings(con)}
    assert st["review"] == 0 and rows[part]["review"] == ""
    assert len(store.places(con, fa["id"])) == 2 and len(store.places(con, part)) == 1   # без дублей мест
    st = run(con, [])                                                # причина ушла — обе части на пересмотр
    assert st["review"] == 2


def test_merged_finding_not_recreated(tmp_path):
    con = obj(tmp_path).con
    run(con, [d("a"), d("b")])
    fa, fb = store.findings(con)
    store.merge_findings(con, [fa["id"], fb["id"]])
    st = run(con, [d("a"), d("b")])
    assert st["new"] == 0 and st["review"] == 0 and len(store.findings(con)) == 1
    assert len(store.places(con, fa["id"])) == 4
