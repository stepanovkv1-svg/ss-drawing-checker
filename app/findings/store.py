"""Хранение замечаний в базе объекта.

Повторная проверка узнаёт замечание по отпечатку (правило + причина):
- не тронутое пользователем — обновляется (текст, класс, места);
- исправленное или рассмотренное пользователем — сохраняется как есть, места обновляются;
- больше не находится: нетронутое удаляется, рассмотренное или правленое получает
  «требуется пересмотр» и не удаляется (Р61, П1-13).
"""
from __future__ import annotations

import json
import sqlite3

from ..data import schema
from .model import REVIEW, Draft


def findings(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM finding ORDER BY grp, rule, id").fetchall()


def places(con: sqlite3.Connection, finding_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM place WHERE finding_id=? ORDER BY ord, id", (finding_id,)).fetchall()


def _history(con, fid, field, old, new):
    con.execute("INSERT INTO history(ts, entity, entity_id, field, old, new) VALUES (?,?,?,?,?,?)",
                (schema.now(), "finding", fid, field, None if old is None else str(old), None if new is None else str(new)))


def _insert_places(con, fid: int, d: Draft) -> None:
    for k, p in enumerate(d.places):
        r = p.rect or (None, None, None, None)
        con.execute("INSERT INTO place(finding_id, page_id, file_name, page_no, sheet, x0, y0, x1, y1, method, cell, ord)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, p.page_id, p.file_name, p.page_no, p.sheet, *r, p.method, p.cell, k))


PART = "/часть-"                     # отпечаток части, выделенной из замечания: «<исходный>/часть-…»
PLACE_EDITS = ("место", "место удалено", "разделение", "объединение")


def _places_edited(con, fid: int) -> bool:
    """Пользователь менял состав или рамки мест — повторная проверка их не перезаписывает."""
    q = "SELECT 1 FROM history WHERE entity='finding' AND entity_id=? AND field IN (%s) LIMIT 1" % ",".join("?" * len(PLACE_EDITS))
    return bool(con.execute(q, (fid, *PLACE_EDITS)).fetchone()
                or con.execute("SELECT 1 FROM place WHERE finding_id=? AND method='вручную' LIMIT 1", (fid,)).fetchone())


def _absorbed_by(con, fp: str) -> int | None:
    for r in con.execute("SELECT entity_id, new FROM history WHERE entity='finding' AND field='объединение' "
                         "AND new LIKE ? ORDER BY id DESC", (f"%{fp}%",)):
        if con.execute("SELECT 1 FROM finding WHERE id=?", (r[0],)).fetchone():
            return r[0]
    return None


def apply_drafts(con: sqlite3.Connection, run_id: int, drafts: list[Draft]) -> dict[str, int]:
    stats = {"new": 0, "updated": 0, "kept": 0, "removed": 0, "review": 0}
    now = schema.now()
    seen = set()
    with con:
        for d in drafts:
            fp = d.fingerprint
            if fp in seen:          # одна причина — одно замечание: места объединяются у правила заранее
                continue
            seen.add(fp)
            old = con.execute("SELECT * FROM finding WHERE fingerprint=?", (fp,)).fetchone()
            host = None if old else _absorbed_by(con, fp)
            if host:                    # причина вошла в объединённое замечание — оно и живёт дальше
                con.execute("UPDATE finding SET run_id=?, review='' WHERE id=?", (run_id, host))
                stats["kept"] += 1
                continue
            if not old:
                cur = con.execute(
                    "INSERT INTO finding(fingerprint, rule, grp, cls, text, basis, proof, run_id, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)", (fp, d.rule, d.grp, d.cls, d.text, d.basis, d.proof, run_id, now, now))
                _insert_places(con, cur.lastrowid, d)
                stats["new"] += 1
                continue
            fid = old["id"]
            manual_places = _places_edited(con, fid)
            if old["edited"] or old["decision"] != "не рассмотрено":
                con.execute("UPDATE finding SET run_id=?, review='', proof=? WHERE id=?", (run_id, d.proof, fid))
                stats["kept"] += 1
            else:
                con.execute("UPDATE finding SET cls=?, text=?, basis=?, proof=?, run_id=?, review='', updated_at=? "
                            "WHERE id=?", (d.cls, d.text, d.basis, d.proof, run_id, now, fid))
                stats["updated"] += 1
            if not manual_places:       # места, поправленные пользователем, не перезаписываются
                con.execute("DELETE FROM place WHERE finding_id=?", (fid,))
                _insert_places(con, fid, d)
        for old in con.execute("SELECT * FROM finding WHERE run_id IS NOT ? OR run_id IS NULL", (run_id,)).fetchall():
            if old["fingerprint"].split(PART)[0] in seen or old["rule"].startswith("ручное"):
                continue
            if old["edited"] or old["decision"] != "не рассмотрено":
                if old["review"] != REVIEW:
                    con.execute("UPDATE finding SET review=? WHERE id=?", (REVIEW, old["id"]))
                    _history(con, old["id"], "review", old["review"], REVIEW)
                stats["review"] += 1
            else:
                con.execute("DELETE FROM finding WHERE id=?", (old["id"],))
                stats["removed"] += 1
    return stats


EDITABLE = {"text", "cls", "severity", "decision", "proof"}


def update_finding(con: sqlite3.Connection, fid: int, **values) -> None:
    """Правка пользователем: текст, класс, критичность, решение. Всё пишется в историю."""
    old = con.execute("SELECT * FROM finding WHERE id=?", (fid,)).fetchone()
    if not old:
        return
    with con:
        for k, v in values.items():
            if k not in EDITABLE or old[k] == v:
                continue
            con.execute(f"UPDATE finding SET {k}=?, updated_at=? WHERE id=?", (v, schema.now(), fid))
            _history(con, fid, k, old[k], v)
            if k != "decision":
                con.execute("UPDATE finding SET edited=1 WHERE id=?", (fid,))
        if "decision" in values and old["review"]:
            con.execute("UPDATE finding SET review='' WHERE id=?", (fid,))


def split_places(con: sqlite3.Connection, fid: int, place_ids: list[int]) -> int | None:
    """Перенос выбранных мест в новое замечание (ФТ-24). Места не теряются."""
    old = con.execute("SELECT * FROM finding WHERE id=?", (fid,)).fetchone()
    total = con.execute("SELECT COUNT(*) FROM place WHERE finding_id=?", (fid,)).fetchone()[0]
    if not old or not place_ids or len(place_ids) >= total:
        return None
    now = schema.now()
    with con:
        cur = con.execute(
            "INSERT INTO finding(fingerprint, rule, grp, cls, severity, text, basis, proof, decision, edited, run_id,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
            (old["fingerprint"] + f"{PART}{now}-{place_ids[0]}", old["rule"], old["grp"], old["cls"], old["severity"],
             old["text"], old["basis"], old["proof"], old["decision"], old["run_id"], now, now))
        new_id = cur.lastrowid
        con.executemany("UPDATE place SET finding_id=? WHERE id=?", [(new_id, p) for p in place_ids])
        con.execute("UPDATE finding SET edited=1 WHERE id=?", (fid,))
        _history(con, fid, "разделение", None, json.dumps({"новое": new_id, "места": place_ids}))
        _history(con, new_id, "разделение", fid, json.dumps(place_ids))
    return new_id


def merge_findings(con: sqlite3.Connection, ids: list[int]) -> int | None:
    """Объединение замечаний: все места переходят в первое, остальные удаляются (ФТ-24)."""
    ids = sorted(set(ids))
    if len(ids) < 2:
        return None
    keep, rest = ids[0], ids[1:]
    fps = [r[0] for r in con.execute("SELECT fingerprint FROM finding WHERE id IN (%s)" % ",".join("?" * len(rest)), rest)]
    with con:
        con.executemany("UPDATE place SET finding_id=? WHERE finding_id=?", [(keep, r) for r in rest])
        con.execute("UPDATE finding SET edited=1 WHERE id=?", (keep,))
        _history(con, keep, "объединение", None, json.dumps({"замечания": rest, "отпечатки": fps}, ensure_ascii=False))
        con.executemany("DELETE FROM finding WHERE id=?", [(r,) for r in rest])
    return keep


def set_place_rect(con: sqlite3.Connection, place_id: int, rect, shot: bytes | None) -> None:
    with con:
        con.execute("UPDATE place SET x0=?, y0=?, x1=?, y1=?, method='вручную', shot=? WHERE id=?", (*rect, shot, place_id))
        fid = con.execute("SELECT finding_id FROM place WHERE id=?", (place_id,)).fetchone()[0]
        con.execute("UPDATE finding SET edited=1 WHERE id=?", (fid,))
        _history(con, fid, "место", place_id, json.dumps(rect))


def delete_place(con: sqlite3.Connection, place_id: int) -> None:
    with con:
        r = con.execute("SELECT finding_id FROM place WHERE id=?", (place_id,)).fetchone()
        if r:
            con.execute("DELETE FROM place WHERE id=?", (place_id,))
            con.execute("UPDATE finding SET edited=1 WHERE id=?", (r[0],))
            _history(con, r[0], "место удалено", place_id, None)
