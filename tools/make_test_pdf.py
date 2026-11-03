"""Тестовый PDF для тестов и самопроверки CI. Это не эталон проверки, а набор технических случаев:
1) лист A3 со штампом по форме 3 и надписями ККС-47 / НСС-02;
2) лист с поворотом 270° (как в комплекте оптических схем);
3) «скан» — лист без текстового слоя, только изображение (для OCR)."""
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
