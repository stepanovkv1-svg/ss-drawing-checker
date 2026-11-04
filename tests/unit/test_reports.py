"""HTML-реестр по исходному шаблону (ФТ-28): шаблон не меняется, данные подставлены, отклонённые не выгружаются."""
import hashlib
import json
import re

import pytest

from app import paths
from app.data import ProjectObject
from app.findings import store
from app.findings.model import CONFIRMED, Draft, PlaceDraft
from app.reports import html


def _obj(tmp_path):
    o = ProjectObject.create(tmp_path / "о", "Аэропорт")
    rid = o.con.execute("INSERT INTO check_run(started_at, status, versions) VALUES ('t','готово','{}')").lastrowid
    store.apply_drafts(o.con, rid, [
        Draft("БП-02", "a", 1, CONFIRMED, "Нет фамилии </script><b>", "осн", "док", [PlaceDraft(None, "А.pdf", 1, "3")]),
        Draft("ВО-01 · И", "b", 2, CONFIRMED, "Стадия не та", "Инструкция «И», версия 1, п. 1", "док",
              [PlaceDraft(None, "А.pdf", 2, "4")], "Критично", "ВО"),
        Draft("БП-03", "c", 1, CONFIRMED, "Отклонённое", "осн", "док", [])])
    rej = next(f for f in store.findings(o.con) if f["text"] == "Отклонённое")
    store.update_finding(o.con, rej["id"], decision="отклонено")
    return o


def _data(page: str, name: str):
    return json.loads(re.search(rf"/\*{name}\*/(.*?)/\*END{name}\*/", page, re.S).group(1).replace("<\\/", "</"))


def test_html_from_template(tmp_path):
    o = _obj(tmp_path)
    page, stat = html.build(o, "04.11.2026")
    assert "@@" not in page and stat == {"total": 2, "undecided": 2, "rejected": 1}
    assert "</script><b>" not in page.split("/*GROUPS*/")[1].split("/*ENDGROUPS*/")[0]     # не ломает разметку
    groups = _data(page, "GROUPS")
    assert [g["group"] for g in groups] == ["Оформление", "Техническая часть", "Расстановка оборудования"]
    one, two = groups[0]["items"], groups[1]["items"]
    assert len(one) == 1 and one[0]["sheet"] == "3" and one[0]["status"] == CONFIRMED
    assert two[0]["text"] == "[Критично] Стадия не та" and "Основание: Инструкция" in two[0]["proof"]
    cfg = _data(page, "CONFIG")
    assert cfg["object"] == "Аэропорт" and cfg["reviewDate"] == "04.11.2026"
    # остальное в файле — байт в байт исходный шаблон
    src = (paths.bundle_dir() / "resources" / "templates" / "template.html").read_text(encoding="utf-8")
    head = src.split("@@CONFIG@@")[0]
    tail = src.split("@@SHOTS@@")[1]
    assert page.startswith(head) and page.endswith(tail)
    out, _ = html.export(o, tmp_path / "r")
    assert out.exists() and out.read_text(encoding="utf-8") == page.replace(cfg["storageKey"], cfg["storageKey"])


def test_template_checksum_guard(tmp_path, monkeypatch):
    d = tmp_path / "resources" / "templates"
    d.mkdir(parents=True)
    src = paths.bundle_dir() / "resources" / "templates"
    (d / "template.html").write_bytes((src / "template.html").read_bytes() + b" ")
    (d / "template.sha256").write_text((src / "template.sha256").read_text())
    monkeypatch.setattr(paths, "bundle_dir", lambda: tmp_path)
    with pytest.raises(html.TemplateChanged):
        html.template_text()


def test_template_matches_recorded_checksum():
    d = paths.bundle_dir() / "resources" / "templates"
    assert hashlib.sha256((d / "template.html").read_bytes()).hexdigest() == (d / "template.sha256").read_text().strip()
