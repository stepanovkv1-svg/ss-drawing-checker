"""Тестовый PDF: кириллица, лист A3 и повёрнутый на 270° лист. Используется в тестах и самопроверке CI."""
import os
import sys

import pypdfium2 as pdfium
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def make(font_path: str, out: str) -> None:
    pdfmetrics.registerFont(TTFont("Cyr", font_path))
    c = canvas.Canvas(out, pagesize=landscape(A3))
    c.setFont("Cyr", 14)
    c.drawString(200, 500, "ККС-47 Оптический кросс ДОЛ-П-8У")
    c.drawString(200, 470, "Шифр НСС-02 лист 3 дата 10.2026")
    c.showPage()
    c.setFont("Cyr", 14)
    c.drawString(100, 300, "ККС-49 повёрнутый лист")
    c.showPage()
    c.save()
    doc = pdfium.PdfDocument(out)
    doc[1].set_rotation(270)
    doc.save(out + ".tmp")
    doc.close()
    os.replace(out + ".tmp", out)


if __name__ == "__main__":
    make(sys.argv[1], sys.argv[2])
