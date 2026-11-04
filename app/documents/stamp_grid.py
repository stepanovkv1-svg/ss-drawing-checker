"""Основная надпись по линиям рамки (ГОСТ Р 21.101-2020, формы 3 и 6) — запасной путь, когда подписи граф
не читаются из текстового слоя: штамп нарисован линиями без текста (шрифты SHX из AutoCAD) или у шрифта
нет таблицы кодировки (в слое «мусор» вместо букв).

Порядок: по изображению правого нижнего угла находятся нижняя и правая линии рамки, затем по наличию
характерных линий определяется форма (3 — первый лист, 6 — последующие листы). Каждая графа берётся
по стандартным размерам; текст графы — из текстового слоя, если он читается, иначе OCR графы отдельно
(OCR небольшой ячейки без линий таблицы точнее, чем OCR всего угла листа).
"""
from __future__ import annotations

import re

import numpy as np
from PIL import ImageOps

from . import ocr
from .pdf_engine import MM_PER_PT, TextRun
from .textnorm import norm_code
from .stamp import Item, Stamp, StampField, _join

PT_PER_MM = 1 / MM_PER_PT
FIND_DPI = 150
READ_DPI = 300
INSET_MM = 0.7

# Графы относительно правого нижнего угла основной надписи: (r_лево, r_право, b_низ, b_верх), мм.
FORM3 = {
    "шифр": (120, 0, 40, 55, 7),
    "объект": (120, 0, 30, 40, 6),
    "раздел": (120, 50, 15, 30, 6),
    "наименование листа": (120, 50, 0, 15, 6),
    "стадия": (50, 35, 15, 25, 8),
    "лист": (35, 20, 15, 25, 7),
    "листов": (20, 0, 15, 25, 7),
    "организация": (50, 0, 0, 15, 6),
}
FORM6 = {
    "шифр": (120, 10, 0, 15, 7),
    "лист": (10, 0, 0, 8.5, 7),
}
SIGN_ROWS = [(25, 30), (20, 25), (15, 20), (10, 15), (5, 10), (0, 5)]
SIGN_COLS = {"роль": (185, 165), "фамилия": (165, 145), "дата": (130, 120)}
FORMAT_CELL = (90, 0, -5, 0)
STAGES = {"П", "Р", "РП", "ЭП", "ПД", "РД"}
DIGITS = ["-c", "tessedit_char_whitelist=0123456789"]

_GARBLED = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f-�]")


def garbled(text: str) -> bool:
    """Текст из слоя без кодировки: управляющие символы вместо букв."""
    return bool(_GARBLED.search(text))


def layer_is_garbled(runs: list[TextRun]) -> bool:
    if not runs:
        return False
    return sum(1 for r in runs if garbled(r.text)) / len(runs) > 0.3


def _dark(img) -> np.ndarray:
    return np.asarray(img.convert("L")) < 150


def _lines(idx: np.ndarray) -> list[int]:
    """Номера строк/столбцов толстой линии → по одному на линию (крайний внешний)."""
    out: list[int] = []
    for v in sorted(idx.tolist(), reverse=True):
        if not out or out[-1] - v > 3:
            out.append(v)
    return out


def _corners(mask: np.ndarray, k: float) -> list[tuple[int, int]]:
    """Кандидаты в правый нижний угол основной надписи: пересечения длинных линий, ближние к углу листа первыми.
    Линия края листа или внешней рамки тоже попадает в кандидаты — форма по ним не определится."""
    h, w = mask.shape
    span = mask[:, max(0, w - int(190 * k)):]
    rows = _lines(np.where(span.sum(axis=1) >= 150 * k)[0])[:4]
    out = []
    for y in rows:
        col_zone = mask[max(0, y - int(15 * k)):y + 1, :]
        cols = _lines(np.where(col_zone.sum(axis=0) >= 0.9 * col_zone.shape[0])[0])[:4]
        out += [(x, y) for x in cols]
    return out


