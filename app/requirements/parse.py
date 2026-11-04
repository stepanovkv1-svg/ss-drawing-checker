"""Разбор инструкции в требования без LLM — по шаблонам фраз (ФТ-15, Р39, Р40).

Каждая строка (пункт) инструкции даёт одно требование. Распознанное полностью — «утверждён»,
операция угадана, но не хватает параметров — «неоднозначен», остальное — «не разобран» (ручная проверка).
Фразы, похожие на команды программе, помечаются и не выполняются (ТЗ 6.x: инструкции — данные).
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .model import AMBIGUOUS, APPROVED, STAMP_FIELDS, UNPARSED, Requirement, Scope

COMMAND_LIKE = re.compile(
    r"игнорир|не учитыва\w* (лимит|бюджет|ограничени)|отключ\w* (контрол|лимит|провер\w* расход)|лимит|бюджет|"
    r"без (согласия|подтверждения)|платн\w* (модел|запрос)|\bapi\b|ключ\w* доступа|отправ\w* в (сеть|интернет)|"
    r"system prompt|системн\w* (правил|промпт)", re.I)

FIELD_WORDS = [
    ("наименование листа", r"наименовани\w* (листа|чертеж)"),
    ("листов", r"\bлистов\b|количеств\w* листов"),
    ("лист", r"\bномер\w* листа|\bграф\w* «?лист»?"),
    ("шифр", r"\bшифр|обозначени\w* документа"),
    ("стадия", r"\bстади"),
    ("объект", r"наименовани\w* объекта|\bобъект\w*\b"),
    ("организация", r"организац|наименовани\w* проектн"),
    ("раздел", r"\bраздел\w*\b"),
    ("формат", r"\bформат"),
]
ROLE_WORDS = [("Разраб.", r"разраб"), ("Пров.", r"\bпровер"), ("Н. контр.", r"н\.?\s?контр|нормоконтр"),
              ("ГИП", r"\bгип\b"), ("Утв.", r"\bутв")]
DOC_TYPE_WORDS = [("План расположения", r"\bна (кажд\w* |всех )?план\w*|\bплан\w* (расположени|размещени|этаж)"),
                  ("Структурная схема", r"структурн\w* схем"),
                  ("Схема подключений", r"схем\w* (подключени|соединени|внешних)"),
                  ("Принципиальная схема", r"принципиальн\w* схем"),
                  ("Общие данные", r"общи\w* данны"),
                  ("Схема кабельной канализации", r"кабельн\w* канализац")]
TABLE_WORDS = [("Кабельный журнал", r"кабельн\w* журнал"), ("Спецификация", r"спецификац")]
COLUMN_WORDS = [("количество", r"кол-?в|количеств"), ("единица", r"единиц\w* измерени|\bед\.?\s?изм"),
                ("производитель", r"производител|завод|поставщик"), ("код", r"\bкод\w*\b|артикул"),
                ("тип", r"\bтип\w*\b|марк\w* оборудования"), ("масса", r"\bмасс"), ("длина", r"\bдлин"),
                ("обозначение", r"обозначени"), ("наименование", r"наименовани"), ("примечание", r"примечани"),
                ("позиция", r"\bпозици|\bпоз\.")]
SEVERITY = [("Критично", r"критич"), ("Существенно", r"существенн"), ("Незначительно", r"незначит")]
QUOTED = re.compile(r"[«\"“']([^»\"”']{1,120})[»\"”']")


def read_text(path: Path) -> str:
    """Текст инструкции из MD, TXT или DOCX (без внешних библиотек: DOCX — это ZIP с XML)."""
    path = Path(path)
    if path.suffix.lower() == ".docx":
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        with zipfile.ZipFile(path) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
        lines = []
        body = root.find("w:body", ns)
        for el in body if body is not None else []:
            tag = el.tag.split("}")[1]
            if tag == "p":
                lines.append("".join(t.text or "" for t in el.iter(f"{{{ns['w']}}}t")))
            elif tag == "tbl":
                for tr in el.iter(f"{{{ns['w']}}}tr"):
                    cells = ["".join(t.text or "" for t in tc.iter(f"{{{ns['w']}}}t")).strip()
                             for tc in tr.iter(f"{{{ns['w']}}}tc")]
                    lines.append(" | ".join(c for c in cells if c))
        return "\n".join(lines)
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def split_items(text: str) -> list[str]:
    """Пункты инструкции: строки без маркеров списка; заголовки и пустые строки пропускаются."""
    out = []
    for raw in text.splitlines():
        if re.match(r"\s*#", raw):
            continue                    # заголовок Markdown — не требование
        s = re.sub(r"^\s*(#+\s*|[-*•–]\s+|\d+(\.\d+)*[.)]?\s+|[а-я]\)\s+)", "", raw).strip()
        s = re.sub(r"[*_`]{1,3}", "", s).strip()
        if len(s) < 8 or s.endswith(":") and len(s) < 60:
            continue
        if not re.search(r"[а-яёa-z]{3,}", s, re.I):
            continue
        out.append(s)
    return out


def _find(words, s):
    return [name for name, pat in words if re.search(pat, s, re.I)]


def _scope(s: str) -> Scope:
    from ..documents.classify import dictionary
    d = dictionary()
    sections = [k for k in d["sections"] if re.search(rf"(?<![А-ЯЁA-Z]){re.escape(k)}(?![А-ЯЁA-Z])", s)]
    subs = [k for k in d["subsystems"] if re.search(rf"(?<![А-ЯЁA-Z]){re.escape(k)}(?![А-ЯЁA-Z])", s)]
    if "А" in sections and not re.search(r"раздел\w* «?А»?|\bмарк\w* «?А»?", s):
        sections.remove("А")          # одиночная «А» в тексте — почти всегда не раздел
    return Scope(sections=sections, subsystems=subs, doc_types=_find(DOC_TYPE_WORDS, s))


def _designation_pattern(s: str) -> str:
    """«ККС-1», «К1» → шаблон ряда обозначений."""
    for q in QUOTED.findall(s) + re.findall(r"\b[A-ZА-ЯЁ]{1,5}-?\d{1,4}\b", s):
        m = re.fullmatch(r"([A-ZА-ЯЁ]{1,5})(-?)\d{1,4}(?:…|\.\.\.)?", q.strip())
        if m:
            return rf"{m.group(1)}{m.group(2)}\d+"
    return ""


def parse_line(s: str) -> Requirement:
    r = Requirement(s)
    sev = _find(SEVERITY, s)
    if sev:
        r.severity = sev[0]
    if re.search(r"оформлени", s, re.I):
        r.grp = 1
    elif re.search(r"расстановк|размещени\w* оборудован", s, re.I):
        r.grp = 3
    r.scope = _scope(s)
    low = s.lower()
    quoted = QUOTED.findall(s)
    negative = bool(re.search(r"не допуска|запрещ|не должн|недопустим|исключ", low))

    if COMMAND_LIKE.search(s):
        r.status, r.note = UNPARSED, ("похоже на команду программе (настройки, лимиты, сеть) — не выполняется; "
                                      "инструкции задают только требования к документации")
        return r

    # ВО-08: символы ↔ условные обозначения
    if re.search(r"(кажд\w*|все\w*) (символ|услов\w* (графическ\w* )?обозначени|значк)", low) and \
            re.search(r"услов\w* (графическ\w* )?обозначени|легенд|экспликац", low):
        r.op, r.status, r.note = "ВО-08", AMBIGUOUS, "поиск символов по изображению на этом шаге не выполняется"
        r.grp = 1 if r.grp == 2 else r.grp
        return r

    # ВО-05: количество по плану = в спецификации
    if re.search(r"количеств", low) and re.search(r"спецификац", low) and re.search(r"план|чертеж|схем", low):
        r.op = "ВО-05"
        r.params = {"pattern": _designation_pattern(s), "tolerance": 0}
        r.status = APPROVED if r.params["pattern"] else AMBIGUOUS
        if not r.params["pattern"]:
            r.note = "не указан шаблон обозначения (например, «ККС-1») — задайте в конструкторе"
        return r

    # ВО-04: обозначения из A есть в B
    tables = _find(TABLE_WORDS, s)
    if re.search(r"обозначени|маркировк|\bкабел", low) and tables and \
            re.search(r"(должн\w* быть|присутств|указ\w*|внесен|отражен|соответств)", low) and \
            not re.search(r"столб|граф", low):
        r.op = "ВО-04"
        r.params = {"pattern": _designation_pattern(s), "from": "листы PDF", "to": tables[0]}
        r.status = APPROVED if r.params["pattern"] else AMBIGUOUS
        if not r.params["pattern"]:
            r.note = "не указан шаблон обозначения (например, «ККС-1») — задайте в конструкторе"
        return r

    # ВО-03: столбец таблицы есть / заполнен
    if tables and re.search(r"столб|граф|колонк", low):
        cols = _find(COLUMN_WORDS, s)
        r.op = "ВО-03"
        r.params = {"table": tables[0], "column": cols[0] if cols else (quoted[0] if quoted else ""),
                    "mode": "заполнено" if re.search(r"заполн|пуст|указан", low) else "есть"}
        r.status = APPROVED if r.params["column"] else AMBIGUOUS
        if not r.params["column"]:
            r.note = "не понятно, о каком столбце речь — выберите в конструкторе"
        return r

    # ВО-06: диапазон / список значений
    rng = re.search(r"(?:от\s*(?P<a>\d+(?:[.,]\d+)?)\s*до\s*(?P<b>\d+(?:[.,]\d+)?))|(?:не (?P<dir>менее|более)\s*(?P<c>\d+(?:[.,]\d+)?))", low)
    lst = re.search(r"(?:одн\w* из|из списка|допускается только|только)\s*[:—-]?\s*(.+)$", s, re.I)
    fields = _find(FIELD_WORDS, s)
    cols = _find(COLUMN_WORDS, s)
    if rng and (fields or cols or tables):
        src = f"поле штампа:{fields[0]}" if fields else f"столбец:{cols[0] if cols else 'количество'}"
        p = {"source": src}
        if rng.group("a"):
            p["min"], p["max"] = float(rng.group("a").replace(",", ".")), float(rng.group("b").replace(",", "."))
        elif rng.group("dir") == "менее":
            p["min"] = float(rng.group("c").replace(",", "."))
        else:
            p["max"] = float(rng.group("c").replace(",", "."))
        r.op, r.params, r.status = "ВО-06", p, APPROVED
        return r

    # ВО-07: элемент на листе
    elem = None
    if re.search(r"стрелк\w* (север|ориентац)|\bроз\w* ветр|ориентаци\w* по сторонам", low):
        elem = ("стрелка севера", ["Север", "С.", "N"], "графический элемент — по тексту находится ненадёжно")
    elif re.search(r"\bмасштаб", low) and not re.search(r"штамп|основн\w* надпис", low):
        elem = ("масштаб", ["М 1:", "М1:", "Масштаб", "M 1:", "M1:"], "")
    elif re.search(r"услов\w* (графическ\w* )?обозначени|экспликац|легенд", low) and \
            re.search(r"(должн\w*|необходим\w*|приводит\w*|наличи|быть|указ\w*)", low):
        elem = ("условные обозначения", ["Условные обозначения", "Экспликация", "Обозначения"], "")
    if elem:
        r.op = "ВО-07"
        r.params = {"element": elem[0], "signs": elem[1]}
        r.status, r.note = (AMBIGUOUS, elem[2]) if elem[2] else (APPROVED, "")
        r.grp = 1 if r.grp == 2 else r.grp
        return r

    # ВО-01: поле штампа
    roles = _find(ROLE_WORDS, s)
    if re.search(r"штамп|основн\w* надпис|\bграф", low) and (fields or roles):
        r.op = "ВО-01"
        r.grp = 1 if r.grp == 2 else r.grp
        if roles and re.search(r"подпис|фамили|дат", low):
            r.params = {"field": f"подпись:{roles[0]}", "mode": "заполнено",
                        "what": "дата" if re.search(r"\bдат", low) and not re.search(r"фамили", low) else "фамилия"}
            r.status = APPROVED
            return r
        f = fields[0]
        if f == "объект" and len(fields) > 1:
            f = fields[1]
        p = {"field": f, "mode": "заполнено"}
        if quoted and not re.search(r"заполн", low):
            vals = [q.strip() for q in quoted]
            p.update({"mode": "список", "values": vals})
        elif re.search(r"шаблон|формат\w* вида|вида\b", low) and quoted:
            p.update({"mode": "шаблон", "pattern": quoted[0]})
        r.params, r.status = p, APPROVED
        return r

    # ВО-02: текст на листе есть / нет
    if quoted and re.search(r"надпис|текст|примечани|указ\w*|написа|приведен|должн|допуска|запрещ", low):
        zone = "штамп" if re.search(r"штамп|основн\w* надпис", low) else "весь лист"
        r.op = "ВО-02"
        r.params = {"text": quoted[0], "present": not negative, "regex": False, "zone": zone}
        r.status = APPROVED
        return r

    r.status, r.note = UNPARSED, "фраза не подошла ни к одному шаблону — ручная проверка или конструктор"
    return r


def parse_text(text: str) -> list[Requirement]:
    reqs, seen = [], set()
    for line in split_items(text):
        r = parse_line(line)
        while r.uid in seen:            # одинаковые строки — разные требования
            r.uid += "+"
        seen.add(r.uid)
        reqs.append(r)
    return reqs
