"""Конфликты требований разных инструкций (ФТ-16, Р77, С3).

Конфликт — два утверждённых требования разных включённых инструкций об одном и том же предмете
(поле штампа, текст, источник значения) с несовместимыми параметрами и пересекающейся областью.
До решения пользователя оба требования не применяются. Решение хранится для пары версий.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..data import schema
from ..documents.textnorm import norm_text
from .library import Instruction
from .model import Requirement

BOTH = "оба"
NEITHER = "ни одно"


@dataclass
class Conflict:
    a: tuple[Instruction, Requirement]
    b: tuple[Instruction, Requirement]
    why: str

    @property
    def key(self) -> str:
        parts = sorted(f"{i.id}:{i.ver}:{r.uid}" for i, r in (self.a, self.b))
        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def label(self, side) -> str:
        i, r = side
        return f"«{i.name}» v{i.ver}: {r.fragment}"


def _subject(r: Requirement) -> str | None:
    p = r.params
    if r.op == "ВО-01" and p.get("mode") in ("список", "шаблон"):
        return f"поле|{p.get('field')}"
    if r.op == "ВО-02":
        return f"текст|{norm_text(p.get('text', ''))}|{p.get('zone')}"
    if r.op == "ВО-06":
        return f"значение|{p.get('source')}"
    if r.op in ("ВО-04", "ВО-05"):
        return f"{r.op}|{p.get('pattern')}"
    return None


def _incompatible(x: Requirement, y: Requirement) -> str | None:
    px, py = x.params, y.params
    if x.op == "ВО-01":
        vx = set(map(str.upper, px.get("values") or [])) or {px.get("pattern")}
        vy = set(map(str.upper, py.get("values") or [])) or {py.get("pattern")}
        if not vx & vy:
            return f"разные допустимые значения графы «{px.get('field')}»"
    if x.op == "ВО-02" and bool(px.get("present")) != bool(py.get("present")):
        return f"одна требует текст «{px.get('text')}», другая его запрещает"
    if x.op == "ВО-06":
        lo = max(px.get("min", float("-inf")), py.get("min", float("-inf")))
        hi = min(px.get("max", float("inf")), py.get("max", float("inf")))
        if lo > hi:
            return f"диапазоны для «{px.get('source')}» не пересекаются"
        if px.get("values") and py.get("values") and not set(px["values"]) & set(py["values"]):
            return f"списки значений для «{px.get('source')}» не пересекаются"
    if x.op == "ВО-05" and px.get("tolerance") != py.get("tolerance"):
        return f"разный допуск по количеству для «{px.get('pattern')}»"
    return None


def find_conflicts(instr: list[Instruction]) -> list[Conflict]:
    items = [(i, r) for i in instr for r in i.requirements if r.runnable]
    out = []
    for n, (ia, ra) in enumerate(items):
        sa = _subject(ra)
        if not sa:
            continue
        for ib, rb in items[n + 1:]:
            if ib.id == ia.id or rb.op != ra.op or _subject(rb) != sa:
                continue
            if not ra.scope.merged(ia.scope).overlaps(rb.scope.merged(ib.scope)):
                continue
            why = _incompatible(ra, rb)
            if why:
                out.append(Conflict((ia, ra), (ib, rb), why))
    return out


def decisions(con) -> dict[str, str]:
    return {r[0]: r[1] for r in con.execute("SELECT key, choice FROM conflict_decision")}


def decide(con, c: Conflict, choice: str) -> None:
    """choice: uid выбранного требования (применяется оно), BOTH или NEITHER; '' — снять решение."""
    with con:
        if choice:
            con.execute("INSERT OR REPLACE INTO conflict_decision(key, choice, decided_at) VALUES (?,?,?)",
                        (c.key, choice, schema.now()))
        else:
            con.execute("DELETE FROM conflict_decision WHERE key=?", (c.key,))


def blocked(conflicts: list[Conflict], dec: dict[str, str]) -> dict[tuple[int, str], str]:
    """(id инструкции, uid требования) → причина, по которой требование не применяется."""
    out: dict[tuple[int, str], str] = {}
    for c in conflicts:
        ch = dec.get(c.key)
        for side, other in ((c.a, c.b), (c.b, c.a)):
            i, r = side
            if ch is None:
                out[(i.id, r.uid)] = f"конфликт с «{other[0].name}» не решён: {c.why}"
            elif ch == NEITHER or (ch != BOTH and ch != r.uid):
                out[(i.id, r.uid)] = f"по решению пользователя применяется другое требование ({c.why})"
    return out


def describe(c: Conflict) -> str:
    return re.sub(r"\s+", " ", f"{c.why}: {c.label(c.a)} ↔ {c.label(c.b)}")
