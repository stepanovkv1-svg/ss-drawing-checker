"""Базовые проверки согласованности БП-01…БП-10 (ТЗ 5.1). Выполняются всегда, без инструкций и без сети.

Программа сверяет документы между собой и проверяет заполненность; это не правила проектирования.
Текст замечания строится только из извлечённых значений (Р75). Одна причина на нескольких листах —
одно замечание со всеми местами (Р74).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..documents.textnorm import norm_code, norm_text
from ..findings.model import CONFIRMED, POSSIBLE, QUESTION, Draft, PlaceDraft
from .context import Context, Sheet

BASIS = "Базовая проверка {code} «{title}» (согласованность комплекта, ТЗ 5.1)"


@dataclass
class StageResult:
    status: str = "выполнено"          # выполнено | частично | пропущено | ручная проверка
    message: str = ""
    drafts: list[Draft] = field(default_factory=list)


def _place(s: Sheet, rect=None, method: str | None = None) -> PlaceDraft:
    if rect is None:
        method = method or "весь лист"
    return PlaceDraft(s.page_id, s.file_name, s.page_no, s.sheet_no, rect, method or "слой")


def _sheet_label(s: Sheet) -> str:
    return f"лист {s.sheet_no}" if s.sheet_no else f"стр. {s.page_no} файла {s.file_name}"


def _sheets_text(sheets: list[Sheet]) -> str:
    """«листы 1, 2 (файл А); лист 5 (файл Б)» — номера по штампу, сгруппированные по файлам."""
    by_file: dict[str, list] = defaultdict(list)
    for s in sheets:
        by_file[s.file_name].append(s.sheet_no or f"стр. {s.page_no}")
    if len(by_file) == 1:
        nums = next(iter(by_file.values()))
        return ("лист " if len(nums) == 1 else "листы ") + _list(nums)
    return "; ".join(("лист " if len(v) == 1 else "листы ") + _list(v) + f" ({k})" for k, v in by_file.items())


def _list(items, limit=12) -> str:
    items = list(items)
    return ", ".join(map(str, items[:limit])) + (f" и ещё {len(items) - limit}" if len(items) > limit else "")


# ------------------------------------------------------------------ БП-01
def bp01(ctx: Context) -> StageResult:
    n = len(ctx.sheets)
    if not n:
        return StageResult("пропущено", "нет листов PDF")
    formats = Counter(s.page["format"] for s in ctx.sheets)
    no_text = sum(1 for s in ctx.sheets if not s.page["text_chars"])
    garbled = sum(1 for s in ctx.sheets if "без кодировки" in (s.page.get("extract_status") or ""))
    msg = (f"Листов: {n}; форматы: " + ", ".join(f"{k} — {v}" for k, v in formats.most_common())
           + f"; без текстового слоя (OCR): {no_text}; текст без кодировки: {garbled}.")
    return StageResult("выполнено", msg)


# ------------------------------------------------------------------ БП-02
REQUIRED_FIRST = ["шифр", "лист", "наименование листа", "стадия"]
REQUIRED_NEXT = ["шифр", "лист"]


def bp02(ctx: Context) -> StageResult:
    res = StageResult()
    missing: dict[str, list] = defaultdict(list)
    no_surname: dict[str, list] = defaultdict(list)
    no_date: dict[str, list] = defaultdict(list)
    skipped = 0
    for s in ctx.sheets:
        if not s.has_stamp:
            continue
        if not s.stamp_reliable:
            skipped += 1
            continue
        for name in (REQUIRED_NEXT if s.form6 else REQUIRED_FIRST):
            if s.field(name) == "":
                missing[name].append(s)
        for sig in s.stamp["signatures"]:
            if not sig["surname"].strip():
                no_surname[sig["role"]].append(s)
            elif not sig["date"].strip():
                no_date[sig["role"]].append(s)
    for name, sheets in missing.items():
        # пустое поле в штампе из слоя может быть и непрочитанным — подтверждённая только для подтверждённого штампа
        cls = CONFIRMED if all(s.stamp["confirmed"] for s in sheets) else POSSIBLE
        res.drafts.append(Draft(
            "БП-02", f"нет поля {name}", 1, cls,
            f"В основной надписи не заполнена графа «{name}» ({_sheets_text(sheets)}).",
            BASIS.format(code="БП-02", title="Заполненность основной надписи"),
            f"Графа «{name}» не найдена в штампе на {len(sheets)} лист(ах).",
            [_place(s, s.stamp_zone, "зона штампа") for s in sheets]))
    for role, sheets in no_surname.items():
        res.drafts.append(Draft(
            "БП-02", f"нет фамилии {norm_text(role)}", 1, CONFIRMED,
            f"В основной надписи не указана фамилия в строке «{role}» ({_sheets_text(sheets)}).",
            BASIS.format(code="БП-02", title="Заполненность основной надписи"),
            f"Строка «{role}» есть, фамилии в ней нет.",
            [_place(s, s.stamp_zone, "зона штампа") for s in sheets]))
    for role, sheets in no_date.items():
        res.drafts.append(Draft(
            "БП-02", f"нет даты {norm_text(role)}", 1, POSSIBLE,
            f"В основной надписи не указана дата в строке «{role}» ({_sheets_text(sheets)}).",
            BASIS.format(code="БП-02", title="Заполненность основной надписи"),
            f"Строка «{role}» с фамилией есть, даты в ней не найдено.",
            [_place(s, s.stamp_zone, "зона штампа") for s in sheets]))
    if skipped:
        res.status = "частично"
        res.message = f"Пропущено листов: {skipped} — штамп прочитан OCR и не подтверждён («Штамп проверен»)."
    return res


# ------------------------------------------------------------------ БП-03
def _num(v: str | None) -> int | None:
    return int(v) if v and re.fullmatch(r"\d{1,4}", v.strip()) else None


def bp03(ctx: Context) -> StageResult:
    res = StageResult()
    skipped = 0
    for key, sheets in ctx.documents().items():
        numbered = []
        for s in sheets:
            n = _num(s.field("лист"))
            if n is None:
                skipped += 1
                continue
            numbered.append((n, s))
        if not numbered:
            continue
        by_no = defaultdict(list)
        for n, s in numbered:
            by_no[n].append(s)
        for n, ss in sorted(by_no.items()):
            if len(ss) > 1:
                one_file = len({x.file_name for x in ss}) == 1
                res.drafts.append(Draft(
                    "БП-03", f"{key}|дубль|{n}", 1, CONFIRMED if one_file else QUESTION,
                    f"Документ {key}: номер листа {n} повторяется на {len(ss)} листах ({_sheets_text(ss)})."
                    + ("" if one_file else " Листы в разных файлах — это один документ или разные?"),
                    BASIS.format(code="БП-03", title="Нумерация листов"),
                    "Графа «Лист»: " + "; ".join(f"стр. {s.page_no} файла {s.file_name} — {n}" for s in ss),
                    [_place(s, s.field_rect("лист"), "штамп") for s in ss]))
        nums = sorted(by_no)
        total = next((_num(s.field("листов")) for n, s in sorted(numbered, key=lambda x: x[0])
                      if _num(s.field("листов"))), None)
        first = by_no.get(1, [None])[0]
        if total and nums[-1] > total:
            res.drafts.append(Draft(
                "БП-03", f"{key}|листов|{total}", 1, CONFIRMED,
                f"Документ {key}: в графе «Листов» указано {total}, а номер листа доходит до {nums[-1]}.",
                BASIS.format(code="БП-03", title="Нумерация листов"),
                f"Листов = {total}; наибольший номер листа = {nums[-1]}.",
                ([_place(first, first.field_rect("листов"), "штамп")] if first else [])
                + [_place(s, s.field_rect("лист"), "штамп") for s in by_no[nums[-1]]]))
        top = total or nums[-1]
        # листы этого документа с номером, прочитанным OCR (штамп не подтверждён), тоже есть в наборе
        present = set(by_no) | {_num(x.field("лист", reliable_only=False)) for x in ctx.sheets
                                if x.has_stamp and norm_code(x.field("шифр", reliable_only=False) or "") == key}
        gaps = [n for n in range(1, top + 1) if n not in present]
        if gaps:
            res.drafts.append(Draft(
                "БП-03", f"{key}|пропуск|{_list(gaps, 50)}", 1, QUESTION,
                f"Документ {key}: в наборе нет листов {_list(gaps)}. Проверьте нумерацию или загрузите недостающие листы.",
                BASIS.format(code="БП-03", title="Нумерация листов"),
                f"Найдены номера листов: {_list(nums, 30)}" + (f"; листов по штампу: {total}" if total else ""),
                [_place(s, s.field_rect("лист"), "штамп") for s in (by_no[n][0] for n in nums[:1] + nums[-1:])]))
    if skipped:
        res.status = "частично"
        res.message = f"Без надёжного номера листа: {skipped} лист(ов) — штамп не подтверждён."
    return res


# ------------------------------------------------------------------ БП-04
def _majority_finding(rule, key, title, values: dict[str, list[Sheet]], what: str, rect_field: str | None,
                      cls=CONFIRMED) -> Draft | None:
    if len(values) < 2:
        return None
    ranked = sorted(values.items(), key=lambda kv: -len(kv[1]))
    main_v, main_s = ranked[0]
    minority = [s for v, ss in ranked[1:] for s in ss]
    variants = "; ".join(f"«{v}» — {len(ss)} лист(ов)" for v, ss in ranked)
    return Draft(
        rule, key, 1, cls,
        f"{what} различается по комплекту: на {len(minority)} лист(ах) написано иначе, чем на остальных "
        f"(основной вариант «{main_v}»).",
        BASIS.format(code=rule, title=title), "Варианты: " + variants,
        [_place(s, s.field_rect(rect_field) if rect_field else s.stamp_zone, "штамп") for s in minority])


def _date_format(d: str) -> str | None:
    if re.fullmatch(r"\d{2}\.\d{2}", d):
        return "ММ.ГГ"
    if re.fullmatch(r"\d{2}\.\d{4}", d):
        return "ММ.ГГГГ"
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{2,4}", d):
        return "ДД.ММ.ГГ(ГГ)"
    return None


def bp04(ctx: Context) -> StageResult:
    res = StageResult()
    title = "Единообразие шифра, наименования объекта, формата дат"
    reliable = [s for s in ctx.sheets if s.stamp_reliable]
    # шифр: одинаковый по смыслу, разное написание (латиница/кириллица, тире, пробелы)
    raw_by_norm: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for s in reliable:
        if s.code:
            raw_by_norm[norm_code(s.code)][s.code.strip()].append(s)
    for norm, variants in raw_by_norm.items():
        d = _majority_finding("БП-04", f"шифр|{norm}", title, variants, f"Написание шифра {norm}", "шифр")
        if d:
            d.proof += ". Отличие в символах: латинские и русские буквы или вид тире выглядят одинаково."
            res.drafts.append(d)
    # наименование объекта
    obj_vals: dict[str, list] = defaultdict(list)
    shown: dict[str, str] = {}
    for s in reliable:
        v = s.field("объект")
        if v:
            k = re.sub(r"[\s.,«»\"]+", " ", norm_text(v)).strip()
            obj_vals[k].append(s)
            shown.setdefault(k, v)
    d = _majority_finding("БП-04", "объект", title, {shown[k]: v for k, v in obj_vals.items()},
                          "Наименование объекта в основной надписи", "объект")
    if d:
        res.drafts.append(d)
    # формат дат
    fmt: dict[str, list] = defaultdict(list)
    seen = set()
    for s in reliable:
        for sig in s.stamp["signatures"]:
            f = _date_format(sig["date"].strip())
            if f and (s.page_id, f) not in seen:
                seen.add((s.page_id, f))
                fmt[f].append(s)
    if len(fmt) > 1:
        d = _majority_finding("БП-04", "формат дат", title, fmt, "Формат дат в основной надписи", None)
        d.text = ("В основной надписи даты записаны в разных форматах: "
                  + ", ".join(f"{k} ({len(v)} лист.)" for k, v in fmt.items()) + ".")
        res.drafts.append(d)
    skipped = sum(1 for s in ctx.sheets if s.has_stamp and not s.stamp_reliable)
    if skipped:
        res.status, res.message = "частично", f"Не участвуют {skipped} лист(ов): штамп не подтверждён."
    return res


# ------------------------------------------------------------------ БП-05
CODE_IN_TEXT = re.compile(r"[A-ZА-ЯЁ0-9]{2,}(?:[.\-‐‑–][A-ZА-ЯЁ0-9]+){2,}(?:\.[A-ZА-ЯЁ]{1,4})?")
SHEET_REF = re.compile(r"(?:см\.?\s*)л(?:ист)?\.?\s*(\d{1,3})", re.I)


def bp05(ctx: Context) -> StageResult:
    res = StageResult()
    title = "Шифры комплектов и ссылки на документы"
    docs = ctx.documents()
    # один шифр в разных файлах
    files_by_code: dict[str, set] = defaultdict(set)
    for key, sheets in docs.items():
        for s in sheets:
            files_by_code[key].add(s.file_name)
    for key, files in files_by_code.items():
        if len(files) > 1:
            sheets = docs[key]
            res.drafts.append(Draft(
                "БП-05", f"шифр в файлах|{key}", 1, QUESTION,
                f"Шифр {key} встречается в разных файлах: {_list(sorted(files))}. Это один документ, разделённый "
                "на файлы, или разные комплекты с одинаковым шифром?",
                BASIS.format(code="БП-05", title=title), f"Файлы: {_list(sorted(files))}",
                [_place(s, s.field_rect("шифр"), "штамп") for s in sheets[:1] + [x for x in sheets
                 if x.file_name != sheets[0].file_name][:1]]))
    # ссылки на документы того же объекта, которых нет в наборе
    known = set(docs)
    prefixes = {k.split("-")[0] for k in known}
    refs: dict[str, list] = defaultdict(list)
    for s in ctx.sheets:
        for it in s.layer_items:
            for m in CODE_IN_TEXT.finditer(it["text"]):
                code = norm_code(m.group(0))
                if code.split("-")[0] in prefixes and len(code) >= 8 and not any(k.startswith(code) for k in known):
                    refs[code].append((s, (it["x0"], it["y0"], it["x1"], it["y1"])))
    for code, hits in refs.items():
        sheets = {h[0].page_id for h in hits}
        res.drafts.append(Draft(
            "БП-05", f"ссылка|{code}", 1, QUESTION,
            f"На листах есть ссылка на документ {code}, которого нет в загруженном наборе. Проверьте шифр ссылки "
            "или загрузите документ.",
            BASIS.format(code="БП-05", title=title), f"Ссылка найдена на {len(sheets)} лист(ах).",
            [_place(s, r) for s, r in hits[:20]]))
    # ссылки на листы
    for key, sheets in docs.items():
        nums = {_num(s.field("лист")) for s in sheets} - {None}
        total = max((_num(s.field("листов")) or 0 for s in sheets), default=0)
        for s in sheets:
            for it in s.layer_items:
                for m in SHEET_REF.finditer(it["text"]):
                    n = int(m.group(1))
                    if n in nums or not nums:
                        continue
                    cls = CONFIRMED if total and n > total else QUESTION
                    res.drafts.append(Draft(
                        "БП-05", f"ссылка на лист|{key}|{n}", 1, cls,
                        f"Документ {key}: ссылка «{m.group(0)}» ведёт на лист {n}, которого нет"
                        + (f" (в документе {total} листов)." if cls == CONFIRMED else " в загруженном наборе."),
                        BASIS.format(code="БП-05", title=title), f"Текст на листе: «{it['text']}»",
                        [_place(s, (it["x0"], it["y0"], it["x1"], it["y1"]))]))
    return res


# ------------------------------------------------------------------ БП-06
SERIES = re.compile(r"^([A-ZА-ЯЁ]{1,5})-?(\d{1,3})$")


def bp06(ctx: Context) -> StageResult:
    res = StageResult()
    title = "Перечни обозначений: дубли и пропуски"
    for key, sheets in ctx.documents().items():
        series: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        for s in sheets:
            for it in s.layer_items:
                for tok in re.split(r"[\s,;()]+", it["text"]):
                    m = SERIES.match(norm_code(tok))
                    if m:
                        series[m.group(1)][int(m.group(2))].append((s, (it["x0"], it["y0"], it["x1"], it["y1"])))
        for prefix, nums in series.items():
            have = sorted(nums)
            if len(have) < 4:
                continue
            gaps = [n for n in range(have[0], have[-1] + 1) if n not in nums]
            if gaps and len(gaps) <= 5 and len(gaps) <= 0.3 * len(have):
                around = [nums[n - 1][0] for n in gaps if n - 1 in nums] + [nums[n + 1][0] for n in gaps if n + 1 in nums]
                res.drafts.append(Draft(
                    "БП-06", f"{key}|{prefix}|пропуск|{gaps}", 1, QUESTION,
                    f"Документ {key}: в ряду обозначений {prefix}-{have[0]}…{prefix}-{have[-1]} нет номеров "
                    + ", ".join(f"{prefix}-{n}" for n in gaps) + ". Пропуск может быть намеренным — проверьте.",
                    BASIS.format(code="БП-06", title=title), f"Найдены: {_list((f'{prefix}-{n}' for n in have), 20)}",
                    [_place(s, r) for s, r in around[:6]]))
    # дубли — в таблицах XLSX (в графике обозначение может законно повторяться: на плане и в легенде)
    for t in ctx.xlsx:
        col = next((c for c, v in t["schema"].items() if v["role"] in ("позиция", "обозначение")), None)
        if not col:
            continue
        seen: dict[str, list] = defaultdict(list)
        for row_no, cells in t["rows"]:
            v = cells.get(col, "").strip()
            if v and not re.fullmatch(r"\d+(\.\d+)?", v):
                seen[norm_code(v)].append(row_no)
        for v, rows in seen.items():
            if len(rows) > 1:
                res.drafts.append(Draft(
                    "БП-06", f"xlsx|{t['file']}|{t['table']['sheet']}|{v}", 1, CONFIRMED,
                    f"В таблице «{t['table']['sheet']}» файла {t['file']} обозначение {v} повторяется в строках "
                    f"{_list(rows)}.",
                    BASIS.format(code="БП-06", title=title), f"Столбец {col}, строки {_list(rows)}",
                    [PlaceDraft(None, t["file"], None, t["table"]["sheet"], None, "XLSX", f"{col}{r}") for r in rows]))
    res.message = "Повторы обозначений на чертежах не считаются ошибкой (обозначение есть на плане и в легенде)."
    return res


# ------------------------------------------------------------------ БП-07
DESIGNATION = re.compile(r"^[A-ZА-ЯЁ]{1,5}-?\d{1,4}(?:\.\d+)?$")


def _pdf_text_index(ctx: Context, sheets=None) -> dict[str, list]:
    idx: dict[str, list] = defaultdict(list)
    for s in sheets if sheets is not None else ctx.sheets:
        for it in s.layer_items:
            for tok in re.split(r"[\s,;()]+", it["text"]):
                if tok:
                    idx[norm_code(tok)].append((s, (it["x0"], it["y0"], it["x1"], it["y1"])))
    return idx


def bp07(ctx: Context) -> StageResult:
    res = StageResult()
    title = "Сверка состава: планы, схемы, спецификация, кабельный журнал"
    if not ctx.xlsx:
        return StageResult("пропущено", "Нет таблиц XLSX. Сверка таблиц, нарисованных в PDF, — на следующих шагах.")
    if not any(s.layer_items for s in ctx.sheets):
        return StageResult("пропущено", "В PDF нет читаемого текстового слоя.")
    idx = _pdf_text_index(ctx)
    pdf_text = " ".join(norm_code(it["text"]) for s in ctx.sheets for it in s.layer_items)
    missing_from_pdf: list = []
    xlsx_desigs: set[str] = set()
    for t in ctx.xlsx:
        cols = {v["role"]: c for c, v in t["schema"].items() if v["role"]}
        for row_no, cells in t["rows"]:
            for role in ("обозначение", "откуда", "куда"):
                c = cols.get(role)
                v = norm_code(cells.get(c, "")) if c else ""
                if v and DESIGNATION.match(v):
                    xlsx_desigs.add(v)
                    if v not in idx:
                        missing_from_pdf.append((t, f"{c}{row_no}", v))
            v = cells.get(cols.get("кабель", ""), "") if cols.get("кабель") else ""
            if v and len(v) >= 4 and norm_code(v).replace(" ", "") not in pdf_text.replace(" ", ""):
                missing_from_pdf.append((t, f"{cols['кабель']}{row_no}", v))
    grouped: dict[str, list] = defaultdict(list)
    for t, cell, v in missing_from_pdf:
        grouped[v].append((t, cell))
    for v, where in grouped.items():
        res.drafts.append(Draft(
            "БП-07", f"нет в PDF|{norm_code(v)}", 2, POSSIBLE,
            f"«{v}» из таблицы XLSX не найдено в тексте листов PDF.",
            BASIS.format(code="БП-07", title=title),
            "Ячейки: " + _list(f"{t['file']} / {t['table']['sheet']} / {c}" for t, c in where),
            [PlaceDraft(None, t["file"], None, t["table"]["sheet"], None, "XLSX", c) for t, c in where[:10]]))
    # обратная сверка: обозначения рядов из XLSX, которые есть на листах, но не в таблице
    prefixes = {re.match(r"[A-ZА-ЯЁ]+", d).group(0) for d in xlsx_desigs}
    for tok, hits in idx.items():
        m = DESIGNATION.match(tok)
        if m and re.match(r"[A-ZА-ЯЁ]+", tok).group(0) in prefixes and tok not in xlsx_desigs:
            res.drafts.append(Draft(
                "БП-07", f"нет в XLSX|{tok}", 2, POSSIBLE,
                f"Обозначение {tok} есть на листах PDF, но его нет в таблицах XLSX.",
                BASIS.format(code="БП-07", title=title), f"Найдено на {len({h[0].page_id for h in hits})} лист(ах).",
                [_place(s, r) for s, r in hits[:6]]))
    res.status = "частично"
    res.message = ("Сверяются обозначения и марки PDF ↔ XLSX. Количества не сверяются: таблицы, нарисованные в PDF, "
                   "пока не разбираются. Листы с текстом из OCR не участвуют.")
    return res


# ------------------------------------------------------------------ БП-08
CABLE = re.compile(
    r"(?<![\w/])((?:[КK][A-ZА-ЯЁ]{1,8}|ПВС|ВВГ|ОК[A-ZА-ЯЁ]{0,4}|(?:S?F|U)/[UF]TP|UTP|FTP)"
    r"(?:[-\s]?(?:нг|ng|НГ|NG)(?:\([AАaа](?:-?[A-ZА-ЯЁa-zа-яё]{1,5})?\))?)?"
    r"(?:[-\s]?(?:FRLS|FRHF|LSLTx|LS|HF|LTx|FR))*"
    r"(?:\s?\d+\s?[xхХX×]\s?\d+(?:[.,]\d+)?(?:\s?[xхХX×]\s?\d+(?:[.,]\d+)?)?)?)")


def _cable_key(s: str) -> str:
    s = s.upper().replace(" ", "").replace("×", "Х").replace("X", "Х").replace(",", ".")
    return norm_code(s).replace("NG", "НГ")


def bp08(ctx: Context) -> StageResult:
    res = StageResult()
    title = "Марки кабелей и оборудования"
    variants: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for s in ctx.sheets:
        for it in s.layer_items:
            for m in CABLE.finditer(it["text"]):
                raw = m.group(1).strip()
                if not re.search(r"\d", raw) or len(raw) < 6:
                    continue        # без сечения или слишком коротко — не марка кабеля
                variants[_cable_key(raw)][raw].append((s, (it["x0"], it["y0"], it["x1"], it["y1"])))
    for key, raws in variants.items():
        if len(raws) < 2:
            continue
        ranked = sorted(raws.items(), key=lambda kv: -len(kv[1]))
        minority = [h for _, hs in ranked[1:] for h in hs]
        res.drafts.append(Draft(
            "БП-08", f"написание|{key}", 2, POSSIBLE,
            f"Марка кабеля записана по-разному: " + "; ".join(f"«{r}» ({len(h)})" for r, h in ranked)
            + f". Основной вариант — «{ranked[0][0]}».",
            BASIS.format(code="БП-08", title=title), "Варианты различаются пробелами, регистром или видом символа «х».",
            [_place(s, r) for s, r in minority[:10]]))
    spec_sheets = [s for s in ctx.sheets if s.cls and "Спецификация" in s.cls.doc_types.value and s.layer_items]
    spec_text = "".join(_cable_key(it["text"]) for s in spec_sheets for it in s.layer_items)
    for t in ctx.xlsx:
        if any(v["role"] in ("наименование", "количество") for v in t["schema"].values()):
            spec_text += "".join(_cable_key(v) for _, cells in t["rows"] for v in cells.values())
    if spec_text:
        for key, raws in variants.items():
            hits = [h for hs in raws.values() for h in hs if not (h[0].cls and "Спецификация" in h[0].cls.doc_types.value)]
            if hits and key not in spec_text:
                res.drafts.append(Draft(
                    "БП-08", f"нет в спецификации|{key}", 2, POSSIBLE,
                    f"Марка кабеля «{next(iter(raws))}» есть на листах, но не найдена в спецификации.",
                    BASIS.format(code="БП-08", title=title), f"Найдено на {len({h[0].page_id for h in hits})} лист(ах).",
                    [_place(s, r) for s, r in hits[:6]]))
    else:
        res.status, res.message = "частично", "Спецификации с читаемым текстом нет — наличие марок в ней не проверялось."
    return res


# ------------------------------------------------------------------ БП-09
def bp09(ctx: Context) -> StageResult:
    res = StageResult()
    for s in ctx.sheets:
        if s.cls and s.cls.conflict and s.cls.source != "пользователь":
            res.drafts.append(Draft(
                "БП-09", f"{s.file_name}|{s.page_no}", 1, QUESTION,
                f"{_sheet_label(s).capitalize()}: {s.cls.conflict}. Уточните, к какому разделу относится лист.",
                BASIS.format(code="БП-09", title="Противоречие шифра и содержания листа"),
                "Признаки содержания: " + ", ".join(s.cls.section.evidence[:6]),
                [_place(s)]))
    return res


# ------------------------------------------------------------------ БП-10
def bp10(ctx: Context, doc_for) -> StageResult:
    """Графа организации (логотип) у первых листов по форме 3: сравнение изображений между листами."""
    import numpy as np

    res = StageResult()
    mm = 72 / 25.4
    vecs = []
    for s in ctx.sheets:
        if not s.has_stamp or s.form6 or "стадия" not in s.stamp["fields"]:
            continue
        doc = doc_for(s)
        info = doc.page_info(s.page_no - 1)
        W, H = info.width_pt, info.height_pt
        box = (W - 55 * mm, H - 20 * mm, W - 5 * mm, H - 5 * mm)      # графа организации: 50×15 мм у угла рамки
        img = doc.render_region(s.page_no - 1, 60, box).convert("L").resize((50, 15))
        a = (np.asarray(img) < 160).astype(float)
        if a.mean() < 0.01:
            continue
        rect = doc.from_pixels(s.page_no - 1, [(box[0] * 4, box[1] * 4, box[2] * 4, box[3] * 4)], 288)[0]
        vecs.append((s, a, rect))
    if len(vecs) < 3:
        return StageResult("пропущено", "Меньше трёх листов с основной надписью по форме 3 — сравнивать не с чем.")
    ref = np.median(np.stack([v for _, v, _ in vecs]), axis=0)
    odd = [(s, r) for s, v, r in vecs if np.abs(v - ref).mean() > 0.12]
    if odd and len(odd) <= len(vecs) // 3:
        res.drafts.append(Draft(
            "БП-10", "графа организации|" + ",".join(f"{s.file_name}:{s.page_no}" for s, _ in odd), 1, POSSIBLE,
            f"Графа организации (логотип) в основной надписи отличается от остальных листов: "
            f"{_list(_sheet_label(s) for s, _ in odd)}.",
            BASIS.format(code="БП-10", title="Графика штампов между листами"),
            f"Сравнение изображений графы на {len(vecs)} листах.",
            [_place(s, r, "зона штампа") for s, r in odd]))
    return res


CHECKS = [
    ("БП-01", "Структура PDF: число листов, форматы, текстовый слой", bp01),
    ("БП-02", "Заполненность основной надписи", bp02),
    ("БП-03", "Нумерация листов", bp03),
    ("БП-04", "Единообразие шифра, наименования объекта, формата дат", bp04),
    ("БП-05", "Шифры комплектов и ссылки на документы", bp05),
    ("БП-06", "Перечни обозначений: дубли и пропуски", bp06),
    ("БП-07", "Сверка состава PDF ↔ XLSX", bp07),
    ("БП-08", "Марки кабелей: единое написание и наличие в спецификации", bp08),
    ("БП-09", "Противоречие шифра и содержания листа", bp09),
    ("БП-10", "Графика штампов между листами", bp10),
]
