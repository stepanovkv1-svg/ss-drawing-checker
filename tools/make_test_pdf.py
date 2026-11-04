"""Тестовый PDF для тестов и самопроверки CI. Это не эталон проверки, а набор технических случаев:
1) лист A3 со штампом по форме 3 и надписями ККС-47 / НСС-02;
2) лист с поворотом 270° (как в комплекте оптических схем);
3) «скан» — лист без текстового слоя, только изображение (для OCR);
4) последующий лист с основной надписью по форме 6 (линии рамки, текст в слое);
5) первый лист по форме 3, где текст штампа — изображение (как шрифты SHX, превращённые в линии)."""
import io
import os
import sys

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

MM = 72 / 25.4

STAMP = [  # (текст, мм от правого края до начала, мм от низа, кегль)
    ("АГА02-03226-000-НСС-02-РД", 61, 53, 9),
    ("Строительство аэровокзального комплекса", 108, 41, 8),
    ("Изм.", 188, 36, 6), ("Кол.уч.", 179, 36, 6), ("Лист", 168, 36, 6), ("№ док.", 159, 36, 6),
    ("Подпись", 147, 36, 6), ("Дата", 133, 36, 6),
    ("Стадия", 52, 31, 6), ("Лист", 35, 31, 6), ("Листов", 19, 31, 6),
    ("Разработал", 189, 30, 6), ("Иванов", 169, 30, 6), ("10.2026", 134, 30, 6),
    ("Проверил", 189, 25, 6), ("Петров", 169, 25, 6), ("10.26", 134, 25, 6),
    ("Наружные сети связи", 103, 26, 8),
    ("Р", 48, 24, 8), ("3", 33, 24, 8), ("5", 19, 24, 8),
    ("Схема кабельной канализации", 119, 11, 8),
    ("ООО Проект", 45, 11, 7),
    ("Формат А3", 37, 0.5, 6),
]


def _frame(c, W, H):
    c.setLineWidth(1.2)
    c.rect(20 * MM, 5 * MM, W - 25 * MM, H - 10 * MM)


# линии основной надписи: (r0, b0, r1, b1) мм от правого и нижнего края листа
FORM6_LINES = [(190, 5, 190, 20), (190, 20, 5, 20), (125, 5, 125, 20), (15, 5, 15, 20), (15, 13, 5, 13),
               (190, 10, 125, 10), (190, 15, 125, 15)] + [(x, 5, x, 20) for x in (180, 170, 160, 150, 135)]
FORM6_TEXT = [("Изм.", 188, 6, 6), ("Кол.уч.", 179, 6, 6), ("Лист", 168, 6, 6), ("№ док.", 159, 6, 6),
              ("Подп.", 147, 6, 6), ("Дата", 133, 6, 6), ("АГА02-03226-000-НСС-02-РД", 95, 11, 12),
              ("Лист", 13.5, 15, 6), ("4", 11, 7, 9)]
FORM3_LINES = [(190, 5, 190, 60), (190, 60, 5, 60), (125, 5, 125, 60), (125, 50, 5, 50), (125, 40, 5, 40),
               (55, 5, 55, 35), (40, 20, 40, 35), (25, 20, 25, 35), (55, 35, 5, 35), (55, 30, 5, 30),
               (125, 20, 5, 20), (190, 35, 125, 35), (190, 40, 125, 40)] + \
              [(190, b, 125, b) for b in (10, 15, 20, 25, 30, 45, 50, 55)] + [(x, 5, x, 60) for x in (170, 150, 135)]
FORM3_IMG_TEXT = [("НСС-02-РД-ОБЩ", 45, 53, 12), ("Строительство аэровокзального комплекса", 120, 43, 8),
                  ("Наружные сети связи", 105, 26, 9), ("Ведомость листов", 105, 11, 9),
                  ("Р", 49, 26.5, 10), ("1", 33, 26.5, 10), ("12", 18, 26.5, 10),
                  ("Стадия", 53, 31.2, 6), ("Лист", 37, 31.2, 6), ("Листов", 22, 31.2, 6),
                  ("Изм.", 188, 36.2, 6), ("Дата", 133, 36.2, 6),
                  ("Разраб.", 188, 31, 7), ("Иванов", 168, 31, 7), ("10.25", 133.5, 31, 7)]


