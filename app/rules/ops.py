"""Выполнение требований инструкций встроенными операциями ВО-01…ВО-07 (ТЗ 5.2, ФТ-19).

Требование выполняется только в своей области (раздел, подсистема, марка, тип документа).
Надёжность как у базовых проверок (П1-20): штамп OCR без подтверждения и текст OCR не дают замечаний —
такие листы уходят в ручную проверку и видны в покрытии. Текст замечания — из шаблона требования
со значениями только из извлечения (Р75). Критичность — из требования (ФТ-23).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..documents.textnorm import norm_code, norm_text
from ..findings.model import CONFIRMED, POSSIBLE, Draft, PlaceDraft
from ..requirements.library import Instruction
from ..requirements.model import NOT_READY, OPS, Requirement, Scope
from .base_checks import _list, _place, _sheets_text
from .context import Context, Sheet


@dataclass
class ReqResult:
    status: str = "выполнено"          # выполнено | частично | пропущено | ручная проверка | конфликт
    message: str = ""
    drafts: list[Draft] = field(default_factory=list)


class _Fmt(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def in_scope(s: Sheet, scope: Scope) -> bool | None:
    """True/False; None — лист не классифицирован, а область задана."""
    if scope.empty():
        return True
    if not s.cls:
        return None
    pairs = [(scope.sections, s.cls.section.value), (scope.subsystems, s.cls.subsystems.value),
             (scope.marks, s.cls.mark.value), (scope.doc_types, s.cls.doc_types.value)]
    return all(not want or set(want) & set(have) for want, have in pairs)


def table_type(t: dict) -> str:
    name = norm_text(f"{t['file']} {t['table']['sheet']}")
    roles = {v["role"] for v in t["schema"].values()}
    if "журнал" in name or {"откуда", "куда"} & roles:
        return "Кабельный журнал"
    return "Спецификация"


def _tables(ctx: Context, kind: str) -> list[dict]:
    return [t for t in ctx.xlsx if kind in ("любая", "") or table_type(t) == kind]


def _col(t: dict, column: str) -> str | None:
    c = norm_text(column)
    for letter, v in t["schema"].items():
        if v["role"] == column or norm_text(v.get("header") or "") == c or c and c in norm_text(v.get("header") or ""):
            return letter
    return None


def _num(v) -> float | None:
    m = re.fullmatch(r"\s*(-?\d+(?:[.,]\d+)?)\s*\D{0,4}\s*", str(v or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def _rect(it):
    return (it["x0"], it["y0"], it["x1"], it["y1"])


def _inside(r, zone):
    return zone and r[0] >= zone[0] - 2 and r[1] >= zone[1] - 2 and r[2] <= zone[2] + 2 and r[3] <= zone[3] + 2


def _cell(t, letter, row) -> PlaceDraft:
    return PlaceDraft(None, t["file"], None, t["table"]["sheet"], None, "XLSX", f"{letter}{row}")


class Runner:
    def __init__(self, ctx: Context, ins: Instruction, n: int, req: Requirement):
        self.ctx, self.ins, self.n, self.req = ctx, ins, n, req
        self.scope = req.scope.merged(ins.scope)
        self.res = ReqResult()
        self.manual = 0
        self.unclassified = 0

    # ---------------------------------------------------------------- общее
    def sheets(self) -> list[Sheet]:
        out = []
        for s in self.ctx.sheets:
            ok = in_scope(s, self.scope)
            if ok is None:
                self.unclassified += 1
            elif ok:
                out.append(s)
        return out

    def basis(self) -> str:
        b = f"Инструкция «{self.ins.name}», версия {self.ins.ver}, п. {self.n}: «{self.req.fragment}»"
        return b + (f"; {self.req.norm_ref}" if self.req.norm_ref else "")

    def draft(self, key: str, cls: str, where: str, what: str, proof: str, places: list, **extra) -> None:
        tpl = self.req.template or "{где}: {что}."
        text = tpl.format_map(_Fmt(где=where, что=what, требование=self.req.fragment, **extra))
        text = text[0].upper() + text[1:] if text else text
        d = Draft(f"{self.req.op} · {self.ins.name}", f"{self.ins.id}|{self.req.uid}|{key}", self.req.grp, cls, text,
                  self.basis(), proof, places, self.req.severity, "ВО")
        self.res.drafts.append(d)

    def finish(self) -> ReqResult:
        notes = []
        if self.manual:
            self.res.status = "частично"
            notes.append(f"ручная проверка: {self.manual} лист(ов) — штамп или текст прочитан OCR")
        if self.unclassified:
            notes.append(f"не классифицировано и не проверено: {self.unclassified} лист(ов)")
            self.res.status = "частично"
        if notes:
            self.res.message = "; ".join(filter(None, [self.res.message] + notes))
        return self.res

    # ---------------------------------------------------------------- операции
    def vo01(self):
        p = self.req.params
        f = p.get("field", "")
        bad: dict[str, list] = defaultdict(list)
        sheets = [s for s in self.sheets() if s.has_stamp]
        if not sheets:
            return ReqResult("пропущено", "в области нет листов со штампом")
        for s in sheets:
            if f.startswith("подпись:"):
                if not s.stamp_reliable:
                    self.manual += 1
                    continue
                role = norm_text(f.split(":", 1)[1]).rstrip(".")
                sig = next((x for x in s.stamp["signatures"] if norm_text(x["role"]).startswith(role[:4])), None)
                what = p.get("what", "фамилия")
                val = (sig or {}).get("surname" if what == "фамилия" else "date", "").strip()
                if not val:
                    bad["пусто"].append(s)
                continue
            v = s.field(f)
            if v is None:
                self.manual += 1
                continue
            v = v.strip()
            if not v:
                bad["пусто"].append(s)
            elif p.get("mode") == "список" and norm_code(v) not in {norm_code(x) for x in p.get("values", [])}:
                bad[v].append(s)
            elif p.get("mode") == "шаблон" and p.get("pattern"):
                try:
                    ok = re.fullmatch(p["pattern"], v, re.I)
                except re.error:
                    ok = norm_code(v) == norm_code(p["pattern"])
                if not ok:
                    bad[v].append(s)
        label = f.split(":", 1)[1] if f.startswith("подпись:") else f
        for v, ss in bad.items():
            if v == "пусто":
                what = (f"в строке «{label}» не указана {p.get('what', 'фамилия')}" if f.startswith("подпись:")
                        else f"в основной надписи не заполнена графа «{label}»")
                cls = CONFIRMED if all(s.stamp["confirmed"] for s in ss) or f.startswith("подпись:") else POSSIBLE
            else:
                allowed = ", ".join(f"«{x}»" for x in p.get("values", [])) or f"шаблон «{p.get('pattern')}»"
                what = f"в графе «{label}» указано «{v}», допускается {allowed}"
                cls = CONFIRMED
            rect_of = (lambda s: s.stamp_zone) if f.startswith("подпись:") else (lambda s: s.field_rect(f) or s.stamp_zone)
            self.draft(v, cls, _sheets_text(ss).capitalize(), what, f"Графа «{label}»: «{'' if v == 'пусто' else v}» "
                       f"на {len(ss)} лист(ах).", [_place(x, rect_of(x), "штамп") for x in ss], поле=label, значение=v)

    def vo02(self):
        p = self.req.params
        want, zone = norm_text(p.get("text", "")), p.get("zone", "весь лист")
        if not want:
            return ReqResult("ручная проверка", "не задан текст")
        absent, present = [], []
        sheets = self.sheets()
        if not sheets:
            return ReqResult("пропущено", "в области нет листов")
        for s in sheets:
            items = s.layer_items
            if not items:
                self.manual += 1
                continue
            if zone != "весь лист":
                z = s.stamp_zone
                items = [it for it in items if bool(_inside(_rect(it), z)) == (zone == "штамп")]
            if p.get("regex"):
                try:
                    hits = [it for it in items if re.search(p["text"], it["text"], re.I)]
                except re.error:
                    hits = []
                found = bool(hits)
            else:
                hits = [it for it in items if want in norm_text(it["text"])]
                found = bool(hits) or want in norm_text(" ".join(it["text"] for it in items))
            if p.get("present", True) and not found:
                absent.append(s)
            elif not p.get("present", True) and found:
                present.append((s, hits))
        if absent:
            self.draft("нет", POSSIBLE, _sheets_text(absent).capitalize(), f"нет текста «{p['text']}»",
                       f"Текст не найден в текстовом слое ({zone}). Он может быть нарисован линиями — проверьте.",
                       [_place(s) for s in absent])
        if present:
            self.draft("есть", CONFIRMED, _sheets_text([s for s, _ in present]).capitalize(),
                       f"есть недопустимый текст «{p['text']}»", "Найдено в текстовом слое.",
                       [_place(s, _rect(h[0]) if h else None) for s, h in present])

    def vo03(self):
        p = self.req.params
        tables = _tables(self.ctx, p.get("table", "любая"))
        if not tables:
            return ReqResult("пропущено", f"нет таблиц XLSX типа «{p.get('table')}» (таблицы, нарисованные в PDF, "
                                          "на этом шаге не разбираются)")
        column = p.get("column", "")
        for t in tables:
            letter = _col(t, column)
            where = f"Таблица «{t['table']['sheet']}» файла {t['file']}"
            if not letter:
                self.draft(f"нет столбца|{t['file']}|{t['table']['sheet']}", CONFIRMED, where,
                           f"нет столбца «{column}»", "Заголовки: " + _list(v.get("header") or k for k, v in t["schema"].items()),
                           [PlaceDraft(None, t["file"], None, t["table"]["sheet"], None, "XLSX", f"строка {t['table']['header_row']}")])
                continue
            if p.get("mode") == "заполнено":
                empty = [rn for rn, cells in t["rows"]
                         if not str(cells.get(letter, "")).strip() and sum(1 for v in cells.values() if str(v).strip()) >= 2]
                if empty:
                    self.draft(f"пусто|{t['file']}|{t['table']['sheet']}", CONFIRMED, where,
                               f"не заполнен столбец «{column}» в строках {_list(empty)}",
                               f"Столбец {letter}, пустых ячеек: {len(empty)}", [_cell(t, letter, r) for r in empty[:20]])

    def _target_text(self, kind: str) -> tuple[set[str], int]:
        vals, n = set(), 0
        for t in _tables(self.ctx, kind):
            n += 1
            for _, cells in t["rows"]:
                for v in cells.values():
                    vals.update(norm_code(x) for x in re.split(r"[\s,;()]+", str(v)) if x)
        for s in self.ctx.sheets:
            if s.cls and kind in s.cls.doc_types.value and s.layer_items:
                n += 1
                for it in s.layer_items:
                    vals.update(norm_code(x) for x in re.split(r"[\s,;()]+", it["text"]) if x)
        return vals, n

    def _designations(self, pattern: str, exclude_kind: str = "") -> dict[str, list]:
        found: dict[str, list] = defaultdict(list)
        rx = re.compile(pattern, re.I)
        for s in self.sheets():
            if exclude_kind and s.cls and exclude_kind in s.cls.doc_types.value:
                continue
            if not s.layer_items:
                self.manual += 1
                continue
            for it in s.layer_items:
                for tok in re.split(r"[\s,;()]+", it["text"]):
                    t = norm_code(tok)
                    if t and rx.fullmatch(t):
                        found[t].append((s, _rect(it)))
        return found

    def vo04(self):
        p = self.req.params
        try:
            re.compile(p.get("pattern", ""))
        except re.error:
            return ReqResult("ручная проверка", "шаблон обозначения задан с ошибкой")
        if not p.get("pattern"):
            return ReqResult("ручная проверка", "не задан шаблон обозначения")
        target, n = self._target_text(p.get("to", "Спецификация"))
        if not n:
            return ReqResult("пропущено", f"нет документа «{p.get('to')}» с читаемым текстом")
        found = self._designations(p["pattern"], p.get("to", ""))
        for d, hits in sorted(found.items()):
            if d not in target:
                self.draft(f"нет|{d}", POSSIBLE, _sheets_text(list({h[0].page_id: h[0] for h in hits}.values())).capitalize(),
                           f"обозначение {d} не найдено в документе «{p.get('to')}»",
                           f"Найдено на листах: {len({h[0].page_id for h in hits})}; в «{p.get('to')}» его нет.",
                           [_place(s, r) for s, r in hits[:6]], значение=d)

    def vo05(self):
        p = self.req.params
        if not p.get("pattern"):
            return ReqResult("ручная проверка", "не задан шаблон обозначения")
        rx = re.compile(p["pattern"], re.I)
        found = self._designations(p["pattern"], "Спецификация")
        plan = len(found)
        spec, rows = 0.0, []
        for t in _tables(self.ctx, "Спецификация"):
            dcol = next((c for c, v in t["schema"].items() if v["role"] in ("обозначение", "позиция", "тип")), None)
            qcol = next((c for c, v in t["schema"].items() if v["role"] == "количество"), None)
            if not dcol or not qcol:
                continue
            for rn, cells in t["rows"]:
                if rx.fullmatch(norm_code(str(cells.get(dcol, "")))) or rx.search(norm_code(str(cells.get(dcol, "")))):
                    q = _num(cells.get(qcol))
                    if q is not None:
                        spec += q
                        rows.append((t, qcol, rn))
        if not rows:
            return ReqResult("пропущено", "в спецификации XLSX нет строк с таким обозначением и количеством")
        if not plan:
            return ReqResult("ручная проверка", "на листах в области обозначения не найдены в текстовом слое")
        if abs(plan - spec) > float(p.get("tolerance", 0) or 0):
            hits = [h for hs in found.values() for h in hs]
            self.draft("количество", POSSIBLE, "Планы и спецификация",
                       f"по планам {plan:g} обозначений «{p['pattern']}», в спецификации {spec:g}",
                       "Считаются разные обозначения в текстовом слое листов; в спецификации — сумма количества.",
                       [_place(s, r) for s, r in hits[:5]] + [_cell(t, c, r) for t, c, r in rows[:5]])

    def vo06(self):
        p = self.req.params
        src = p.get("source", "")
        lo, hi, vals = p.get("min"), p.get("max"), p.get("values")

        def bad(v) -> bool:
            if vals:
                return norm_code(str(v)) not in {norm_code(x) for x in vals}
            x = _num(v)
            return x is not None and (lo is not None and x < lo or hi is not None and x > hi)

        limit = ", ".join(f"«{x}»" for x in vals) if vals else (
            f"от {lo:g} до {hi:g}" if lo is not None and hi is not None else
            f"не менее {lo:g}" if lo is not None else f"не более {hi:g}")
        if src.startswith("поле штампа:"):
            f = src.split(":", 1)[1]
            by_v: dict[str, list] = defaultdict(list)
            for s in self.sheets():
                v = s.field(f) if s.has_stamp else None
                if v is None:
                    self.manual += s.has_stamp
                    continue
                if v and bad(v):
                    by_v[v].append(s)
            for v, ss in by_v.items():
                self.draft(f"{f}|{v}", CONFIRMED, _sheets_text(ss).capitalize(),
                           f"в графе «{f}» значение «{v}» вне допустимого ({limit})", f"Графа «{f}»: «{v}»",
                           [_place(s, s.field_rect(f), "штамп") for s in ss], значение=v)
        else:
            role = src.split(":", 1)[-1]
            tables = [t for t in self.ctx.xlsx if _col(t, role)]
            if not tables:
                return ReqResult("пропущено", f"нет таблиц XLSX со столбцом «{role}»")
            for t in tables:
                letter = _col(t, role)
                out = [(rn, cells[letter]) for rn, cells in t["rows"] if str(cells.get(letter, "")).strip() and bad(cells[letter])]
                if out:
                    self.draft(f"{t['file']}|{t['table']['sheet']}|{role}", CONFIRMED,
                               f"Таблица «{t['table']['sheet']}» файла {t['file']}",
                               f"в столбце «{role}» значения вне допустимого ({limit}): "
                               + _list(f"строка {rn} — {v}" for rn, v in out),
                               f"Столбец {letter}", [_cell(t, letter, rn) for rn, _ in out[:20]])

    def vo07(self):
        p = self.req.params
        signs = [norm_text(x) for x in p.get("signs", []) if x.strip()]
        if not signs:
            return ReqResult("ручная проверка", "не заданы текстовые признаки элемента")
        sheets = self.sheets()
        if not sheets:
            return ReqResult("пропущено", "в области нет листов")
        missing = []
        for s in sheets:
            if not s.layer_items:
                self.manual += 1
                continue
            text = norm_text(" ".join(it["text"] for it in s.layer_items))
            if not any(x in text for x in signs):
                missing.append(s)
        if missing:
            self.draft("нет", POSSIBLE, _sheets_text(missing).capitalize(), f"не найден элемент «{p.get('element')}»",
                       "Искались надписи: " + ", ".join(f"«{x}»" for x in p.get("signs", [])) +
                       ". Элемент может быть нарисован без текста — проверьте.", [_place(s) for s in missing])

    def run(self) -> ReqResult:
        fn = getattr(self, "vo0" + self.req.op[-1], None) if self.req.op in OPS else None
        if self.req.op in NOT_READY:
            return ReqResult("ручная проверка", NOT_READY[self.req.op])
        if not fn:
            return ReqResult("ручная проверка", "операция не задана")
        early = fn()
        if isinstance(early, ReqResult):
            return early
        return self.finish()


def run_instructions(ctx: Context, instr: list[Instruction], blocked: dict) -> list[tuple[str, str, ReqResult]]:
    """[(код, заголовок этапа, результат)] — по строке покрытия на каждое требование."""
    out = []
    for ins in instr:
        for n, req in enumerate(ins.requirements, 1):
            code = req.op or "—"
            title = f"«{ins.name}» v{ins.ver}, п. {n}: {req.fragment[:120]}"
            if (ins.id, req.uid) in blocked:
                out.append((code, title, ReqResult("конфликт", blocked[(ins.id, req.uid)])))
            elif not req.runnable:
                why = req.note or ("требование не утверждено" if req.op else "не разобрано")
                out.append((code, title, ReqResult("ручная проверка", f"{req.status}: {why}")))
            else:
                try:
                    res = Runner(ctx, ins, n, req).run()
                except Exception as e:  # noqa: BLE001 — ошибка требования видна в покрытии
                    res = ReqResult("ошибка", f"{type(e).__name__}: {e}")
                out.append((code, title, res))
    return out
