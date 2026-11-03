"""Тестовый PDF для самопроверки сборки в CI: кириллица, обычный лист A3 и повёрнутый лист."""
import sys
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

font_path, out = sys.argv[1], sys.argv[2]
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

# повернуть второй лист на 270°, как в комплекте оптических схем
import pypdfium2 as pdfium  # noqa: E402
doc = pdfium.PdfDocument(out)
doc[1].set_rotation(270)
doc.save(out + ".tmp")
doc.close()
import os  # noqa: E402
os.replace(out + ".tmp", out)
