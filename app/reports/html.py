"""HTML-реестр по утверждённому шаблону (ФТ-28). Шаблон не меняется: программа только подставляет
CONFIG, GROUPS и SHOTS вместо плейсхолдеров; контрольная сумма шаблона проверяется перед выгрузкой.

В реестр идут замечания, кроме отклонённых. Критичность (если задана) — префиксом в тексте (В1, вариант «а»).
Лист — только номер листа; скриншот на каждое место (у каждого своя подпись).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from collections import Counter
from pathlib import Path

from PIL import Image

from .. import paths
from ..findings import store
from ..findings.model import GROUPS

MAX_SHOTS = 6                       # на одно замечание; остальные места — в Excel


class TemplateChanged(Exception):
    pass


def template_text() -> str:
    d = paths.bundle_dir() / "resources" / "templates"
    raw = (d / "template.html").read_bytes()
    want = (d / "template.sha256").read_text(encoding="ascii").strip()
    if hashlib.sha256(raw).hexdigest() != want:
        raise TemplateChanged("Исходный шаблон реестра изменён — выгрузка остановлена (шаблон менять нельзя без согласования).")
    return raw.decode("utf-8")


def _js(o) -> str:
    return json.dumps(o, ensure_ascii=False).replace("</", "<\\/")


def _jpeg(png: bytes) -> str:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    b = io.BytesIO()
    im.save(b, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode("ascii")


def _sheet_key(x: str):
    return (0, int(x)) if x.isdigit() else (1, x)


def build(obj, review_date: str | None = None) -> tuple[str, dict]:
    """Возвращает (html, сводка). Сводка: замечаний, не рассмотрено, отклонено (не выгружены)."""
    con = obj.con
    groups = {g: [] for g in GROUPS.values()}
    shots: dict[str, list] = {}
    stat = Counter()
    for f in store.findings(con):
        if f["decision"] == "отклонено":
            stat["rejected"] += 1
            continue
        stat["total"] += 1
        stat["undecided"] += f["decision"] == "не рассмотрено"
        pl = store.places(con, f["id"])
        sheets = sorted({p["sheet"] for p in pl if p["sheet"] and p["page_no"]}, key=_sheet_key)
        text = f["text"]
        if f["severity"] and f["severity"] != "Не задана":
            text = f"[{f['severity']}] {text}"
        proof = f["proof"]
        if f["basis"].startswith("Инструкция"):
            proof = f"{proof} Основание: {f['basis']}"
        fid = f"f{f['id']}"
        groups[GROUPS[f["grp"]]].append({"id": fid, "sheet": ", ".join(sheets), "text": text, "proof": proof,
                                         "status": f["cls"]})
        sl = []
        for p in pl:
            if p["shot"] and len(sl) < MAX_SHOTS:
                label = (f"Лист {p['sheet']}" if p["sheet"] else f"стр. {p['page_no']}") + f", {p['file_name']}"
                sl.append({"sheet": label, "src": _jpeg(bytes(p["shot"]))})
        if sl:
            shots[fid] = sl
    codes = Counter(r[0] for r in con.execute("SELECT value FROM stamp_field WHERE name='шифр' AND value<>''"))
    code = codes.most_common(1)[0][0] if codes else ""
    date = review_date or time.strftime("%d.%m.%Y")
    n_files = con.execute("SELECT COUNT(*) FROM file WHERE status='прочитан'").fetchone()[0]
    n_pages = con.execute("SELECT COUNT(*) FROM page").fetchone()[0]
    run = con.execute("SELECT * FROM check_run ORDER BY id DESC LIMIT 1").fetchone()
    manual = con.execute("SELECT COUNT(*) FROM check_stage WHERE run_id=? AND status IN ('ручная проверка','конфликт',"
                         "'частично','пропущено','ошибка')", (run["id"],)).fetchone()[0] if run else 0
    prefix = re.sub(r"[^\w.-]+", "_", code or obj.name).strip("_")
    cfg = {
        "code": code, "object": obj.name, "scope": f"файлов {n_files}, листов {n_pages}", "reviewDate": date,
        "title": "Реестр замечаний", "titleEngineer": "Замечания в работу",
        "tabTitle": f"Замечания {code or obj.name}", "filePrefix": f"Замечания_{prefix}",
        "storageKey": "ssdc-" + hashlib.sha1(f"{obj.folder}|{date}".encode()).hexdigest()[:12],
        "extra": (f"Подготовлено программой SS Drawing Checker. Этапов и требований с неполной проверкой: {manual} — "
                  "подробности на листе «Покрытие проверки» в Excel." if run else ""),
    }
    data = [{"group": g, "na": "Замечаний нет.", "items": its} for g, its in groups.items()]
    t = template_text()
    for ph in ("@@CONFIG@@", "@@GROUPS@@", "@@SHOTS@@"):
        if t.count(ph) != 1:
            raise TemplateChanged(f"В шаблоне нет места для {ph}")
    html = t.replace("@@CONFIG@@", _js(cfg)).replace("@@GROUPS@@", _js(data)).replace("@@SHOTS@@", _js(shots))
    return html, dict(stat)


def export(obj, folder: Path | None = None, review_date: str | None = None) -> tuple[Path, dict]:
    html, stat = build(obj, review_date)
    folder = Path(folder or Path(obj.folder) / "reports")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"Замечания_{time.strftime('%Y-%m-%d_%H%M')}.html"
    out.write_text(html, encoding="utf-8")
    return out, stat
