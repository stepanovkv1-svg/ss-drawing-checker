"""Реализация движка на pypdfium2 (основной движок по итогам Э0)."""
from __future__ import annotations

import ctypes
import threading

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from .base import PageInfo, PdfError, Rect, TextHit, TextRun

# PDFium не потокобезопасен: все вызовы движка идут под одной блокировкой.
_LOCK = threading.RLock()

_ERRORS = {
    "Data format error": "файл повреждён или это не PDF",
    "Incorrect password error": "файл защищён паролем",
    "File access error": "файл не открывается (нет доступа или он занят)",
    "Unsupported security scheme error": "неподдерживаемая защита файла",
}


def _reason(exc: Exception) -> str:
    msg = str(exc)
    for key, text in _ERRORS.items():
        if key in msg:
            return text
    return "файл не прочитан"


class PdfiumDocument:
    def __init__(self, path: str):
        with _LOCK:
            try:
                self._doc = pdfium.PdfDocument(path)
                self._n = len(self._doc)
            except pdfium.PdfiumError as e:
                raise PdfError(_reason(e), str(e)) from e
        if self._n == 0:
            raise PdfError("в файле нет страниц")

    def page_count(self) -> int:
        return self._n

    def page_info(self, index: int) -> PageInfo:
        with _LOCK:
            pg = self._doc[index]
            try:
                w, h = pg.get_size()
                tp = pg.get_textpage()
                n = tp.count_chars()
                tp.close()
                return PageInfo(index + 1, w, h, pg.get_rotation(), n)
            finally:
                pg.close()

    def page_text(self, index: int) -> str:
        with _LOCK:
            pg = self._doc[index]
            tp = pg.get_textpage()
            try:
                return tp.get_text_range()
            finally:
                tp.close()
                pg.close()

    def text_in_rect(self, index: int, rect: Rect) -> str:
        with _LOCK:
            pg = self._doc[index]
            tp = pg.get_textpage()
            try:
                x0, y0, x1, y1 = rect
                return tp.get_text_bounded(left=x0, bottom=y0, right=x1, top=y1)
            finally:
                tp.close()
                pg.close()

    def search(self, index: int, needle: str, limit: int = 100) -> list[TextHit]:
        hits: list[TextHit] = []
        if not needle:
            return hits
        with _LOCK:
            pg = self._doc[index]
            tp = pg.get_textpage()
            try:
                s = tp.search(needle, match_case=False)
                while len(hits) < limit:
                    res = s.get_next()
                    if not res:
                        break
                    i, cnt = res
                    boxes = [tp.get_charbox(k) for k in range(i, i + cnt)]
                    rect = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes))
                    hits.append(TextHit(tp.get_text_range(i, cnt), rect))
                s.close()
            finally:
                tp.close()
                pg.close()
        return hits

    def runs(self, index: int) -> list[TextRun]:
        """Фрагменты текстового слоя с координатами (PDF и отображения)."""
        out: list[TextRun] = []
        with _LOCK:
            pg = self._doc[index]
            tp = pg.get_textpage()
            try:
                w, h = pg.get_size()
                W, H = round(w * 4), round(h * 4)   # 4 пикселя на пункт — точность 0,1 мм
                dx, dy = ctypes.c_int(), ctypes.c_int()
                for k in range(tp.count_rects()):
                    l, b, r, t = tp.get_rect(k)
                    text = tp.get_text_bounded(left=l, bottom=b, right=r, top=t)
                    if not text.strip():
                        continue
                    pts = []
                    for x, y in ((l, b), (r, t)):
                        pdfium_c.FPDF_PageToDevice(pg.raw, 0, 0, W, H, 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
                        pts.append((dx.value / 4, dy.value / 4))
                    (a, b2), (c, d) = pts
                    out.append(TextRun(text.strip(), (l, b, r, t), (min(a, c), min(b2, d), max(a, c), max(b2, d))))
            finally:
                tp.close()
                pg.close()
        return out

    def render(self, index: int, dpi: int) -> Image.Image:
        with _LOCK:
            pg = self._doc[index]
            try:
                return pg.render(scale=dpi / 72).to_pil().convert("RGB")
            finally:
                pg.close()

    def render_region(self, index: int, dpi: int, box: Rect) -> Image.Image:
        """Отрисовка части листа. box — координаты отображения в пунктах (x0, y0, x1, y1), начало вверху слева."""
        with _LOCK:
            pg = self._doc[index]
            try:
                w, h = pg.get_size()
                x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
                return pg.render(scale=dpi / 72, crop=(x0, h - y1, w - x1, y0)).to_pil().convert("RGB")
            finally:
                pg.close()

    def to_pixels(self, index: int, rects: list[Rect], dpi: int) -> list[tuple[int, int, int, int]]:
        """Координаты PDF → пиксели отрисовки. Поворот учитывает PDFium (Э0, И4)."""
        out = []
        with _LOCK:
            pg = self._doc[index]
            try:
                w, h = pg.get_size()
                W, H = round(w * dpi / 72), round(h * dpi / 72)
                dx, dy = ctypes.c_int(), ctypes.c_int()
                for x0, y0, x1, y1 in rects:
                    pts = []
                    for x, y in ((x0, y0), (x1, y1)):
                        pdfium_c.FPDF_PageToDevice(pg.raw, 0, 0, W, H, 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
                        pts.append((dx.value, dy.value))
                    (a, b), (c, d) = pts
                    out.append((min(a, c), min(b, d), max(a, c), max(b, d)))
            finally:
                pg.close()
        return out

    def from_pixels(self, index: int, boxes: list[tuple[int, int, int, int]], dpi: int) -> list[Rect]:
        """Пиксели отрисовки → координаты PDF (для результатов OCR)."""
        out: list[Rect] = []
        with _LOCK:
            pg = self._doc[index]
            try:
                w, h = pg.get_size()
                W, H = round(w * dpi / 72), round(h * dpi / 72)
                px, py = ctypes.c_double(), ctypes.c_double()
                for a, b, c, d in boxes:
                    pts = []
                    for x, y in ((a, b), (c, d)):
                        pdfium_c.FPDF_DeviceToPage(pg.raw, 0, 0, W, H, 0, int(x), int(y), ctypes.byref(px), ctypes.byref(py))
                        pts.append((px.value, py.value))
                    (x0, y0), (x1, y1) = pts
                    out.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            finally:
                pg.close()
        return out

    def close(self) -> None:
        with _LOCK:
            self._doc.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class PdfiumEngine:
    name = "pypdfium2"

    def open(self, path: str) -> PdfiumDocument:
        return PdfiumDocument(path)