def _hline(mask, ox, oy, k, b, r0, r1) -> bool:
    y = oy - b * k
    xs = slice(max(0, int(ox - r0 * k)), max(0, int(ox - r1 * k)))
    band = mask[max(0, int(y - k)):int(y + k) + 1, xs]
    return band.size > 0 and band.any(axis=0).mean() >= 0.85


def _vline(mask, ox, oy, k, r, b0, b1) -> bool:
    x = ox - r * k
    ys = slice(max(0, int(oy - b1 * k)), max(0, int(oy - b0 * k)))
    band = mask[ys, max(0, int(x - k)):int(x + k) + 1]
    return band.size > 0 and band.any(axis=1).mean() >= 0.85


def _form(mask, ox, oy, k) -> str | None:
    if _hline(mask, ox, oy, k, 55, 185, 0) and _vline(mask, ox, oy, k, 50, 0, 30) and _vline(mask, ox, oy, k, 120, 0, 55):
        return "3"
    if _hline(mask, ox, oy, k, 15, 185, 0) and _vline(mask, ox, oy, k, 10, 0, 15) and _vline(mask, ox, oy, k, 120, 0, 15):
        return "6"
    return None


def _fix_ocr(t: str) -> str:
    """Типовые ошибки OCR на чертёжном шрифте: «С» в начале слова читается как «(»,
    «О» внутри кириллического обозначения — как ноль."""
    t = re.sub(r"(?<![\w(])\((?=[а-яё])", "С", t)
    t = re.sub(r"(?<![\w(])\((?=[А-ЯЁ][а-яё])", "", t)
    t = re.sub(r"(?<=[А-ЯЁ.])0(?=[А-ЯЁ]|$)", "О", t)
    t = re.sub(r"(?<=[А-ЯЁ])0(?=[.\s]|$)", "О", t)
    return t


