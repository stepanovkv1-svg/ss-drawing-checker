"""Извлечение данных (Э1, шаг 2): текст листа с координатами, OCR листов без текстового слоя,
штампы, таблицы XLSX. Каждое значение хранит источник и версию извлечения (ТЗ 3.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ..data import ProjectObject
from . import ocr
from .pdf_engine import PdfError, TextRun, engine
from .stamp import parse_stamp
from .stamp_grid import layer_is_garbled, parse_stamp_grid
from .xlsx import parse_workbook

EXTRACT_VERSION = "e1.3"
OCR_DPI = 300
OCR_MAX_PIXELS = 80_000_000      # большие листы распознаются при меньшем разрешении


@dataclass
class ExtractResult:
    file: str
    pages: int = 0
    ocr_pages: int = 0
    stamps: int = 0
    tables: int = 0
    rows: int = 0
    errors: list[str] | None = None


def _ocr_page(doc, i: int, w_pt: float, h_pt: float) -> tuple[list[TextRun], list[tuple]]:
    dpi = OCR_DPI
    while (w_pt * dpi / 72) * (h_pt * dpi / 72) > OCR_MAX_PIXELS and dpi > 150:
        dpi -= 50
    img = doc.render(i, dpi).convert("L")
    words = ocr.recognize(img)
    rects = doc.from_pixels(i, [w.px for w in words], dpi)
    runs, items = [], []
    for w, r in zip(words, rects):
        disp = tuple(v * 72 / dpi for v in w.px)
        runs.append(TextRun(w.text, r, disp))
        items.append((w.text, r, w.confidence))
    return runs, items


def extract_pdf(obj: ProjectObject, file_id: int, cancelled: Callable[[], bool] | None = None) -> ExtractResult:
    f = next(x for x in obj.files() if x.id == file_id)
    res = ExtractResult(f.name, errors=[])
    try:
        doc = engine().open(str(obj.file_path(f)))
    except PdfError as e:
        res.errors.append(e.reason)
        return res
    con = obj.con
    try:
        for page in obj.pages(file_id):
            if cancelled and cancelled():
                break
            i = page["page_no"] - 1
            pid = page["id"]
            status = "готово"
            source = "слой"
            if page["text_chars"] > 0:
                runs = doc.runs(i)
                items = [(r.text, r.rect, None) for r in runs]
                if layer_is_garbled(runs):
                    # шрифт без таблицы кодировки: в слое «мусор»; текст листа считается нечитаемым
                    status = "текстовый слой без кодировки — текст не читается"
                    items = []
            elif ocr.available():
                try:
                    runs, items = _ocr_page(doc, i, page["width_pt"], page["height_pt"])
                    source = "OCR"
                    res.ocr_pages += 1
                    weak = sum(1 for _, _, c in items if c < ocr.CONFIDENT)
                    status = f"OCR: неуверенно {weak} из {len(items)}" if weak else "OCR"
                except ocr.OcrUnavailable as e:
                    runs, items, status = [], [], f"ошибка OCR: {e}"
                    res.errors.append(f"лист {page['page_no']}: {e}")
            else:
                runs, items, status = [], [], "нет текста, OCR недоступен"
            st = parse_stamp(runs, page["width_pt"], page["height_pt"])
            if not st.recognized:
                try:
                    # после OCR всего листа графы читаются заново по сетке — OCR отдельной графы точнее
                    grid = parse_stamp_grid(doc, i, [] if source == "OCR" else runs, page["width_pt"], page["height_pt"])
                except ocr.OcrUnavailable as e:
                    grid = None
                    res.errors.append(f"лист {page['page_no']}: {e}")
                if grid:
                    st = grid
                else:
                    st.note = ("Основная надпись не найдена (титульный лист, обложка или нестандартная рамка) — "
                               "поля можно ввести вручную")
            with con:
                con.execute("DELETE FROM text_item WHERE page_id=?", (pid,))
                con.executemany(
                    "INSERT INTO text_item(page_id, text, x0, y0, x1, y1, source, confidence, version) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(pid, t, *r, source, c, EXTRACT_VERSION) for t, r, c in items])
                old = con.execute("SELECT confirmed FROM stamp WHERE page_id=?", (pid,)).fetchone()
                edited = {r["name"]: r for r in con.execute(
                    "SELECT name, value, rects, confidence FROM stamp_field WHERE page_id=? AND edited=1", (pid,))}
                con.execute("DELETE FROM stamp WHERE page_id=?", (pid,))
                con.execute("INSERT INTO stamp(page_id, recognized, source, note, confirmed, version) VALUES (?,?,?,?,?,?)",
                            (pid, int(st.recognized), "OCR" if source == "OCR" or st.source == "OCR" else "слой", st.note, old["confirmed"] if old else 0, EXTRACT_VERSION))
                for fld in st.fields.values():
                    if fld.name in edited:      # правку пользователя не перезаписываем (ТЗ 7.3)
                        continue
                    conf = "низкая" if source == "OCR" and st.source != "OCR" else fld.confidence
                    con.execute("INSERT INTO stamp_field(page_id, name, value, rects, confidence) VALUES (?,?,?,?,?)",
                                (pid, fld.name, fld.value, json.dumps(fld.rects), conf))
                for name, r in edited.items():
                    con.execute("INSERT INTO stamp_field(page_id, name, value, rects, confidence, edited) VALUES (?,?,?,?,?,1)",
                                (pid, name, r["value"], r["rects"], r["confidence"]))
                for s in st.signatures:
                    con.execute("INSERT INTO signature(page_id, role, surname, date, rects) VALUES (?,?,?,?,?)",
                                (pid, s["роль"], s["фамилия"], s["дата"], json.dumps(s["rects"])))
                con.execute("UPDATE page SET extract_status=? WHERE id=?", (status, pid))
            res.pages += 1
            res.stamps += int(st.recognized)
    finally:
        doc.close()
    return res


def extract_xlsx(obj: ProjectObject, file_id: int) -> ExtractResult:
    f = next(x for x in obj.files() if x.id == file_id)
    res = ExtractResult(f.name, errors=[])
    try:
        tables = parse_workbook(str(obj.file_path(f)))
    except Exception as e:  # noqa: BLE001 — повреждённый или нестандартный файл не должен ронять приём
        res.errors.append(f"таблица не разобрана: {type(e).__name__}")
        obj.set_file_status(f.id, "ошибка", "таблица не разобрана")
        return res
    con = obj.con
    with con:
        con.execute("DELETE FROM xlsx_table WHERE file_id=?", (file_id,))
        for t in tables:
            cur = con.execute(
                "INSERT INTO xlsx_table(file_id, sheet, header_row, schema, confidence, version) VALUES (?,?,?,?,?,?)",
                (file_id, t.sheet, t.header_row, json.dumps(t.schema(), ensure_ascii=False), t.confidence, EXTRACT_VERSION))
            con.executemany("INSERT INTO xlsx_row(table_id, row_no, cells) VALUES (?,?,?)",
                            [(cur.lastrowid, r["row"], json.dumps(r["cells"], ensure_ascii=False)) for r in t.rows])
            res.tables += 1
            res.rows += len(t.rows)
    return res


def extract_file(obj: ProjectObject, file_id: int, cancelled=None) -> ExtractResult:
    f = next(x for x in obj.files() if x.id == file_id)
    if f.status != "прочитан":
        return ExtractResult(f.name, errors=[f"файл не прочитан: {f.error or f.status}"])
    if f.kind == "PDF":
        return extract_pdf(obj, file_id, cancelled)
    if f.kind == "XLSX":
        return extract_xlsx(obj, file_id)
    return ExtractResult(f.name)
