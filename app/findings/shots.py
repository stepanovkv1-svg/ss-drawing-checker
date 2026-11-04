"""Скриншот места (ТЗ 4.7): область места с полем контекста, красная рамка по координатам места.
Отрисовка — pypdfium2 по части листа, без отрисовки целого листа большого формата."""
from __future__ import annotations

import io

from PIL import ImageDraw

from ..documents.pdf_engine import MM_PER_PT

CONTEXT_MM = 25         # поле контекста вокруг места
SHOT_DPI = 150
WHOLE_DPI = 30          # «весь лист» — уменьшенная копия
MAX_SIDE = 1400


def _png(img) -> bytes:
    if max(img.size) > MAX_SIDE:
        k = MAX_SIDE / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def make_shot(doc, index: int, rect) -> bytes:
    """rect — пункты PDF или None (весь лист)."""
    if rect is None:
        return _png(doc.render(index, WHOLE_DPI))
    # координаты отображения в пунктах: to_pixels при 72 dpi
    x0, y0, x1, y1 = doc.to_pixels(index, [rect], 72)[0]
    ctx = CONTEXT_MM / MM_PER_PT
    box = (x0 - ctx, y0 - ctx, x1 + ctx, y1 + ctx)
    info = doc.page_info(index)
    box = (max(0, box[0]), max(0, box[1]), min(info.width_pt, box[2]), min(info.height_pt, box[3]))
    img = doc.render_region(index, SHOT_DPI, box)
    k = SHOT_DPI / 72
    d = ImageDraw.Draw(img)
    pad = 3
    d.rectangle(((x0 - box[0]) * k - pad, (y0 - box[1]) * k - pad, (x1 - box[0]) * k + pad, (y1 - box[1]) * k + pad),
                outline=(220, 0, 0), width=3)
    return _png(img)