def parse_stamp_grid(doc, index: int, runs: list[TextRun], page_w_pt: float, page_h_pt: float,
                     use_ocr: bool = True) -> Stamp | None:
    """Ищет основную надпись по линиям. None — рамка или форма не найдены."""
    zw, zh = min(page_w_pt, 240 * PT_PER_MM), min(page_h_pt, 90 * PT_PER_MM)
    zx, zy = page_w_pt - zw, page_h_pt - zh
    k = FIND_DPI / 25.4
    mask = _dark(doc.render_region(index, FIND_DPI, (zx, zy, page_w_pt, page_h_pt)))
    corner = form = None
    for c in _corners(mask, k):
        form = _form(mask, *c, k)
        if form:
            corner = c
            break
    if not corner:
        return None
    # угол основной надписи в координатах отображения листа, пт
    cx = zx + corner[0] / k * PT_PER_MM
    cy = zy + corner[1] / k * PT_PER_MM

    def box(r0, r1, b0, b1, inset=INSET_MM):
        return (cx - (r0 - inset) * PT_PER_MM, cy - (b1 - inset) * PT_PER_MM,
                cx - (r1 + inset) * PT_PER_MM, cy - (b0 + inset) * PT_PER_MM)

    # изображение всей основной надписи для OCR граф
    bad_layer = layer_is_garbled(runs)
    used_ocr = False
    sbox = box(190, -2, -6, 60 if form == "3" else 20, inset=0)
    big = None
    kk = READ_DPI / 72

    def read(cell, psm=7, extra=None) -> tuple[str, str] | None:
        """Текст графы и уверенность: из слоя, если читается, иначе OCR."""
        nonlocal big, used_ocr
        x0, y0, x1, y1 = box(*cell[:4])
        inside = [r for r in runs if x0 <= (r.disp[0] + r.disp[2]) / 2 <= x1 and y0 <= (r.disp[1] + r.disp[3]) / 2 <= y1]
        good = [] if bad_layer else [r for r in inside if not garbled(r.text) and r.text.strip()]
        if good and len(good) == len(inside):
            items = [Item(r.text, r.rect, (page_w_pt - r.disp[0]) * MM_PER_PT, (page_w_pt - r.disp[2]) * MM_PER_PT,
                          (page_h_pt - r.disp[3]) * MM_PER_PT) for r in good]
            return _join(items), "высокая"
        if not use_ocr or not ocr.available():
            return None
        if big is None:
            big = doc.render_region(index, READ_DPI, sbox).convert("L")
        crop = big.crop((int((x0 - sbox[0]) * kk), int((y0 - sbox[1]) * kk),
                         int((x1 - sbox[0]) * kk), int((y1 - sbox[1]) * kk)))
        if crop.width < 5 or crop.height < 5 or not (np.asarray(crop) < 150).any():
            return None
        # бинаризация и белое поле вокруг: на шрифтах ГОСТ (ISOCPEUR) это заметно точнее
        crop = ImageOps.expand(crop.point(lambda v: 0 if v < 170 else 255), border=20, fill=255)
        if extra == DIGITS:
            # номер листа: сначала без ограничения набора символов, затем только цифры и режим «один символ»
            for p_, e_ in ((psm, None), (psm, DIGITS), (10, DIGITS)):
                words = [w for w in ocr.recognize(crop, psm=p_, extra=e_) if re.search(r"\d", w.text)]
                if words:
                    break
        else:
            words = ocr.recognize(crop, psm=psm, extra=extra)
        words = [w for w in words if re.search(r"[0-9A-Za-zА-Яа-яЁё]", w.text)]
        if not words:
            return None
        used_ocr = True
        text = _fix_ocr(re.sub(r"\s+", " ", " ".join(w.text for w in words)).strip(" |[]_"))
        conf = sum(w.confidence for w in words) / len(words)
        return text, ("высокая" if conf >= ocr.CONFIDENT else "низкая")

    def rect(cell):
        x0, y0, x1, y1 = box(*cell[:4], inset=0)
        return doc.from_pixels(index, [(x0 * 4, y0 * 4, x1 * 4, y1 * 4)], 288)[0]

    st = Stamp(True)
    layout = FORM3 if form == "3" else FORM6
    for name, cell in layout.items():
        extra = DIGITS if name in ("лист", "листов") else None
        got = read(cell, psm=cell[4], extra=extra)
        if not got:
            continue
        value, conf = got
        if name == "организация" and not re.search(r"[А-Яа-яЁё]{3,}", value):
            continue                            # логотип вместо названия — OCR его не прочтёт
        if name == "стадия":
            value = norm_code(value)
            if value not in STAGES:
                conf = "низкая"
        if name in ("лист", "листов"):
            value = re.sub(r"\D", "", value)
            if not value:
                continue
        st.fields[name] = StampField(name, value, [rect(cell)], conf)

    fmt = read(FORMAT_CELL)
    if fmt:
        m = re.search(r"[АA]\s*(\d)\s*(?:[xх×]\s*(\d))?", fmt[0])
        if m:
            st.fields["формат"] = StampField("формат", "А" + m.group(1) + ("×" + m.group(2) if m.group(2) else ""),
                                             [rect(FORMAT_CELL)], fmt[1])

    if form == "3":
        for b0, b1 in SIGN_ROWS:
            role = read((*SIGN_COLS["роль"], b0, b1))
            if not role:
                continue
            name = read((*SIGN_COLS["фамилия"], b0, b1))
            date = read((*SIGN_COLS["дата"], b0, b1), extra=["-c", "tessedit_char_whitelist=0123456789."])
            st.signatures.append({"роль": role[0], "фамилия": name[0] if name else "",
                                  "дата": date[0] if date else "",
                                  "rects": [rect((*SIGN_COLS["роль"], b0, b1))]})

    kind = "форма 3, первый лист" if form == "3" else "форма 6, последующий лист"
    how = "текст граф прочитан OCR — проверьте значения" if used_ocr else "текст граф из текстового слоя"
    st.note = f"Основная надпись найдена по линиям рамки ({kind}); {how}."
    st.form = kind
    st.source = "OCR" if used_ocr else "слой"
    required = ("шифр", "лист", "наименование листа") if form == "3" else ("шифр", "лист")
    missing = [n for n in required if n not in st.fields]
    if missing:
        st.note += " Не найдены поля: " + ", ".join(missing) + "."
    return st
