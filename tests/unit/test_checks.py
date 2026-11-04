"""Базовые проверки БП-02…БП-09: каждая находит заложенный дефект и молчит на чистом наборе (ТЗ 5.1).
Надёжность источников (П1-20): штамп OCR без подтверждения и текст OCR в замечания не идут."""
import json

from app.documents.classify import SURE, Characteristic, Classification
from app.findings.model import CONFIRMED, POSSIBLE, QUESTION
from app.rules import base_checks as bc
from app.rules.context import Context, Sheet

R = [10.0, 10.0, 20.0, 20.0]


class FakeObj:
    def __init__(self):
        self.items = {}

    def text_items(self, page_id):
        return self.items.get(page_id, [])


def stamp(source="слой", confirmed=False, sigs=None, **fields):
    return {"recognized": True, "source": source, "confirmed": confirmed, "note": "",
            "fields": {k: {"value": v, "confidence": "высокая", "edited": False, "rects": json.dumps([R])}
                       for k, v in fields.items()},
            "signatures": sigs if sigs is not None else [{"role": "Разраб.", "surname": "Иванов", "date": "10.25"}]}


def full(code="НСС-02-РД", sheet="1", total="3", **kw):
    f = {"шифр": code, "лист": sheet, "листов": total, "наименование листа": "План", "стадия": "Р",
         "объект": "Аэропорт"}
    f.update(kw)
    return f


def make_ctx(specs, xlsx=None):
    """specs: [(file, stamp|None, [тексты слоя] | [(текст, источник)])]."""
    obj = FakeObj()
    ctx = Context(obj)
    for n, (fname, st, texts) in enumerate(specs, 1):
        page = {"id": n, "page_no": n, "format": "A3", "text_chars": 100, "extract_status": "прочитан"}
        obj.items[n] = [{"text": t if isinstance(t, str) else t[0], "source": "слой" if isinstance(t, str) else t[1],
                         "x0": 1, "y0": 2, "x1": 3, "y1": 4} for t in texts]
        ctx.sheets.append(Sheet(obj, 1 if fname == "А.pdf" else 2, fname, page, st, None))
    ctx.xlsx = xlsx or []
    return ctx


def clean():
    return make_ctx([("А.pdf", stamp(**full(sheet=str(i))), ["ККС-1 ККС-2 ККС-3 ККС-4", "КПСнг(А)-FRHF 1х2х0,75"])
                     for i in (1, 2, 3)])


def rules(res):
    return [(d.rule, d.cls) for d in res.drafts]


def test_clean_set_is_silent():
    ctx = clean()
    for code, _, fn in bc.CHECKS:
        if code == "БП-10":
            continue
        res = fn(ctx)
        assert res.drafts == [], (code, [d.text for d in res.drafts])


def test_bp02_missing_field_and_surname():
    ctx = make_ctx([("А.pdf", stamp(confirmed=True, sigs=[{"role": "Разраб.", "surname": "", "date": ""}],
                                    **{k: v for k, v in full().items() if k != "стадия"}), [])])
    ctx.sheets[0].stamp["fields"]["стадия"] = None
    del ctx.sheets[0].stamp["fields"]["стадия"]
    res = bc.bp02(ctx)
    texts = " ".join(d.text for d in res.drafts)
    assert ("БП-02", CONFIRMED) in rules(res)
    assert "«стадия»" in texts and "фамилия в строке «Разраб.»" in texts


def test_bp02_unconfirmed_ocr_stamp_skipped():
    ctx = make_ctx([("А.pdf", stamp(source="OCR", sigs=[{"role": "Разраб.", "surname": "", "date": ""}],
                                    шифр="НСС-02-РД"), [])])
    res = bc.bp02(ctx)
    assert res.drafts == [] and res.status == "частично"
    ctx.sheets[0].stamp["confirmed"] = True          # «Штамп проверен» — замечания строятся
    assert bc.bp02(ctx).drafts


def test_bp03_duplicate_gap_and_total():
    ctx = make_ctx([("А.pdf", stamp(**full(sheet="1", total="4")), []),
                    ("А.pdf", stamp(**full(sheet="1", total="4")), []),
                    ("А.pdf", stamp(**full(sheet="5", total="4")), [])])
    res = bc.bp03(ctx)
    keys = {d.key.split("|")[1]: d for d in res.drafts}
    assert keys["дубль"].cls == CONFIRMED and len(keys["дубль"].places) == 2
    assert keys["листов"].cls == CONFIRMED
    assert keys["пропуск"].cls == QUESTION and "2, 3" in keys["пропуск"].text


def test_bp03_gap_single_sheet_one_place():
    ctx = make_ctx([("А.pdf", stamp(**full(sheet="3", total="3")), [])])
    gap = bc.bp03(ctx).drafts[0]
    assert "1, 2" in gap.text and len(gap.places) == 1


