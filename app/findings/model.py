"""Черновик замечания от проверки: правило, причина, класс, текст из значений извлечения и все места."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CONFIRMED = "подтверждённая ошибка"
POSSIBLE = "возможная ошибка"
QUESTION = "требуется уточнение"
CLASSES = [CONFIRMED, POSSIBLE, QUESTION]
GROUPS = {1: "Оформление", 2: "Техническая часть", 3: "Расстановка оборудования"}
SEVERITIES = ["Не задана", "Критично", "Существенно", "Незначительно"]
DECISIONS = ["не рассмотрено", "принято", "отклонено"]
REVIEW = "требуется пересмотр"

Rect = tuple[float, float, float, float]


@dataclass
class PlaceDraft:
    page_id: int | None
    file_name: str
    page_no: int | None
    sheet: str = ""
    rect: Rect | None = None          # пункты PDF; None — весь лист или комплект
    method: str = "слой"
    cell: str = ""


@dataclass
class Draft:
    rule: str
    key: str                          # причина: одинаковая причина на нескольких листах = одно замечание
    grp: int
    cls: str
    text: str
    basis: str
    proof: str
    places: list[PlaceDraft] = field(default_factory=list)
    severity: str = "Не задана"       # только из инструкции (ФТ-23)
    fp_rule: str = ""                 # основа отпечатка, если имя правила может меняться (имя инструкции)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha1(f"{self.fp_rule or self.rule}|{self.key}".encode("utf-8")).hexdigest()
