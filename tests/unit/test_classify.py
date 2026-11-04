import pytest

from app.data import ProjectObject
from app.documents.classify import DOUBT, SURE, UNKNOWN, classify_file, classify_page, parse_code
from app.documents.intake import add_files


def test_parse_code():
    assert parse_code("1497-Р-00-СПД.СО") == (["СПД"], "СО")
    assert parse_code("АГА02-03226-000-НСС-02-РД") == (["НСС"], "")
    assert parse_code("") == ([], "")


def test_confident_section_from_content():
    c = classify_page({"шифр": "1-Р-СПД", "наименование листа": "Структурная схема ЛВС"},
                      "Коммутатор доступа, маршрутизатор, патч-корд, точка доступа Wi-Fi", True)
    assert c.mark.value == ["СПД"] and c.mark.confidence == SURE
    assert c.section.value == ["СС"] and c.section.confidence == SURE
    assert "СПД" in c.subsystems.value
    assert c.doc_types.value == ["Структурная схема"] and c.level == SURE
    assert any("коммутатор" in e for e in c.section.evidence)
    assert not c.conflict


def test_one_feature_is_not_confident():
    c = classify_page({"шифр": "1-Р-01", "наименование листа": "План"}, "камера", True)
    assert c.section.confidence != SURE


def test_mark_vs_content_conflict():
    c = classify_page({"шифр": "1-Р-ПС", "наименование листа": "План расположения видеокамер"},
                      "Видеонаблюдение: IP-камера, видеорегистратор, видеокамера купольная", True)
    assert c.section.value == ["КТСБ"] and "ПС" in c.conflict
    assert c.conflict in c.section.contradictions


def test_mixed_sections_are_doubtful():
    c = classify_page({"шифр": "1-Р-07", "наименование листа": "План"},
                      "коммутатор маршрутизатор ЛВС видеонаблюдение видеокамера видеорегистратор", True)
    assert c.section.confidence == DOUBT and c.section.contradictions


def test_unreadable_text_falls_back_to_mark():
    c = classify_page({"шифр": "1497-Р-00-СПД", "наименование листа": "План расположения оборудования"}, "", True,
                      text_present=False)
    assert c.section.value == ["СС"] and c.section.confidence == DOUBT
    assert "не читается" in c.section.evidence[0]


def test_cover_without_stamp():
    c = classify_page({}, "РАБОЧАЯ ДОКУМЕНТАЦИЯ Том 5.1", False, fallback_code="1497-Р-00-СПД")
    assert c.doc_types.value == ["Обложка / титульный лист"]
    assert c.mark.value == ["СПД"] and c.mark.confidence == DOUBT


def test_suffix_type():
    c = classify_page({"шифр": "1497-Р-00-СПД.СО"}, "", True, text_present=False)
    assert c.doc_types.value == ["Спецификация"]


@pytest.fixture
def obj(tmp_path, sample_pdf):
    o = ProjectObject.create(tmp_path / "o", "o")
    add_files(o, [sample_pdf])
    yield o
    o.close()


def test_intake_classifies(obj):
    f = obj.files()[0]
    c = obj.classification(obj.pages(f.id)[0]["id"])
    assert c.mark.value == ["НСС"] and c.section.value == ["СС"]
    assert "Схема кабельной канализации" in c.doc_types.value
    assert c.dict_version


def test_user_decision_survives_reclassify_and_reopen(obj, tmp_path):
    f = obj.files()[0]
    pid = obj.pages(f.id)[0]["id"]
    obj.decide_classification(pid, {"section": ["КТСБ"], "subsystems": ["СОТ"]})
    classify_file(obj, f.id)
    c = obj.classification(pid)
    assert c.source == "пользователь" and c.section.value == ["КТСБ"] and c.section.confidence == SURE
    folder = obj.folder
    obj.close()
    o2 = ProjectObject.open(folder)
    try:
        classify_file(o2, f.id)
        assert o2.classification(pid).section.value == ["КТСБ"]
        assert any(h["entity"] == "classification" for h in o2.con.execute("SELECT entity FROM history"))
    finally:
        o2.close()


def test_classification_creates_no_findings(obj):
    tables = {r[0] for r in obj.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"finding", "remark"} & tables or obj.con.execute("SELECT COUNT(*) FROM finding").fetchone()[0] == 0


def test_unknown_level_value():
    c = classify_page({}, "", False, text_present=False)
    assert c.level == UNKNOWN
