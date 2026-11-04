"""Просмотр листа: масштаб, поиск надписи с рамкой (ТЗ 8.1 «Просмотр листа»)."""
from __future__ import annotations

from PIL import Image, ImageDraw
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..documents.pdf_engine import PdfDocument, PdfError, engine

ZOOMS = [("50%", 36), ("75%", 54), ("100%", 72), ("150%", 108), ("200%", 144)]


def to_pixmap(img: Image.Image) -> QPixmap:
    data = img.tobytes("raw", "RGB")
    qi = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qi.copy())


class SheetView(QWidget):
    rect_picked = Signal(object)          # прямоугольник в координатах PDF, обведённый мышью

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: PdfDocument | None = None
        self.page = 0
        self.rects = []
        self.picking = False
        self._press = None

        self.title = QLabel("Выберите лист в списке файлов")
        self.zoom = QComboBox()
        for name, dpi in ZOOMS:
            self.zoom.addItem(name, dpi)
        self.zoom.setCurrentIndex(1)
        self.zoom.currentIndexChanged.connect(self.redraw)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Найти надпись на листе")
        self.find.returnPressed.connect(self.do_find)
        btn = QPushButton("Найти")
        btn.clicked.connect(self.do_find)
        self.found = QLabel("")

        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.find)
        top.addWidget(btn)
        top.addWidget(self.found)
        top.addWidget(QLabel("Масштаб:"))
        top.addWidget(self.zoom)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.image.installEventFilter(self)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.image)
        self.scroll.setWidgetResizable(True)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.scroll, 1)

    def show_page(self, path: str, page_no: int, caption: str) -> None:
        if self.doc:
            self.doc.close()
            self.doc = None
        try:
            self.doc = engine().open(path)
        except PdfError as e:
            self.title.setText(f"{caption}: {e.reason}")
            self.image.clear()
            return
        self.page = page_no - 1
        self.rects = []
        self.found.setText("")
        self.title.setText(caption)
        self.redraw()

    def redraw(self, *_):
        if not self.doc:
            return
        dpi = self.zoom.currentData()
        img = self.doc.render(self.page, dpi)
        if self.rects:
            d = ImageDraw.Draw(img)
            for x0, y0, x1, y1 in self.doc.to_pixels(self.page, self.rects, dpi):
                d.rectangle((x0 - 4, y0 - 4, x1 + 4, y1 + 4), outline=(220, 0, 0), width=3)
        self.image.setPixmap(to_pixmap(img))

    def set_rects(self, rects: list) -> None:
        """Показать места (красные рамки) и прокрутить к первому."""
        self.rects = [r for r in rects if r]
        self.redraw()
        if self.rects and self.doc:
            x0, y0, _, _ = self.doc.to_pixels(self.page, [self.rects[0]], self.zoom.currentData())[0]
            self.scroll.horizontalScrollBar().setValue(max(0, x0 - 200))
            self.scroll.verticalScrollBar().setValue(max(0, y0 - 150))

    def start_pick(self) -> None:
        if self.doc:
            self.picking = True
            self.found.setText("обведите место мышью")

    def eventFilter(self, obj, ev):
        if obj is self.image and self.picking and self.doc:
            if ev.type() == QEvent.MouseButtonPress:
                self._press = ev.position().toPoint()
                return True
            if ev.type() == QEvent.MouseButtonRelease and self._press is not None:
                a, b = self._press, ev.position().toPoint()
                self._press, self.picking = None, False
                box = (min(a.x(), b.x()), min(a.y(), b.y()), max(a.x(), b.x()), max(a.y(), b.y()))
                if box[2] - box[0] > 4 and box[3] - box[1] > 4:
                    rect = self.doc.from_pixels(self.page, [box], self.zoom.currentData())[0]
                    self.rects = [rect]
                    self.found.setText("рамка задана")
                    self.redraw()
                    self.rect_picked.emit(rect)
                else:
                    self.found.setText("")
                return True
        return super().eventFilter(obj, ev)

    def do_find(self):
        if not self.doc:
            return
        hits = self.doc.search(self.page, self.find.text().strip())
        self.rects = [h.rect for h in hits]
        self.found.setText(f"найдено: {len(hits)}")
        self.redraw()
        if hits:
            x0, y0, _, _ = self.doc.to_pixels(self.page, [hits[0].rect], self.zoom.currentData())[0]
            self.scroll.horizontalScrollBar().setValue(max(0, x0 - 200))
            self.scroll.verticalScrollBar().setValue(max(0, y0 - 150))

    def clear(self):
        if self.doc:
            self.doc.close()
            self.doc = None
        self.image.clear()
        self.title.setText("Выберите лист в списке файлов")
