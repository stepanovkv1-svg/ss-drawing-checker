"""Запуск базовых проверок по объекту. Каждый этап получает статус; ошибка одной проверки не останавливает
остальные. Повторный запуск на тех же данных даёт те же замечания (П1-30)."""
from __future__ import annotations

import json
import traceback
from typing import Callable

from .. import VERSION
from ..data import schema
from ..documents.classify import dictionary
from ..documents.pdf_engine import PdfError, engine
from ..findings.shots import make_shot
from ..findings.store import apply_drafts
from ..requirements.conflicts import blocked, find_conflicts
from ..rules.base_checks import CHECKS
from ..rules.ops import run_instructions
from ..rules.context import Context

RULES_VERSION = "БП 1.0, ВО 1.0"


def run_checks(obj, progress: Callable[[int, int, str], None] | None = None,
               cancelled: Callable[[], bool] | None = None, instructions: list | None = None,
               conflict_decisions: dict | None = None) -> int:
    """instructions — включённые инструкции из библиотеки (их версии копируются в объект);
    conflict_decisions — решения пользователя по конфликтам требований."""
    con = obj.con
    instructions = instructions or []
    versions = {"программа": VERSION, "словарь признаков": dictionary()["version"], "базовые проверки": RULES_VERSION,
                "инструкции": {i.name: i.ver for i in instructions}}
    with con:
        run_id = con.execute("INSERT INTO check_run(started_at, status, versions) VALUES (?,?,?)",
                             (schema.now(), "идёт", json.dumps(versions, ensure_ascii=False))).lastrowid
    ctx = Context.build(obj)
    docs: dict[int, object] = {}

    def doc_for(sheet):
        if sheet.file_id not in docs:
            f = next(x for x in obj.files() if x.id == sheet.file_id)
            docs[sheet.file_id] = engine().open(str(obj.file_path(f)))
        return docs[sheet.file_id]

    drafts = []
    total = len(CHECKS) + 1 + bool(instructions)
    status = "готово"
    try:
        for k, (code, title, fn) in enumerate(CHECKS, 1):
            if cancelled and cancelled():
                status = "остановлено"
                break
            if progress:
                progress(k, total, f"{code} {title}")
            try:
                res = fn(ctx, doc_for) if code == "БП-10" else fn(ctx)
                st, msg, found = res.status, res.message, res.drafts
            except Exception as e:  # noqa: BLE001 — ошибка проверки фиксируется в статусе этапа
                st, msg, found = "ошибка", f"{type(e).__name__}: {e}", []
                traceback.print_exc()
            drafts += found
            with con:
                con.execute("INSERT INTO check_stage(run_id, code, title, status, message, findings) VALUES (?,?,?,?,?,?)",
                            (run_id, code, title, st, msg, len(found)))
        if status == "готово" and instructions and not (cancelled and cancelled()):
            if progress:
                progress(len(CHECKS) + 1, total, "Требования инструкций")
            conflicts = find_conflicts(instructions)
            for code, title, res in run_instructions(ctx, instructions, blocked(conflicts, conflict_decisions or {})):
                drafts += res.drafts
                with con:
                    con.execute("INSERT INTO check_stage(run_id, code, title, status, message, findings)"
                                " VALUES (?,?,?,?,?,?)", (run_id, code, title, res.status, res.message, len(res.drafts)))
            with con:
                for i in instructions:
                    snap = i.snapshot()
                    con.execute("INSERT INTO applied_instruction(run_id, instruction_id, name, ver, scope, text,"
                                " requirements) VALUES (?,?,?,?,?,?,?)",
                                (run_id, i.id, i.name, i.ver, json.dumps(snap["scope"], ensure_ascii=False), i.text,
                                 json.dumps(snap["requirements"], ensure_ascii=False)))
        elif cancelled and cancelled():
            status = "остановлено"
        if status == "готово":
            apply_drafts(con, run_id, drafts)
            if progress:
                progress(total, total, "Скриншоты мест")
            _shots(obj, doc_for, cancelled)
    finally:
        for d in docs.values():
            d.close()
        with con:
            con.execute("UPDATE check_run SET finished_at=?, status=? WHERE id=?", (schema.now(), status, run_id))
    return run_id


def _shots(obj, doc_for, cancelled) -> None:
    con = obj.con
    rows = con.execute("SELECT p.id, p.page_id, p.x0, p.y0, p.x1, p.y1, pg.file_id, pg.page_no FROM place p "
                       "JOIN page pg ON pg.id = p.page_id WHERE p.shot IS NULL").fetchall()

    class _S:   # минимальный «лист» для doc_for
        def __init__(self, file_id):
            self.file_id = file_id

    for r in rows:
        if cancelled and cancelled():
            break
        rect = None if r["x0"] is None else (r["x0"], r["y0"], r["x1"], r["y1"])
        try:
            png = make_shot(doc_for(_S(r["file_id"])), r["page_no"] - 1, rect)
        except (PdfError, OSError):
            continue
        with con:
            con.execute("UPDATE place SET shot=? WHERE id=?", (png, r["id"]))


def last_run(con):
    run = con.execute("SELECT * FROM check_run ORDER BY id DESC LIMIT 1").fetchone()
    stages = con.execute("SELECT * FROM check_stage WHERE run_id=? ORDER BY id", (run["id"],)).fetchall() if run else []
    return run, stages
