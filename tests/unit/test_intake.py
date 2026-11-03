from pathlib import Path

from app.data import ProjectObject
from app.documents.intake import add_files
from app.documents.pdf_engine import engine


def test_intake_pdf_and_errors(tmp_path, sample_pdf):
    broken = tmp_path / "обрезан.pdf"
    broken.write_bytes(sample_pdf.read_bytes()[:300])
    empty = tmp_path / "пустой.pdf"
    empty.write_bytes(b"")
    badx = tmp_path / "таблица.xlsx"
    badx.write_bytes(b"not a zip")
    o = ProjectObject.create(tmp_path / "o", "o")
    res = add_files(o, [sample_pdf, broken, empty, badx, sample_pdf])
    st = {r.name: (r.status, r.message) for r in res}
    assert st[sample_pdf.name][0] in ("прочитан", "уже есть")
    assert res[0].status == "прочитан" and res[0].pages == 3
    assert res[-1].status == "уже есть"
    assert st["обрезан.pdf"] == ("ошибка", "файл повреждён или это не PDF")
    assert st["пустой.pdf"][0] == "ошибка"
    assert st["таблица.xlsx"][0] == "ошибка"
    f = o.files()[0]
    pages = o.pages(f.id)
    assert [p["format"] for p in pages] == ["A3", "A3", "A3"]
    assert [p["rotation"] for p in pages] == [0, 270, 0]
    assert pages[0]["text_chars"] > 0
    o.close()


def test_search_and_frames_on_rotated_page(sample_pdf):
    doc = engine().open(str(sample_pdf))
    try:
        hits = doc.search(0, "ККС-47")
        assert len(hits) == 1 and hits[0].text == "ККС-47"
        # рамка внутри отрисовки и вокруг тёмных пикселей надписи
        for page, needle in ((0, "ККС-47"), (1, "ККС-49")):
            h = doc.search(page, needle)[0]
            img = doc.render(page, 100).convert("L")
            x0, y0, x1, y1 = doc.to_pixels(page, [h.rect], 100)[0]
            assert 0 <= x0 < x1 <= img.width and 0 <= y0 < y1 <= img.height
            crop = img.crop((x0, y0, x1 + 1, y1 + 1))
            assert crop.getextrema()[0] < 100, f"рамка на листе {page + 1} не попала на текст"
        assert "НСС-02" in doc.text_in_rect(0, (0, 0, 2000, 2000))
    finally:
        doc.close()
