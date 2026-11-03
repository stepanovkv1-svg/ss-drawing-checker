"""OCR через Tesseract из состава сборки (ТЗ 4.2). Без сети, без скачивания языков.

Итог Э0: на целых листах распознаётся 56–77% слов, поэтому результат OCR несёт уверенность,
а неуверенные слова (ниже порога) идут в «ручную проверку» и замечаний не создают."""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

from PIL import Image

from .. import paths

CONFIDENT = 70.0          # % — ниже считается неуверенным
LANG = "rus+eng"


@dataclass
class OcrWord:
    text: str
    px: tuple[int, int, int, int]     # пиксели изображения
    confidence: float
    line: tuple[int, int, int]        # блок, абзац, строка — для склейки в строки


class OcrUnavailable(Exception):
    pass


def available() -> bool:
    return paths.tesseract_exe() is not None


def recognize(img: Image.Image, psm: int = 11, timeout: int = 600) -> list[OcrWord]:
    exe = paths.tesseract_exe()
    if not exe:
        raise OcrUnavailable("Tesseract не найден в составе программы")
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.png")
        img.save(src)
        cmd = [exe, src, "stdout", "-l", LANG, "--psm", str(psm)]
        tdd = paths.tessdata_dir()
        if tdd:
            cmd += ["--tessdata-dir", tdd]
        cmd += ["tsv"]
        flags = 0x08000000 if os.name == "nt" else 0          # без окна консоли
        p = subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=timeout)
    if p.returncode != 0:
        raise OcrUnavailable(p.stderr.decode("utf-8", "replace")[-300:])
    words = []
    for row in p.stdout.decode("utf-8", "replace").splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        x, y, w, h = map(int, f[6:10])
        words.append(OcrWord(f[11], (x, y, x + w, y + h), float(f[10]), (int(f[2]), int(f[3]), int(f[4]))))
    return words


def recognize_fragment(img: Image.Image) -> tuple[str, float]:
    """Повторное распознавание фрагмента (ТЗ 8.1 «Просмотр листа»): текст и средняя уверенность.
    Вертикальный фрагмент пробуется в обоих поворотах."""
    variants = [img]
    if img.height > 1.5 * img.width:
        variants = [img.rotate(90, expand=True, fillcolor="white"), img.rotate(270, expand=True, fillcolor="white")]
    best = ("", 0.0)
    for v in variants:
        words = recognize(v, psm=6)
        if not words:
            continue
        conf = sum(w.confidence for w in words) / len(words)
        lines: dict[tuple, list[str]] = {}
        for w in words:
            lines.setdefault(w.line, []).append(w.text)
        text = "\n".join(" ".join(t) for t in lines.values())
        if conf > best[1]:
            best = (text, round(conf, 1))
    return best