def test_bp03_duplicate_between_files_is_question():
    ctx = make_ctx([("А.pdf", stamp(**full(sheet="1", total="1")), []),
                    ("Б.pdf", stamp(**full(sheet="1", total="1")), [])])
    assert rules(bc.bp03(ctx)) == [("БП-03", QUESTION)]


def test_bp03_ocr_sheet_numbers_count_as_present():
    ocr = stamp(source="OCR", **full(sheet="2"))
    ctx = make_ctx([("А.pdf", stamp(**full(sheet="1")), []), ("А.pdf", ocr, []),
                    ("А.pdf", stamp(**full(sheet="3")), [])])
    assert bc.bp03(ctx).drafts == []


def test_bp04_code_spelling_and_dates():
    lat = stamp(**full(code="HCC-02-PД", sheet="3"), sigs=[{"role": "Разраб.", "surname": "Иванов", "date": "10.2025"}])
    ctx = make_ctx([("А.pdf", stamp(**full(sheet="1")), []), ("А.pdf", stamp(**full(sheet="2")), []),
                    ("А.pdf", lat, [])])
    res = bc.bp04(ctx)
    keys = [d.key for d in res.drafts]
    assert "шифр|НСС-02-РД" in keys and "формат дат" in keys
    code = next(d for d in res.drafts if d.key.startswith("шифр"))
    assert len(code.places) == 1 and code.places[0].page_no == 3


def test_bp05_reference_to_missing_doc_and_sheet():
    ctx = make_ctx([("А.pdf", stamp(**full(sheet="1", total="2")), ["см. НСС-02-РД-СС2", "см. лист 7"]),
                    ("А.pdf", stamp(**full(sheet="2", total="2")), [])])
    res = bc.bp05(ctx)
    keys = {d.key: d for d in res.drafts}
    assert "ссылка|НСС-02-РД-СС2" in keys
    assert keys["ссылка на лист|НСС-02-РД|7"].cls == CONFIRMED


def test_bp05_ocr_text_not_used():
    ctx = make_ctx([("А.pdf", stamp(**full(sheet="1", total="1")), [("см. НСС-02-РД-СС2", "OCR")])])
    assert bc.bp05(ctx).drafts == []


def test_bp06_series_gap_and_xlsx_duplicate():
    ctx = make_ctx([("А.pdf", stamp(**full()), ["ККС-1", "ККС-2", "ККС-4", "ККС-5", "ККС-6"])],
                   xlsx=[{"file": "С.xlsx", "table": {"sheet": "Лист1"},
                          "schema": {"A": {"role": "обозначение"}},
                          "rows": [(2, {"A": "К1"}), (3, {"A": "K1"}), (4, {"A": "К2"})]}])
    res = bc.bp06(ctx)
    keys = [d.key for d in res.drafts]
    assert any("ККС|пропуск|[3]" in k for k in keys)
    dup = next(d for d in res.drafts if d.key.startswith("xlsx"))
    assert dup.cls == CONFIRMED and [p.cell for p in dup.places] == ["A2", "A3"]


def test_bp07_xlsx_vs_pdf():
    ctx = make_ctx([("А.pdf", stamp(**full()), ["ККС-1", "ККС-2", "ККС-9"])],
                   xlsx=[{"file": "С.xlsx", "table": {"sheet": "Лист1"}, "schema": {"A": {"role": "обозначение"}},
                          "rows": [(2, {"A": "ККС-1"}), (3, {"A": "ККС-2"}), (4, {"A": "ККС-3"})]}])
    res = bc.bp07(ctx)
    keys = sorted(d.key for d in res.drafts)
    assert keys == ["нет в PDF|ККС-3", "нет в XLSX|ККС-9"]
    assert all(d.cls == POSSIBLE for d in res.drafts)


def test_bp08_cable_spelling():
    ctx = make_ctx([("А.pdf", stamp(**full()), ["КПСнг(А)-FRHF 1х2х0,75", "КПСнг(А)-FRHF 1х2х0,75",
                                                "КПСнг(А)-FRHF 1x2x0,75"])])
    res = bc.bp08(ctx)
    d = next(d for d in res.drafts if d.key.startswith("написание"))
    assert len(d.places) == 1 and "1x2x0,75" in d.text


def test_bp09_conflict():
    def ch(v):
        return Characteristic(value=[v], confidence=SURE, evidence=["ККС"])
    ctx = make_ctx([("А.pdf", stamp(**full()), [])])
    ctx.sheets[0].cls = Classification(ch("НСС"), ch("СС"), ch(""), ch("План"),
                                       conflict="шифр указывает на СС, содержание — на КТСБ")
    assert rules(bc.bp09(ctx)) == [("БП-09", QUESTION)]
    ctx.sheets[0].cls.source = "пользователь"         # решение пользователя снимает вопрос
    assert bc.bp09(ctx).drafts == []
