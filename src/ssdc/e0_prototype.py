"""SS Drawing Checker — прототип этапа 0.

Что проверяет прототип на компьютере пользователя (Windows 11):
- окно PySide6 запускается из переносимой папки без установки и без Python;
- PDF открывается через pypdfium2, лист отрисовывается;
- поиск надписи по текстовому слою и красная рамка вокруг неё (точность координат);
- OCR фрагмента листа через Tesseract из состава сборки (rus+eng).

Сетевого кода нет. Режим самопроверки: `SSDrawingChecker-E0.exe --selftest <pdf> <out.json>`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

VERSION = "0.0.1-e0"
MM_PER_PT = 25.4 / 72
FORMATS = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "A1": (594, 841), "A0": (841, 1189)}


# ---------------------------------------------------------------- ядро (без Qt)

def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def tesseract_exe() -> str | None:
    bundled = os.path.join(base_dir(), "tesseract", "tesseract.exe")
    if os.path.exists(bundled):
        return bundled
    return shutil.which("tesseract")


def tessdata_dir() -> str | None:
    d = os.path.join(base_dir(), "tesseract", "tessdata")
    return d if os.path.isdir(d) else None


def sheet_format(w_mm: float, h_mm: float) -> str:
    a, b = sorted((w_mm, h_mm))
    for name, (x, y) in FORMATS.items():
        if abs(a - x) <= 3 and abs(b - y) <= 3:
            return name
    for name, (x, y) in FORMATS.items():
        for short, long_ in ((x, y), (y, x)):
            if abs(a - short) <= 3:
                k = b / long_ if long_ != short else 0
                k2 = b / x if short == y else b / y
                for kk in (k, k2):
                    if kk and abs(kk - round(kk)) < 0.05 and round(kk) > 1:
                        return f"{name}x{round(kk)}"
    return f"нестандартный {round(a)}x{round(b)} мм"


def page_info(doc: pdfium.PdfDocument) -> list[dict]:
    out = []
    for i in range(len(doc)):
        pg = doc[i]
        w, h = pg.get_size()
        tp = pg.get_textpage()
        n = tp.count_chars()
        out.append({
            "page": i + 1,
            "size_mm": [round(w * MM_PER_PT), round(h * MM_PER_PT)],
            "format": sheet_format(w * MM_PER_PT, h * MM_PER_PT),
            "rotation": pg.get_rotation(),
            "text_chars": n,
            "text_layer": "есть" if n > 50 else "нет (нужен OCR)",
        })
        tp.close()
        pg.close()
    return out


def render(doc: pdfium.PdfDocument, index: int, dpi: int) -> Image.Image:
    pg = doc[index]
    img = pg.render(scale=dpi / 72).to_pil().convert("RGB")
    pg.close()
    return img


def find_boxes(doc: pdfium.PdfDocument, index: int, needle: str, limit: int = 50) -> list[tuple[float, float, float, float]]:
    """Рамки найденного текста в координатах PDF (left, bottom, right, top)."""
    pg = doc[index]
    tp = pg.get_textpage()
    boxes = []
    if needle:
        s = tp.search(needle, match_case=False)
        while len(boxes) < limit:
            res = s.get_next()
            if not res:
                break
            idx, cnt = res
            cb = [tp.get_charbox(k) for k in range(idx, idx + cnt)]
            boxes.append((min(b[0] for b in cb), min(b[1] for b in cb), max(b[2] for b in cb), max(b[3] for b in cb)))
        s.close()
    tp.close()
    pg.close()
    return boxes


def boxes_to_pixels(doc, index, boxes, dpi):
    """PDF-координаты → пиксели отрисовки. Поворот страницы (/Rotate) учитывает сам PDFium:
    ручной пересчёт на листах с поворотом 270° давал рамки мимо надписи (испытание И4)."""
    import ctypes
    import pypdfium2.raw as raw
    pg = doc[index]
    w, h = pg.get_size()  # размер уже с учётом поворота — как при отрисовке
    W, H = round(w * dpi / 72), round(h * dpi / 72)
    out = []
    dx, dy = ctypes.c_int(), ctypes.c_int()
    for l, b, r, t in boxes:
        pts = []
        for x, y in ((l, b), (r, t)):
            raw.FPDF_PageToDevice(pg.raw, 0, 0, W, H, 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
            pts.append((dx.value, dy.value))
        (x1, y1), (x2, y2) = pts
        out.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    pg.close()
    return out


def draw_frames(img: Image.Image, px_boxes, pad=6) -> Image.Image:
    img = img.copy()
    d = ImageDraw.Draw(img)
    for x1, y1, x2, y2 in px_boxes:
        d.rectangle((x1 - pad, y1 - pad, x2 + pad, y2 + pad), outline=(220, 0, 0), width=3)
    return img


def ocr(img: Image.Image, lang: str = "rus+eng") -> dict:
    exe = tesseract_exe()
    if not exe:
        return {"ok": False, "error": "Tesseract не найден в составе сборки"}
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.png")
        img.save(src)
        cmd = [exe, src, "stdout", "-l", lang, "--psm", "6"]
        td_dir = tessdata_dir()
        if td_dir:
            cmd += ["--tessdata-dir", td_dir]
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        t0 = time.perf_counter()
        p = subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=300)
        dt = time.perf_counter() - t0
    if p.returncode != 0:
        return {"ok": False, "error": p.stderr.decode("utf-8", "replace")[-500:]}
    return {"ok": True, "text": p.stdout.decode("utf-8", "replace"), "seconds": round(dt, 2)}


def selftest(pdf_path: str, out_path: str, needle: str = "") -> int:
    """Проверка без окна: открытие, сведения, поиск с рамкой, OCR фрагмента. Пишет JSON."""
    rep: dict = {"version": VERSION, "pdf": os.path.basename(pdf_path)}
    try:
        t0 = time.perf_counter()
        doc = pdfium.PdfDocument(pdf_path)
        rep["open_s"] = round(time.perf_counter() - t0, 3)
        rep["pages"] = page_info(doc)
        t0 = time.perf_counter()
        img = render(doc, 0, 150)
        rep["render150_s"] = round(time.perf_counter() - t0, 2)
        rep["render_px"] = list(img.size)
        if needle:
            boxes = find_boxes(doc, 0, needle)
            rep["found"] = len(boxes)
            if boxes:
                px = boxes_to_pixels(doc, 0, boxes[:1], 150)[0]
                x1, y1, x2, y2 = px
                window = (max(0, int(x1) - 300), max(0, int(y1) - 150), int(x2) + 300, int(y2) + 150)
                draw_frames(img, [px]).crop(window).save(os.path.splitext(out_path)[0] + "_frame.png")
                big = render(doc, 0, 300)
                px300 = boxes_to_pixels(doc, 0, boxes[:1], 300)[0]
                a, b, c, d = px300
                rep["ocr"] = ocr(big.crop((max(0, a - 20), max(0, b - 20), c + 20, d + 20)))
        else:
            w, h = img.size
            rep["ocr"] = ocr(img.crop((w // 3, h // 3, 2 * w // 3, 2 * h // 3)))
        rep["tesseract"] = tesseract_exe()
        doc.close()
        rep["ok"] = True
    except Exception as e:  # noqa: BLE001 — в самопроверке фиксируем любую ошибку
        rep["ok"] = False
        rep["error"] = f"{type(e).__name__}: {e}"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    return 0 if rep["ok"] else 1


# ---------------------------------------------------------------- окно

def run_gui() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                   QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                                   QSplitter, QVBoxLayout, QWidget, QSpinBox)

    def to_pixmap(img: Image.Image) -> QPixmap:
        data = img.tobytes("raw", "RGB")
        qi = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
        return QPixmap.fromImage(qi.copy())

    class Main(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"SS Drawing Checker — прототип Э0 ({VERSION})")
            self.resize(1400, 900)
            self.doc = None
            self.path = None
            self.current_img = None
            self.boxes_px = []

            btn_open = QPushButton("Открыть PDF…")
            btn_open.clicked.connect(self.open_pdf)
            self.search = QLineEdit()
            self.search.setPlaceholderText("Найти надпись на листе, например ККС-47")
            self.search.returnPressed.connect(self.do_search)
            btn_find = QPushButton("Найти")
            btn_find.clicked.connect(self.do_search)
            self.dpi = QSpinBox()
            self.dpi.setRange(72, 300)
            self.dpi.setValue(100)
            self.dpi.setSuffix(" dpi")
            self.dpi.valueChanged.connect(self.show_page)
            btn_ocr = QPushButton("OCR найденного")
            btn_ocr.clicked.connect(self.do_ocr)

            top = QHBoxLayout()
            for w in (btn_open, self.search, btn_find, QLabel("Масштаб:"), self.dpi, btn_ocr):
                top.addWidget(w)

            self.pages = QListWidget()
            self.pages.currentRowChanged.connect(self.show_page)
            self.info = QPlainTextEdit()
            self.info.setReadOnly(True)
            left = QSplitter(Qt.Vertical)
            left.addWidget(self.pages)
            left.addWidget(self.info)

            self.view = QLabel("Откройте PDF")
            self.view.setAlignment(Qt.AlignCenter)
            scroll = QScrollArea()
            scroll.setWidget(self.view)
            scroll.setWidgetResizable(True)
            self.scroll = scroll

            split = QSplitter()
            split.addWidget(left)
            split.addWidget(scroll)
            split.setSizes([320, 1080])

            root = QWidget()
            lay = QVBoxLayout(root)
            lay.addLayout(top)
            lay.addWidget(split)
            self.setCentralWidget(root)
            self.statusBar().showMessage("Сеть не используется. Tesseract: " + (tesseract_exe() or "не найден"))

        def open_pdf(self):
            path, _ = QFileDialog.getOpenFileName(self, "PDF", "", "PDF (*.pdf)")
            if not path:
                return
            try:
                doc = pdfium.PdfDocument(path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Не удалось открыть", f"{os.path.basename(path)}\n{e}")
                return
            if self.doc:
                self.doc.close()
            self.doc, self.path = doc, path
            self.infos = page_info(doc)
            self.pages.clear()
            for p in self.infos:
                self.pages.addItem(f"Лист {p['page']}: {p['format']}, поворот {p['rotation']}°, текст {p['text_layer']}")
            self.pages.setCurrentRow(0)

        def show_page(self, *_):
            if not self.doc or self.pages.currentRow() < 0:
                return
            i = self.pages.currentRow()
            t0 = time.perf_counter()
            img = render(self.doc, i, self.dpi.value())
            dt = time.perf_counter() - t0
            self.current_img = img
            if getattr(self, "boxes_pdf_page", None) == i and self.boxes_pdf:
                img = draw_frames(img, boxes_to_pixels(self.doc, i, self.boxes_pdf, self.dpi.value()))
            self.view.setPixmap(to_pixmap(img))
            p = self.infos[i]
            self.info.setPlainText(
                f"Файл: {os.path.basename(self.path)}\nЛист: {p['page']}\nРазмер: {p['size_mm'][0]}×{p['size_mm'][1]} мм\n"
                f"Формат: {p['format']}\nПоворот: {p['rotation']}°\nСимволов в текстовом слое: {p['text_chars']}\n"
                f"Отрисовка: {dt:.2f} с при {self.dpi.value()} dpi")

        def do_search(self):
            if not self.doc:
                return
            i = max(0, self.pages.currentRow())
            self.boxes_pdf = find_boxes(self.doc, i, self.search.text().strip())
            self.boxes_pdf_page = i
            self.show_page()
            self.statusBar().showMessage(f"Найдено на листе: {len(self.boxes_pdf)}")
            if self.boxes_pdf:
                x1, y1, _, _ = boxes_to_pixels(self.doc, i, self.boxes_pdf[:1], self.dpi.value())[0]
                self.scroll.horizontalScrollBar().setValue(int(x1) - 300)
                self.scroll.verticalScrollBar().setValue(int(y1) - 200)

        def do_ocr(self):
            if not self.doc or not getattr(self, "boxes_pdf", None):
                QMessageBox.information(self, "OCR", "Сначала найдите надпись — распознаётся её фрагмент.")
                return
            i = self.boxes_pdf_page
            big = render(self.doc, i, 300)
            a, b, c, d = boxes_to_pixels(self.doc, i, self.boxes_pdf[:1], 300)[0]
            res = ocr(big.crop((max(0, a - 20), max(0, b - 20), c + 20, d + 20)))
            if res["ok"]:
                QMessageBox.information(self, "OCR", f"Текстовый слой: {self.search.text()}\nOCR ({res['seconds']} с): {res['text'].strip()}")
            else:
                QMessageBox.warning(self, "OCR", res["error"])

    app = QApplication(sys.argv)
    w = Main()
    w.show()
    return app.exec()


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
