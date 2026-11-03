"""Разбор XLSX (ТЗ 4.3, Р48): автоматическое определение строки заголовка и назначения столбцов
по словарю синонимов; схема показывается пользователю на подтверждение (шаг 3, «Состав комплекта»).
Каждое значение хранит адрес ячейки."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .textnorm import norm_text

# Роль столбца → признаки в заголовке. Порядок важен: первое совпадение.
COLUMN_SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    ("позиция", ("поз", "позиция", "№ п/п", "n п/п", "№п/п")),
    ("кабель", ("марка кабеля", "кабель")),
    ("обозначение", ("обозначение", "марка", "тип, марка", "код", "артикул", "шифр")),
    ("наименование", ("наименование", "наименование и техническая характеристика", "описание")),
    ("количество", ("кол", "количество", "кол-во", "кол.")),
    ("единица", ("ед. изм", "ед.изм", "единица", "ед.")),
    ("завод", ("завод", "изготовитель", "поставщик", "производитель")),
    ("масса", ("масса",)),
    ("длина", ("длина", "l, м", "l,м")),
    ("откуда", ("откуда", "начало")),
    ("куда", ("куда", "конец")),
    ("примечание", ("примечание", "прим.")),
]
MAX_HEADER_SCAN = 25


@dataclass
class XlsxColumn:
    index: int                 # 1-based
    letter: str
    header: str
    role: str | None


@dataclass
class XlsxTable:
    sheet: str
    header_row: int
    columns: list[XlsxColumn]
    rows: list[dict] = field(default_factory=list)     # {"row": n, "cells": {letter: value}}
    confidence: str = "высокая"                        # низкая — заголовок не найден уверенно

    def schema(self) -> dict:
        return {c.letter: {"header": c.header, "role": c.role} for c in self.columns}


def _role(header: str) -> str | None:
    h = norm_text(header).replace("\n", " ")
    for role, keys in COLUMN_SYNONYMS:
        for k in keys:
            k = norm_text(k)
            if h == k or h.startswith(k + " ") or h.startswith(k + ",") or h.startswith(k + "."):
                return role
            if len(k) > 4 and k in h:
                return role
    return None


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return re.sub(r"\s+", " ", str(v)).strip()


def parse_workbook(path: str) -> list[XlsxTable]:
    wb = load_workbook(path, read_only=True, data_only=True)
    tables = []
    try:
        for ws in wb.worksheets:
            grid = [[_cell_str(v) for v in row] for row in ws.iter_rows(values_only=True)]
            if not any(any(r) for r in grid):
                continue
            best, best_score = None, 0
            for i, row in enumerate(grid[:MAX_HEADER_SCAN]):
                texts = [c for c in row if c and not re.fullmatch(r"[\d.,\s]+", c)]
                roles = [_role(c) for c in row if c]
                score = sum(1 for r in roles if r) * 3 + len(texts)
                if len(texts) >= 2 and score > best_score:
                    best, best_score = i, score
            if best is None:
                best, conf = 0, "низкая"
            else:
                conf = "высокая" if sum(1 for c in grid[best] if c and _role(c)) >= 2 else "низкая"
            header = grid[best]
            cols = [XlsxColumn(j + 1, get_column_letter(j + 1), h, _role(h)) for j, h in enumerate(header) if h]
            t = XlsxTable(ws.title, best + 1, cols, confidence=conf)
            letters = {c.index: c.letter for c in cols}
            for n, row in enumerate(grid[best + 1:], start=best + 2):
                cells = {letters[j + 1]: v for j, v in enumerate(row) if v and (j + 1) in letters}
                # строка с номерами столбцов (1, 2, 3 …) под заголовком — не данные
                if cells and all(re.fullmatch(r"\d+", v) for v in cells.values()) and n == best + 2:
                    continue
                if cells:
                    t.rows.append({"row": n, "cells": cells})
            tables.append(t)
    finally:
        wb.close()
    return tables
