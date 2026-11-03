from .base import MM_PER_PT, PageInfo, PdfDocument, PdfEngine, PdfError, Rect, TextHit
from .pdfium_engine import PdfiumEngine

_default: PdfEngine = PdfiumEngine()


def engine() -> PdfEngine:
    """Текущий движок PDF. Замена библиотеки — только здесь."""
    return _default


__all__ = ["MM_PER_PT", "PageInfo", "PdfDocument", "PdfEngine", "PdfError", "Rect", "TextHit", "engine"]
