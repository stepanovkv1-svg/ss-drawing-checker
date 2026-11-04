"""Требование инструкции: операция ВО-NN + параметры + область + критичность + раздел реестра + шаблон текста.

Статус разбора: утверждён (выполняется) | неоднозначен | не разобран (оба → ручная проверка в покрытии).
Инструкции — данные, а не команды: никакое требование не меняет настройки, лимиты и сеть (Р30).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

APPROVED = "утверждён"
AMBIGUOUS = "неоднозначен"
UNPARSED = "не разобран"
STATUSES = [APPROVED, AMBIGUOUS, UNPARSED]

OPS = {
    "ВО-01": "Поле штампа заполнено / соответствует шаблону",
    "ВО-02": "На листе есть / нет текста",
    "ВО-03": "В таблице есть столбец / значения заполнены",
    "ВО-04": "Обозначения из документа A есть в документе B",
    "ВО-05": "Количество по плану равно количеству в спецификации",
    "ВО-06": "Значение в диапазоне / из списка допустимых",
    "ВО-07": "На листе есть элемент (масштаб, условные обозначения, стрелка севера)",
    "ВО-08": "Каждому символу на плане есть строка в условных обозначениях",
}
# ВО-08 требует поиска символов по изображению — на Э1 операция не готова, требование уходит в ручную проверку
NOT_READY = {"ВО-08": "поиск символов по изображению на этом шаге не выполняется"}

STAMP_FIELDS = ["шифр", "объект", "раздел", "наименование листа", "стадия", "лист", "листов", "организация", "формат"]
TABLE_ROLES = ["позиция", "наименование", "обозначение", "тип", "код", "производитель", "единица", "количество",
               "масса", "примечание", "кабель", "откуда", "куда", "длина"]
TABLE_TYPES = ["любая", "Спецификация", "Кабельный журнал"]
ZONES = ["весь лист", "вне штампа", "штамп"]

DEFAULT_TEMPLATES = {
    "ВО-01": "{где}: в основной надписи графа «{поле}» {что}.",
    "ВО-02": "{где}: {что}.",
    "ВО-03": "{где}: {что}.",
    "ВО-04": "{где}: {что}.",
    "ВО-05": "{где}: {что}.",
    "ВО-06": "{где}: {что}.",
    "ВО-07": "{где}: {что}.",
    "ВО-08": "{где}: {что}.",
}


@dataclass
class Scope:
    """Область действия; пустой список — без ограничения."""
    sections: list[str] = field(default_factory=list)
    subsystems: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)
    doc_types: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.sections or self.subsystems or self.marks or self.doc_types)

    def merged(self, outer: "Scope") -> "Scope":
        """Область требования внутри области инструкции: непустое у требования уточняет."""
        return Scope(*(getattr(self, k) or getattr(outer, k) for k in ("sections", "subsystems", "marks", "doc_types")))

    def overlaps(self, other: "Scope") -> bool:
        for k in ("sections", "subsystems", "marks", "doc_types"):
            a, b = set(getattr(self, k)), set(getattr(other, k))
            if a and b and not a & b:
                return False
        return True

    def text(self) -> str:
        parts = [", ".join(v) for v in (self.sections, self.subsystems, self.marks, self.doc_types) if v]
        return "; ".join(parts) or "весь комплект"

    @classmethod
    def from_json(cls, d: dict | None) -> "Scope":
        d = d or {}
        return cls(**{k: list(d.get(k) or []) for k in ("sections", "subsystems", "marks", "doc_types")})


@dataclass
class Requirement:
    fragment: str                            # исходный фрагмент инструкции
    status: str = UNPARSED
    op: str = ""
    params: dict = field(default_factory=dict)
    scope: Scope = field(default_factory=Scope)
    severity: str = "Не задана"
    grp: int = 2                             # В2: по умолчанию «2 — Техническая часть»
    template: str = ""
    note: str = ""                           # почему неоднозначно / не разобрано
    norm_ref: str = ""                       # ссылка на норматив (проверка цитат — Э2)
    uid: str = ""                            # устойчивый ключ: замечания узнаются между версиями

    def __post_init__(self):
        if not self.uid:
            self.uid = fragment_uid(self.fragment)

    @property
    def runnable(self) -> bool:
        return self.status == APPROVED and self.op in OPS and self.op not in NOT_READY

    @property
    def exec_class(self) -> str:
        """Класс выполнения: локально / вручную (LLM — Э3)."""
        return "локально" if self.runnable else "вручную"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Requirement":
        d = dict(d)
        d["scope"] = Scope.from_json(d.get("scope"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def fragment_uid(text: str) -> str:
    t = re.sub(r"\s+", " ", text.strip().lower().replace("ё", "е"))
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:12]
