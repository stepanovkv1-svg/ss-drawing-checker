"""Данные объекта для проверок: листы со штампом, классификацией и надёжностью источников.

Надёжность (П1-20): замечание не строится на тексте OCR и на неподтверждённом штампе, прочитанном OCR.
- Штамп надёжен, если прочитан из текстового слоя или подтверждён пользователем.
- Поле штампа надёжно, если штамп надёжен и поле не помечено «низкая», либо поле исправлено пользователем.
- Текст листа для поиска обозначений — только из текстового слоя.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property

from ..documents.textnorm import norm_code


@dataclass
class Sheet:
    obj: object
    file_id: int
    file_name: str
    page: dict
    stamp: dict | None
    cls: object | None

    @property
    def page_id(self) -> int:
        return self.page["id"]

    @property
    def page_no(self) -> int:
        return self.page["page_no"]

    @property
    def has_stamp(self) -> bool:
        return bool(self.stamp and self.stamp["recognized"])

    @property
    def stamp_reliable(self) -> bool:
        return self.has_stamp and (self.stamp["source"] == "слой" or self.stamp["confirmed"])

    @property
    def form6(self) -> bool:
        return bool(self.stamp and "форма 6" in (self.stamp["note"] or ""))

    def field(self, name: str, reliable_only: bool = True) -> str | None:
        """Значение поля или None, если поле ненадёжно. '' — поля нет в надёжном штампе."""
        if not self.has_stamp:
            return None
        f = self.stamp["fields"].get(name)
        if f and f["edited"]:
            return f["value"]
        if reliable_only and not self.stamp_reliable:
            return None
        if f and reliable_only and f["confidence"] == "низкая" and not self.stamp["confirmed"]:
            return None
        return f["value"] if f else ""

    def field_rect(self, name: str):
        f = self.stamp["fields"].get(name) if self.stamp else None
        rects = json.loads(f["rects"]) if f else []
        return _union(rects)

    @cached_property
    def stamp_zone(self):
        """Прямоугольник штампа: объединение мест всех найденных полей."""
        if not self.stamp:
            return None
        rects = [r for f in self.stamp["fields"].values() for r in json.loads(f["rects"])]
        return _union(rects)

    @property
    def sheet_no(self) -> str:
        return (self.field("лист", reliable_only=False) or "") if self.has_stamp else ""

    @property
    def code(self) -> str:
        return self.field("шифр") or ""

    @property
    def doc_key(self) -> str:
        """Документ = шифр (с суффиксом .СО и т. п.); листы без надёжного шифра в документы не входят."""
        return norm_code(self.code) if self.code else ""

    @cached_property
    def layer_items(self) -> list[dict]:
        """Текст листа из текстового слоя (без OCR)."""
        return [dict(r) for r in self.obj.text_items(self.page_id) if r["source"] in ("слой", "пользователь")]


def _union(rects):
    rects = [r for r in rects if r]
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


@dataclass
class Context:
    obj: object
    sheets: list[Sheet] = field(default_factory=list)
    xlsx: list[dict] = field(default_factory=list)     # {file, table row, schema, rows}

    @classmethod
    def build(cls, obj) -> "Context":
        ctx = cls(obj)
        for f in obj.files():
            if f.status != "прочитан":
                continue
            if f.kind == "PDF":
                for p in obj.pages(f.id):
                    ctx.sheets.append(Sheet(obj, f.id, f.name, dict(p), obj.stamp(p["id"]), obj.classification(p["id"])))
            elif f.kind == "XLSX":
                for t in obj.xlsx_tables(f.id):
                    ctx.xlsx.append({"file": f.name, "table": dict(t), "schema": json.loads(t["schema"]),
                                     "rows": [(r["row_no"], json.loads(r["cells"])) for r in obj.xlsx_rows(t["id"])]})
        return ctx

    def documents(self) -> dict[str, list[Sheet]]:
        out: dict[str, list[Sheet]] = {}
        for s in self.sheets:
            if s.doc_key:
                out.setdefault(s.doc_key, []).append(s)
        return out
