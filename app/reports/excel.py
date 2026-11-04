"""Excel-реестр по согласованному образцу v2 от 04.11 (ФТ-29, Р80): 4 листа — «Замечания», «Места»,
«Покрытие проверки», «Состав комплекта». Структура столбцов зафиксирована образцом: менять только по согласованию."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image

from ..findings.model import GROUPS
from ..findings.store import findings, places

SHEETS = ["Замечания", "Места", "Покрытие проверки", "Состав комплекта"]
FINDING_COLS = ["№", "Группа", "Класс", "Критичность", "Замечание", "Скриншот (первое место; все — на листе «Места»)",
                "Листы", "Файлы", "Мест", "Правило", "Основание", "Доказательство", "Решение", "Пересмотр",
                "Правлено вручную", "Найдено", "Изменено"]
FINDING_W = [6, 14, 16, 12, 60, 34, 14, 26, 6, 18, 40, 40, 13, 14, 10, 11, 15]
PLACE_COLS = ["№ замечания", "№ места", "Файл", "Стр. PDF", "Лист по штампу", "Ячейка XLSX", "x0, пт", "y0, пт",
              "x1, пт", "y1, пт", "Способ", "Скриншот"]
PLACE_W = [11, 8, 30, 8, 10, 10, 8, 8, 8, 8, 13, 34]
STAGE_COLS = ["Код", "Этап / требование", "Статус", "Замечаний", "Пояснение (что пропущено и почему)"]
STAGE_W = [10, 60, 16, 10, 70]
COMP_COLS = ["Файл", "Стр.", "Лист", "Шифр", "Наименование листа", "Формат", "Марка", "Раздел", "Подсистемы",
             "Типы документа", "Уверенность", "Решение", "Штамп", "Чтение"]
COMP_W = [30, 6, 6, 26, 40, 8, 8, 8, 12, 22, 12, 12, 16, 22]

HDR = PatternFill("solid", fgColor="1F4E78")
HFONT = Font(bold=True, color="FFFFFF")
CLS = {"подтверждённая ошибка": "FDE2E1", "возможная ошибка": "FFF3CD", "требуется уточнение": "E7F1FB"}
ST = {"частично": "FFF3CD", "пропущено": "EEEEEE", "ошибка": "FDE2E1", "ручная проверка": "E7F1FB", "конфликт": "FDE2E1"}
LEVEL = {"спорно": "FFF3CD", "не определено": "FDE2E1"}
_thin = Side(style="thin", color="BFBFBF")
BOX = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
WRAP = Alignment(wrap_text=True, vertical="top")


def _shot(ws, cell: str, row: int, shot: bytes, box=(230, 140)) -> None:
    im = Image.open(io.BytesIO(bytes(shot)))
    im.thumbnail(box)
    b = io.BytesIO()
    im.save(b, "PNG")
    b.seek(0)
    ws.add_image(XLImage(b), cell)
    ws.row_dimensions[row].height = max(ws.row_dimensions[row].height or 15, im.height * 0.75 + 6)


def _table(ws, row, headers, widths, rows, fill_col=None, fills=None) -> None:
    for c, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(row, c, h)
        cell.fill, cell.font, cell.border = HDR, HFONT, BOX
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = w
    for r, vals in enumerate(rows, row + 1):
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v)
            cell.alignment, cell.border = WRAP, BOX
        if fill_col is not None and vals[fill_col] in fills:
            ws.cell(r, fill_col + 1).fill = PatternFill("solid", fgColor=fills[vals[fill_col]])
    ws.freeze_panes = ws.cell(row + 1, 1)
    ws.auto_filter.ref = f"A{row}:{get_column_letter(len(headers))}{row + max(len(rows), 1)}"


def _sheet_key(x: str):
    return (0, int(x), "") if x.isdigit() else (1, 0, x)


def build(obj) -> Workbook:
    con = obj.con
    wb = Workbook()
    # --- 1. Замечания (нумерация как в HTML: группа.порядковый)
    ws = wb.active
    ws.title = SHEETS[0]
    fs = sorted(findings(con), key=lambda f: (f["grp"], f["rule"], f["id"]))
    code_of, rows, shots, num = {}, [], [], {g: 0 for g in GROUPS}
    for f in fs:
        num[f["grp"]] += 1
        code_of[f["id"]] = f"{f['grp']}.{num[f['grp']]}"
        pl = places(con, f["id"])
        sheets = sorted({p["sheet"] or (f"стр. {p['page_no']}" if p["page_no"] else p["cell"]) for p in pl}, key=_sheet_key)
        shots.append(next((p["shot"] for p in pl if p["shot"]), None))
        rows.append([code_of[f["id"]], GROUPS[f["grp"]], f["cls"], f["severity"], f["text"], "", ", ".join(sheets),
                     "; ".join(sorted({p["file_name"] for p in pl})), len(pl), f["rule"], f["basis"], f["proof"],
                     f["decision"], f["review"] or "", "да" if f["edited"] else "", f["created_at"][:10],
                     f["updated_at"][:16]])
    _table(ws, 1, FINDING_COLS, FINDING_W, rows, 2, CLS)
    for i, s in enumerate(shots, 2):
        if s:
            _shot(ws, f"F{i}", i, s)
    # --- 2. Места
    ws = wb.create_sheet(SHEETS[1])
    rows, shots = [], []
    for f in fs:
        for k, p in enumerate(places(con, f["id"]), 1):
            xy = [None if p[x] is None else round(p[x], 1) for x in ("x0", "y0", "x1", "y1")]
            rows.append([code_of[f["id"]], k, p["file_name"], p["page_no"], p["sheet"], p["cell"], *xy, p["method"], ""])
            shots.append(p["shot"])
    _table(ws, 1, PLACE_COLS, PLACE_W, rows)
    for i, s in enumerate(shots, 2):
        if s:
            _shot(ws, f"L{i}", i, s)
    # --- 3. Покрытие проверки
    ws = wb.create_sheet(SHEETS[2])
    run = con.execute("SELECT * FROM check_run ORDER BY id DESC LIMIT 1").fetchone()
    vers = json.loads(run["versions"]) if run else {}
    stages = con.execute("SELECT * FROM check_stage WHERE run_id=? ORDER BY id", (run["id"],)).fetchall() if run else []
    info = [("Объект", obj.name), ("Проверка", f"{run['started_at']} — {run['status']}" if run else "не запускалась"),
            ("Версия программы", vers.get("программа", "")), ("Словарь признаков", vers.get("словарь признаков", "")),
            ("Базовые проверки", vers.get("базовые проверки", "")),
            ("Инструкции", "; ".join(f"{k} v{v}" for k, v in (vers.get("инструкции") or {}).items()) or "нет"),
            ("Координаты мест", "пункты PDF (1/72 дюйма), начало — левый верхний угол листа в его отображаемой ориентации")]
    for r, (k, v) in enumerate(info, 1):
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.cell(r, 2, v)
    _table(ws, len(info) + 2, STAGE_COLS, STAGE_W,
           [[s["code"], s["title"], s["status"], s["findings"], s["message"]] for s in stages], 2, ST)
    # --- 4. Состав комплекта
    ws = wb.create_sheet(SHEETS[3])
    rows = []
    for f in obj.files():
        for p in obj.pages(f.id):
            st, c = obj.stamp(p["id"]), obj.classification(p["id"])

            def fv(k, st=st):
                return ((st["fields"].get(k) or {}).get("value", "")) if st else ""
            rows.append([f.name, p["page_no"], fv("лист"), fv("шифр"), fv("наименование листа"), p["format"],
                         ",".join(c.mark.value) if c else "", ",".join(c.section.value) if c else "",
                         ",".join(c.subsystems.value) if c else "", ",".join(c.doc_types.value) if c else "",
                         c.level if c else "", c.source if c else "",
                         (st["source"] + (", подтверждён" if st["confirmed"] else "")) if st and st["recognized"] else "нет",
                         p["extract_status"]])
    _table(ws, 1, COMP_COLS, COMP_W, rows, 10, LEVEL)
    return wb


def export(obj, folder: Path | None = None) -> Path:
    folder = Path(folder or Path(obj.folder) / "reports")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"Замечания_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
    build(obj).save(out)
    return out
