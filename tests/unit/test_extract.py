import json

import pytest

from app.data import ProjectObject
from app.documents import ocr
from app.documents.intake import add_files
from app.documents.textnorm import norm_code
from app.documents.xlsx import parse_workbook


@pytest.fixture
def obj(tmp_path, sample_pdf, sample_xlsx):
    o = ProjectObject.create(tmp_path / "o", "o")
    add_files(o, [sample_pdf, sample_xlsx])
    yield o
    o.close()


def test_stamp_fields(obj):
    pdf = obj.files()[0]
    p1 = obj.pages(pdf.id)[0]
    st = obj.stamp(p1["id"])
    assert st["recognized"]
    f = {k: v["value"] for k, v in st["fields"].items()}
    assert f["шифр"] == "АГА02-03226-000-НСС-02-РД"
    assert f["стадия"] == "Р" and f["лист"] == "3" and f["листов"] == "5"
    assert f["объект"] == "Строительство аэровокзального комплекса"
    assert f["раздел"] == "Наружные сети связи"
    assert f["наименование листа"] == "Схема кабельной канализации"
    assert f["организация"] == "ООО Проект"
    assert f["формат"] == "А3"
    assert [(s["role"], s["surname"], s["date"]) for s in st["signatures"]] == [
        ("Разработал", "Иванов", "10.2026"), ("Проверил", "Петров", "10.26")]
    # у каждого поля есть место на листе
    assert all(json.loads(v["rects"]) for v in st["fields"].values())


def test_page_without_stamp(obj):
    pdf = obj.files()[0]
    st = obj.stamp(obj.pages(pdf.id)[1]["id"])
    assert not st["recognized"] and "вручную" in st["note"]


def test_user_edit_survives_reextract(obj):
    from app.documents.extract import extract_pdf
    pdf = obj.files()[0]
    pid = obj.pages(pdf.id)[0]["id"]
    obj.set_stamp_field(pid, "лист", "4")
    extract_pdf(obj, pdf.id)
    st = obj.stamp(pid)
    assert st["fields"]["лист"]["value"] == "4" and st["fields"]["лист"]["edited"] == 1


def test_text_items_have_coordinates(obj):
    pdf = obj.files()[0]
    items = obj.text_items(obj.pages(pdf.id)[0]["id"])
    hit = [i for i in items if "ККС-47" in i["text"]]
    assert hit and hit[0]["source"] == "слой" and hit[0]["x1"] > hit[0]["x0"]


@pytest.mark.skipif(not ocr.available(), reason="Tesseract не установлен")
def test_ocr_on_scan(obj):
    pdf = obj.files()[0]
    p3 = obj.pages(pdf.id)[2]
    assert p3["text_chars"] == 0
    items = obj.text_items(p3["id"])
    assert items and all(i["source"] == "OCR" for i in items)
    text = norm_code(" ".join(i["text"] for i in items))
    assert "ККС-50" in text
    assert p3["extract_status"].startswith("OCR")
    # координаты OCR в пределах листа
    assert all(0 <= i["x0"] < i["x1"] <= p3["width_pt"] + 1 for i in items)


def test_xlsx_tables(obj):
    x = obj.files()[1]
    tables = obj.xlsx_tables(x.id)
    assert [t["sheet"] for t in tables] == ["Спецификация", "Кабельный журнал"]
    spec = tables[0]
    assert spec["header_row"] == 3
    schema = json.loads(spec["schema"])
    roles = {v["role"] for v in schema.values()}
    assert {"позиция", "наименование", "обозначение", "единица", "количество", "примечание"} <= roles
    rows = [json.loads(r["cells"]) for r in obj.xlsx_rows(spec["id"])]
    assert len(rows) == 2                         # строка номеров столбцов пропущена
    assert rows[0]["C"] == "ККС-2" and rows[1]["G"] == "1250.5"
    j = json.loads(tables[1]["schema"])
    assert {j[c]["role"] for c in j} >= {"откуда", "куда", "кабель", "длина"}


def test_norm_code():
    assert norm_code("KKC – 47") == "ККС-47"
    assert norm_code("ккс-47 ") == "ККС-47"
    assert norm_code("АГА02-03226") == "АГА02-03226"


def test_xlsx_broken(tmp_path):
    p = tmp_path / "x.xlsx"
    p.write_bytes(b"PK\x03\x04garbage")
    with pytest.raises(Exception):
        parse_workbook(str(p))


def test_stamp_form6_next_sheet(obj):
    """Последующий лист: основная надпись по форме 6 находится по линиям рамки, текст — из слоя."""
    pdf = obj.files()[0]
    st = obj.stamp(obj.pages(pdf.id)[3]["id"])
    assert st["recognized"] and "форма 6" in st["note"]
    f = {k: v["value"] for k, v in st["fields"].items()}
    assert f["шифр"] == "АГА02-03226-000-НСС-02-РД" and f["лист"] == "4"


@pytest.mark.skipif(not ocr.available(), reason="Tesseract не установлен")
def test_stamp_drawn_as_image(obj):
    """Штамп без текста в слое (как шрифты SHX): графы читаются OCR по сетке формы 3."""
    pdf = obj.files()[0]
    st = obj.stamp(obj.pages(pdf.id)[4]["id"])
    assert st["recognized"] and "форма 3" in st["note"] and "OCR" in st["note"]
    f = {k: v["value"] for k, v in st["fields"].items()}
    assert f["шифр"] == "НСС-02-РД-ОБЩ"
    assert f["лист"] == "1" and f["листов"] == "12" and f["стадия"] == "Р"
    assert f["наименование листа"] == "Ведомость листов"
    assert st["signatures"][0]["surname"] == "Иванов"


def test_garbled_layer_detected():
    from app.documents.pdf_engine import TextRun
    from app.documents.stamp_grid import layer_is_garbled
    bad = [TextRun("\x0e\x13\x06\x1a\x02", (0, 0, 1, 1), (0, 0, 1, 1))] * 3
    good = [TextRun("Стадия", (0, 0, 1, 1), (0, 0, 1, 1))]
    assert layer_is_garbled(bad + good) and not layer_is_garbled(good)
