"""Классификация листа (ТЗ 2.3, 4.5): раздельно марка комплекта (по шифру), фактический раздел и подсистемы
(по содержанию), типы документа. У каждой характеристики — признаки-доказательства, уверенность
(уверенно / спорно / не определено) и противоречия. Веса и пороги — в словаре с версией
(resources/dictionaries/classify.json). Решение пользователя запоминается по ключу листа и важнее авто.

Классификация замечаний не создаёт. Единственное, что она отмечает для проверок, — противоречие
«марка уверенно указывает на один раздел, а содержание уверенно на другой» (флаг conflict).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from .. import paths
from .textnorm import norm_code

SURE, DOUBT, UNKNOWN = "уверенно", "спорно", "не определено"


@dataclass
class Feature:
    term: str
    count: int
    weight: float
    where: str            # «наименование» | «текст листа» | «шифр»


@dataclass
class Characteristic:
    value: list[str] = field(default_factory=list)
    confidence: str = UNKNOWN
    evidence: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)


@dataclass
class Classification:
    mark: Characteristic
    section: Characteristic
    subsystems: Characteristic
    doc_types: Characteristic
    conflict: str = ""                 # противоречие «шифр ↔ содержание» (для проверок шага 4)
    source: str = "авто"
    dict_version: str = ""

    @property
    def level(self) -> str:
        """Итог по листу для экрана: худшая из уверенностей."""
        order = {SURE: 0, DOUBT: 1, UNKNOWN: 2}
        return max((self.mark.confidence, self.section.confidence, self.doc_types.confidence), key=order.get)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Classification":
        return cls(**{k: Characteristic(**v) if isinstance(v, dict) else v for k, v in d.items()})


@lru_cache(maxsize=1)
def dictionary() -> dict:
    p = paths.bundle_dir() / "resources" / "dictionaries" / "classify.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["_features"] = {s: [(re.compile(t, re.I), t, w) for t, w in feats] for s, feats in d["features"].items()}
    d["_types"] = [(name, [re.compile(p, re.I) for p in pats], suffixes) for name, pats, suffixes in d["doc_types"]]
    return d


def decision_key(file_sha: str, page_no: int, code: str, sheet: str, title: str) -> str:
    """Ключ решения пользователя (ТЗ 4.5): документ, шифр, номер и наименование листа, контрольная сумма."""
    raw = "|".join([file_sha, str(page_no), norm_code(code or ""), sheet or "", (title or "").strip().lower()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_code(code: str) -> tuple[list[str], str]:
    """Шифр → (кандидаты марки, суффикс документа). «1497-Р-00-СПД.СО» → (["СПД"], "СО")."""
    d = dictionary()
    code = norm_code(code or "")
    suffix = ""
    m = re.search(r"\.([А-ЯЁ]{1,4})$", code)
    if m:
        suffix, code = m.group(1), code[:m.start()]
    tokens = [t for t in re.split(r"[-\s]+", code) if t]
    letters = [t for t in tokens if re.fullmatch(r"[А-ЯЁ]{1,6}", t) and t not in d["stage_tokens"]]
    return letters, suffix


def _mark(code: str, fallback_code: str) -> Characteristic:
    d = dictionary()
    c = Characteristic()
    used = code or fallback_code
    if not used:
        c.evidence.append("шифр не найден")
        return c
    letters, suffix = parse_code(used)
    known = [t for t in letters if t in d["marks"]]
    if known:
        c.value = [known[-1]]
        c.confidence = SURE if code else DOUBT
        c.evidence.append(f"шифр «{used}» → марка {known[-1]}")
        if not code:
            c.evidence.append("шифр взят с других листов документа")
        if len(set(known)) > 1:
            c.contradictions.append("в шифре несколько марок: " + ", ".join(known))
            c.confidence = DOUBT
    elif letters:
        c.value = [letters[-1]]
        c.confidence = DOUBT
        c.evidence.append(f"марка {letters[-1]} нет в словаре")
    else:
        c.evidence.append(f"в шифре «{used}» марка не выделена")
    return c


def _label(term: str) -> str:
    """Признак словаря для показа человеку: «точк[аи] доступа» → «точка доступа»."""
    t = re.sub(r"\((?:[^()]*)\)\?", "", term)
    t = re.sub(r"\[([^\]])[^\]]*\]", r"\1", t)
    t = t.replace("\\b", "").replace("\\w*", "…").replace("?", "").replace("\\", "")
    return re.sub(r"\s+", " ", t).strip()


def _score(text: str, title: str) -> tuple[dict[str, float], dict[str, list[Feature]]]:
    d = dictionary()
    scores: dict[str, float] = {}
    feats: dict[str, list[Feature]] = {}
    for sub, pats in d["_features"].items():
        for rx, term, w in pats:
            n_title = len(rx.findall(title))
            n_text = len(rx.findall(text))
            if not (n_title or n_text):
                continue
            # в наименовании листа признак весит вдвое; повторы — по логарифму, чтобы один частый признак
            # не перевешивал разнообразие признаков
            s = w * (2 if n_title else 0) + (w * (1 + math.log(n_text)) if n_text else 0)
            scores[sub] = scores.get(sub, 0) + s
            feats.setdefault(sub, []).append(Feature(_label(term), n_title + n_text, w,
                                                     "наименование" if n_title else "текст листа"))
    return scores, feats


def _section(scores: dict[str, float], feats: dict[str, list[Feature]], mark: Characteristic,
             text_present: bool) -> tuple[Characteristic, Characteristic, str]:
    d = dictionary()
    th = d["thresholds"]
    subs = d["subsystems"]
    by_section: dict[str, float] = {}
    n_feats: Counter = Counter()
    for sub, s in scores.items():
        sec = subs[sub]["section"]
        by_section[sec] = by_section.get(sec, 0) + s
        n_feats[sec] += len(feats[sub])
    sec_c, sub_c = Characteristic(), Characteristic()
    mark_sec = d["marks"].get(mark.value[0], {}).get("section") if mark.value else None
    ranked = sorted(by_section.items(), key=lambda x: -x[1])
    conflict = ""
    if ranked and ranked[0][1] >= th["min_score"]:
        best, best_s = ranked[0]
        second_s = ranked[1][1] if len(ranked) > 1 else 0
        sec_c.value = [best]
        sec_c.evidence = [f"{f.term} ×{f.count} ({f.where})" for sub in scores if subs[sub]["section"] == best
                          for f in feats[sub]]
        strong = best_s >= th["confident_score"] and n_feats[best] >= th["confident_features"]
        clear = second_s <= th["second_ratio"] * best_s
        if not clear:
            other = ranked[1][0]
            sec_c.contradictions.append(
                f"есть признаки раздела {other}: " + ", ".join(f.term for sub in scores if subs[sub]["section"] == other
                                                              for f in feats[sub]))
        if strong and clear:
            sec_c.confidence = SURE
        elif mark_sec == best and clear:
            # слабое содержание, но совпадает с маркой комплекта
            sec_c.confidence = SURE
            sec_c.evidence.append(f"совпадает с маркой {mark.value[0]}")
        else:
            sec_c.confidence = DOUBT
            if n_feats[best] < th["confident_features"]:
                sec_c.evidence.append("один признак не даёт уверенности")
        if mark_sec and mark_sec != best and mark.confidence == SURE and sec_c.confidence == SURE:
            conflict = f"Марка {mark.value[0]} указывает на раздел {mark_sec}, а содержание листа — на раздел {best}"
            sec_c.contradictions.append(conflict)
        # подсистемы: кандидаты, не распространяются на весь лист без подтверждения (ФТ-11)
        cands = sorted((s, sub) for sub, s in scores.items() if s >= th["subsystem_score"])
        sub_c.value = [sub for s, sub in reversed(cands)]
        sub_c.evidence = [f"{sub}: " + ", ".join(f.term for f in feats[sub]) for sub in sub_c.value]
        sub_c.confidence = DOUBT if sub_c.value else UNKNOWN
    elif mark_sec:
        sec_c.value = [mark_sec]
        sec_c.confidence = DOUBT
        sec_c.evidence = [f"только по марке {mark.value[0]}" + ("" if text_present else "; текст листа не читается")]
        msub = d["marks"][mark.value[0]].get("subsystem")
        if msub:
            sub_c.value, sub_c.confidence = [msub], DOUBT
            sub_c.evidence = [f"{msub}: по марке"]
    else:
        sec_c.evidence = ["признаков раздела нет" + ("" if text_present else "; текст листа не читается")]
    return sec_c, sub_c, conflict


def _types(title: str, suffix: str, has_stamp: bool, text: str) -> Characteristic:
    c = Characteristic()
    for name, pats, sufs in dictionary()["_types"]:
        if name.startswith("Обложка"):
            if not has_stamp and any(p.search(text[:2000]) for p in pats + [re.compile("рабоч\\w* документац", re.I)]):
                c.value.append(name)
                c.evidence.append("нет основной надписи, есть признаки обложки или титульного листа")
            continue
        if suffix and suffix in sufs:
            c.value.append(name)
            c.evidence.append(f"суффикс шифра .{suffix}")
            continue
        if name == "Схема (прочее)" and c.value:
            continue
        hit = next((p for p in pats if p.search(title)), None)
        if hit:
            c.value.append(name)
            c.evidence.append(f"наименование «{title}»")
    c.value = list(dict.fromkeys(c.value))
    if c.value:
        c.confidence = SURE if title or suffix else DOUBT
    else:
        c.evidence.append("наименование листа не найдено" if not title else f"тип по наименованию «{title}» не определён")
    return c


def classify_page(stamp_fields: dict[str, str], text: str, has_stamp: bool, fallback_code: str = "",
                  text_present: bool = True) -> Classification:
    d = dictionary()
    code = stamp_fields.get("шифр", "")
    title = stamp_fields.get("наименование листа", "")
    mark = _mark(code, fallback_code)
    _, suffix = parse_code(code or fallback_code)
    scores, feats = _score(text, title)
    section, subs, conflict = _section(scores, feats, mark, text_present)
    types = _types(title, suffix, has_stamp, text)
    if types.value == ["Обложка / титульный лист"]:
        # у обложки раздел и марка берутся с листов документа, вопрос к пользователю не нужен
        types.confidence = SURE if mark.value else DOUBT
    return Classification(mark, section, subs, types, conflict, "авто", d["version"])


def classify_file(obj, file_id: int) -> dict[str, int]:
    """Классифицирует все листы PDF-файла и сохраняет результат. Решения пользователя применяются по ключу."""
    f = next(x for x in obj.files() if x.id == file_id)
    pages = obj.pages(file_id)
    stamps = {p["id"]: obj.stamp(p["id"]) for p in pages}
    codes = Counter(st["fields"]["шифр"]["value"] for st in stamps.values()
                    if st and st["recognized"] and "шифр" in st["fields"])
    fallback = codes.most_common(1)[0][0] if codes else ""
    stats: Counter = Counter()
    for p in pages:
        st = stamps[p["id"]]
        fields = {k: v["value"] for k, v in st["fields"].items()} if st else {}
        items = obj.text_items(p["id"])
        text = " ".join(i["text"] for i in items)
        cls = classify_page(fields, text, bool(st and st["recognized"]), fallback, text_present=bool(items))
        key = decision_key(f.sha256, p["page_no"], fields.get("шифр", ""), fields.get("лист", ""),
                           fields.get("наименование листа", ""))
        dec = obj.decision(key)
        if dec:
            for name, value in dec.items():
                ch = getattr(cls, name)
                if ch.value != value:
                    ch.evidence = ["задано пользователем"]
                ch.value, ch.confidence, ch.contradictions = value, SURE, []
            cls.source, cls.conflict = "пользователь", ""
        obj.save_classification(p["id"], cls, key)
        stats[cls.level] += 1
    return dict(stats)
