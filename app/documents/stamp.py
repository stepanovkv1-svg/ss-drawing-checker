"""Основная надпись (штамп) по ГОСТ Р 21.101, форма 3 (ТЗ 4.4).

Поиск идёт от подписей граф («Стадия», «Лист», «Листов», «Изм.», «Дата»), а не по жёсткой сетке:
так распознаются штампы с небольшими отступами от стандарта и листы с поворотом.
Работа ведётся в координатах отображения: миллиметры от правого и нижнего края листа.
Если подписи граф не найдены, штамп считается не распознанным — поля требуют ручного ввода.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf_engine import MM_PER_PT, Rect, TextRun

ZONE_W_MM = 200     # штамп 185 мм + рамка
ZONE_H_MM = 65      # штамп 55 мм + рамка
ROW_TOL = 2.5       # мм: в одной строке

CODE_RE = re.compile(r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9.]*(?:-[A-ZА-ЯЁ0-9.]+){2,}", re.I)
DATE_RE = re.compile(r"^\d{2}\.\d{2}(?:\d{2})?$")
ROLE_WORDS = ("Разраб", "Провер", "Н.контр", "Н. контр", "ГИП", "Утв", "Нач", "Согл", "Т.контр", "Рук")


@dataclass
class Item:
    text: str
    rect: Rect               # PDF
    r: float                 # мм от правого края до левой границы фрагмента
    rr: float                # мм от правого края до правой границы
    b: float                 # мм от нижнего края до нижней границы


@dataclass
class StampField:
    name: str
    value: str
    rects: list[Rect] = field(default_factory=list)
    confidence: str = "высокая"     # высокая | низкая


@dataclass
class Stamp:
    recognized: bool
    fields: dict[str, StampField] = field(default_factory=dict)
    signatures: list[dict] = field(default_factory=list)   # роль, фамилия, дата
    note: str = ""

    def get(self, name: str) -> str:
        f = self.fields.get(name)
        return f.value if f else ""


def _items(runs: list[TextRun], page_w_pt: float, page_h_pt: float) -> list[Item]:
    out = []
    for run in runs:
        x0, y0, x1, y1 = run.disp
        r = (page_w_pt - x0) * MM_PER_PT
        rr = (page_w_pt - x1) * MM_PER_PT
        b = (page_h_pt - y1) * MM_PER_PT
        if r <= ZONE_W_MM and b <= ZONE_H_MM:
            out.append(Item(run.text.strip(), run.rect, r, rr, b))
    return out


def _label(items: list[Item], text: str) -> list[Item]:
    t = text.lower().rstrip(".")
    return [i for i in items if i.text.lower().rstrip(". ") == t]


def _is_punct(t: str) -> bool:
    return bool(t.strip()) and not re.search(r"[0-9A-Za-zА-Яа-яЁё]", t)


def _rows(parts: list[Item]) -> list[list[Item]]:
    """Группирует фрагменты в строки: строки — сверху вниз, внутри строки — слева направо.
    Знаки препинания (кавычки, скобки, тире) стоят выше или ниже букв, поэтому их относят к строке
    ближайшего по горизонтали слова."""
    words = [p for p in parts if not _is_punct(p.text)]
    puncts = [p for p in parts if _is_punct(p.text)]
    rows: list[list[Item]] = []
    for p in sorted(words, key=lambda i: -i.b):
        for row in rows:
            if abs(row[0].b - p.b) <= ROW_TOL:
                row.append(p)
                break
        else:
            rows.append([p])
    for p in puncts:
        if not rows:
            rows.append([p])
            continue

        def gap(w: Item) -> float:
            # расстояние до ближайшего края слова: знак внутри длинной фразы к ней не «прилипает»
            edge = min(abs(p.rr - w.r), abs(w.rr - p.r), abs(p.r - w.r), abs(p.rr - w.rr))
            return edge + abs(w.b - p.b) * 0.1
        nearest = min((w for r in rows for w in r), key=gap)
        next(r for r in rows if nearest in r).append(p)
    return [sorted(r, key=lambda i: -i.r) for r in rows]


def _join(parts: list[Item]) -> str:
    """Склеивает фрагменты в порядке чтения; «Горно» «-» «Алтайск» → «Горно-Алтайск», «( М 1:2000)» → «(М 1:2000)»."""
    s = ""
    for row in _rows(parts):
        for p in row:
            t = p.text.strip()
            if not s:
                s = t
            elif s.endswith("-") or t.startswith("-"):
                s += t
            else:
                s += " " + t
    s = re.sub(r"(?<=[А-Яа-яA-Za-z])\s*-\s*(?=[А-ЯA-Z])", "-", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r'"\s*([^"]*?)\s*"', r'"\1"', s)
    return re.sub(r"\s+", " ", s).strip()


def _field(name: str, parts: list[Item], confidence="высокая") -> StampField | None:
    parts = [p for p in parts if p.text.strip()]
    if not parts:
        return None
    return StampField(name, _join(parts), [p.rect for p in parts], confidence)


def parse_stamp(runs: list[TextRun], page_w_pt: float, page_h_pt: float) -> Stamp:
    items = _items(runs, page_w_pt, page_h_pt)
    st_l = _label(items, "Стадия")
    if not st_l:
        return Stamp(False, note="Подписи граф штампа не найдены — поля нужно ввести вручную")
    stadia = max(st_l, key=lambda i: i.b)            # самая верхняя, если их несколько
    row = [i for i in items if abs(i.b - stadia.b) <= ROW_TOL]
    list_l = [i for i in row if i.text.lower().startswith("лист") and not i.text.lower().startswith("листов")
              and i.r < stadia.r]
    lists_l = [i for i in row if i.text.lower().startswith("листов")]
    header = [i for i in items if i.text.lower().rstrip(". ") in ("изм", "дата", "подпись", "№ док")
              and i.b > stadia.b]
    header_b = min((h.b for h in header), default=stadia.b + 5)
    date_l = [h for h in header if h.text.lower().startswith("дата")]
    sign_l = [h for h in header if h.text.lower().startswith("подпись")]
    mid_left = (date_l[0].rr - 2) if date_l else stadia.r + 70   # левая граница средней графы (мм от правого края)
    right_edge = stadia.r + 2                                    # граница правой части (графа «Стадия» и правее)

    st = Stamp(True)
    fields: dict[str, StampField | None] = {}

    # Стадия / Лист / Листов: значения под подписями, в своей колонке
    cols = sorted([stadia] + list_l[:1] + lists_l[:1], key=lambda i: -i.r)
    for k, lab in enumerate(cols):
        left = lab.r + 1.5
        right = cols[k + 1].r + 1.5 if k + 1 < len(cols) else -1
        vals = [i for i in items if right < i.r <= left and lab.b - 12 < i.b < lab.b - 1]
        key = {"стадия": "стадия", "лист": "лист", "листов": "листов"}[lab.text.lower().rstrip(". ")]
        fields[key] = _field(key, vals)
    if fields.get("листов") is None and lists_l:
        fields["листов"] = None

    # Шифр: обозначение в верхней строке штампа
    codes = [i for i in items if i.b > header_b + 6 and CODE_RE.search(i.text) and i.r < mid_left + 15]
    if codes:
        top = max(codes, key=lambda i: i.b)
        fields["шифр"] = StampField("шифр", CODE_RE.search(top.text).group(0), [top.rect])

    # Наименование объекта: строки между шифром и строкой «Изм. … Дата», правее графы «Дата»
    code_b = max((c.b for c in codes), default=99)
    obj = [i for i in items if header_b + 2 < i.b < code_b - 2 and i.r < mid_left]
    fields["объект"] = _field("объект", obj)

    # Средняя графа: выше нижней границы значений «Стадия» — наименование части/здания, ниже — наименование листа
    values_b = min((p.b for f in ("стадия", "лист") if fields.get(f) for p in items
                    if any(p.rect == r for r in fields[f].rects)), default=stadia.b - 7)
    middle = [i for i in items if right_edge < i.r < mid_left and i.b < header_b - 1 and i.b > 2.5]
    fields["раздел"] = _field("раздел", [i for i in middle if i.b >= values_b - 2])
    fields["наименование листа"] = _field("наименование листа", [i for i in middle if i.b < values_b - 2])

    # Организация: правая часть ниже строки значений
    org = [i for i in items if i.r <= right_edge and 2.5 < i.b < values_b - 3]
    fields["организация"] = _field("организация", org)

    # Формат: последняя строка под рамкой
    fmt = [i for i in items if i.b <= 2.5 and i.r < 60]
    if fmt:
        txt = re.sub(r"\s+", "", _join(fmt)).replace("Формат", "")
        fields["формат"] = StampField("формат", txt, [i.rect for i in fmt])

    # Подписи: роль — левая колонка, фамилия — до графы «Подпись», дата — в графе «Дата»
    roles = [i for i in items if i.b < header_b - 1 and i.r > mid_left and any(i.text.startswith(w) for w in ROLE_WORDS)]
    for role in sorted(roles, key=lambda i: -i.b):
        same = [i for i in items if abs(i.b - role.b) <= 1.5 and i is not role and i.r > mid_left]
        name = [i for i in same if not DATE_RE.match(i.text) and (not sign_l or i.r > sign_l[0].rr)]
        date = [i for i in same if DATE_RE.match(i.text)]
        st.signatures.append({"роль": role.text.rstrip(), "фамилия": _join(name) if name else "",
                              "дата": date[0].text if date else "",
                              "rects": [role.rect] + [i.rect for i in name + date]})

    st.fields = {k: v for k, v in fields.items() if v is not None}
    missing = [k for k in ("шифр", "лист", "наименование листа") if k not in st.fields]
    if missing:
        st.note = "Не найдены поля: " + ", ".join(missing)
        for f in st.fields.values():
            f.confidence = "низкая" if f.name in missing else f.confidence
    return st