def make(font_path: str, out: str) -> None:
    pdfmetrics.registerFont(TTFont("Cyr", font_path))
    W, H = landscape(A3)
    c = canvas.Canvas(out, pagesize=(W, H))
    c.setFont("Cyr", 14)
    c.drawString(200, 500, "ККС-47 Оптический кросс ДОЛ-П-8У")
    c.drawString(200, 470, "Шифр НСС-02 лист 3 дата 10.2026")
    c.rect(20 * MM, 5 * MM, W - 25 * MM, H - 10 * MM)
    c.rect(W - 190 * MM, 5 * MM, 185 * MM, 55 * MM)
    for text, r, b, size in STAMP:
        c.setFont("Cyr", size)
        c.drawString(W - r * MM, b * MM + 1, text)
    c.showPage()

    c.setFont("Cyr", 14)
    c.drawString(100, 300, "ККС-49 повёрнутый лист")
    c.showPage()

    # «скан»: картинка с текстом, без текстового слоя
    img = Image.new("L", (int(W / 72 * 200), int(H / 72 * 200)), 255)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 80)
    d.text((400, 600), "ККС-50 Колодец кабельный", fill=0, font=font)
    d.text((400, 800), "Кабель ОКЛ-0,22-16", fill=0, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), 0, 0, W, H)
    c.showPage()

    # 4) форма 6 — последующий лист
    _frame(c, W, H)
    for r0, b0, r1, b1 in FORM6_LINES:
        c.line(W - r0 * MM, b0 * MM, W - r1 * MM, b1 * MM)
    for text, r, b, size in FORM6_TEXT:
        c.setFont("Cyr", size)
        c.drawString(W - r * MM, b * MM, text)
    c.showPage()

    # 5) форма 3, текст штампа — картинкой
    _frame(c, W, H)
    for r0, b0, r1, b1 in FORM3_LINES:
        c.line(W - r0 * MM, b0 * MM, W - r1 * MM, b1 * MM)
    dpi = 300
    k = dpi / 25.4
    img = Image.new("L", (int(190 * k), int(60 * k)), 255)   # область r 0..190, b 0..60 мм
    d = ImageDraw.Draw(img)
    for text, r, b, size in FORM3_IMG_TEXT:
        f = ImageFont.truetype(font_path, int(size * 0.3528 * k))
        d.text(((190 - r) * k, (60 - b) * k), text, fill=0, font=f, anchor="ls")
    img = img.convert("RGBA")
    img.putalpha(Image.eval(img.convert("L"), lambda v: 255 - v))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), W - 190 * MM, 0, 190 * MM, 60 * MM, mask="auto")
    c.showPage()
    c.save()

    doc = pdfium.PdfDocument(out)
    doc[1].set_rotation(270)
    doc.save(out + ".tmp")
    doc.close()
    os.replace(out + ".tmp", out)


def make_xlsx(out: str) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Спецификация"
    ws.append(["Спецификация оборудования, изделий и материалов"])
    ws.append([])
    ws.append(["Поз.", "Наименование и техническая характеристика", "Тип, марка", "Код", "Завод-изготовитель",
               "Ед. изм.", "Кол.", "Масса", "Примечание"])
    ws.append([1, 2, 3, 4, 5, 6, 7, 8, 9])
    ws.append([1, "Колодец кабельный", "ККС-2", None, None, "шт.", 4, None, None])
    ws.append([2, "Кабель оптический", "ОКЛ-0,22-16", None, None, "м", 1250.5, None, "с запасом 2%"])
    ws2 = wb.create_sheet("Кабельный журнал")
    ws2.append(["Обозначение", "Откуда", "Куда", "Марка кабеля", "Длина, м"])
    ws2.append(["К1", "ККС-47", "ККС-48", "ОКЛ-0,22-16", 60])
    wb.save(out)


if __name__ == "__main__":
    make(sys.argv[1], sys.argv[2])
    if len(sys.argv) > 3:
        make_xlsx(sys.argv[3])
