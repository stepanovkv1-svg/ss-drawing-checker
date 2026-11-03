"""Определение формата листа по ГОСТ 2.301: основные и кратные форматы."""
from __future__ import annotations

BASE = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "A1": (594, 841), "A0": (841, 1189)}
TOL_MM = 3


def sheet_format(w_mm: float, h_mm: float) -> str:
    a, b = sorted((w_mm, h_mm))
    for name, (s, l) in BASE.items():
        if abs(a - s) <= TOL_MM and abs(b - l) <= TOL_MM:
            return name
    # Кратный формат: короткая сторона — длинная сторона основного, длинная — кратна короткой
    # (A3x4 = 420 x 1189, A2x3 = 594 x 1261).
    for name, (s, l) in BASE.items():
        if abs(a - l) <= TOL_MM:
            k = b / s
            if round(k) >= 3 and abs(b - round(k) * s) <= TOL_MM * round(k):
                return f"{name}x{round(k)}"
    return f"нестандартный {round(a)}x{round(b)}"
