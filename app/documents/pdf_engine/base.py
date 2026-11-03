"""Интерфейс движка PDF (ТЗ 3.2, Р43): остальные модули не знают, какая библиотека читает PDF."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image

MM_PER_PT = 25.4 / 72

# Координаты везде — пункты страницы PDF без учёта поворота (как в самом файле):
# (x0, y0, x1, y1), начало координат — левый нижний угол.
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class PageInfo:
    page_no: int            # с 1
    width_pt: float         # размер как при отображении (с учётом поворота)
    height_pt: float
    rotation: int           # 0, 90, 180, 270
    text_chars: int

    @property
    def width_mm(self) -> int:
        return round(self.width_pt * MM_PER_PT)

    @property
    def height_mm(self) -> int:
        return round(self.height_pt * MM_PER_PT)

    @property
    def has_text(self) -> bool:
        return self.text_chars > 0


@dataclass(frozen=True)
class TextHit:
    text: str
    rect: Rect


class PdfError(Exception):
    """Файл не прочитан. `reason` — понятная пользователю причина на русском."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class PdfDocument(Protocol):
    def page_count(self) -> int: ...
    def page_info(self, index: int) -> PageInfo: ...
    def page_text(self, index: int) -> str: ...
    def text_in_rect(self, index: int, rect: Rect) -> str: ...
    def search(self, index: int, needle: str, limit: int = 100) -> list[TextHit]: ...
    def render(self, index: int, dpi: int) -> Image.Image: ...
    def to_pixels(self, index: int, rects: list[Rect], dpi: int) -> list[tuple[int, int, int, int]]: ...
    def close(self) -> None: ...


class PdfEngine(Protocol):
    name: str

    def open(self, path: str) -> PdfDocument: ...
