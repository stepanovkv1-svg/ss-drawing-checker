"""Пути программы: ресурсы сборки и профиль пользователя (%LOCALAPPDATA%, решение по В5)."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def bundle_dir() -> Path:
    """Папка с ресурсами: _internal собранной программы или корень репозитория при запуске из исходников."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Настройки и база программы. Переопределяется SSDC_HOME (тесты, переносной режим)."""
    override = os.environ.get("SSDC_HOME")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SSDrawingChecker"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ssdrawingchecker"
    base.mkdir(parents=True, exist_ok=True)
    return base


def tesseract_exe() -> str | None:
    bundled = bundle_dir() / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")
    if bundled.exists():
        return str(bundled)
    return shutil.which("tesseract")


def tessdata_dir() -> str | None:
    d = bundle_dir() / "tesseract" / "tessdata"
    return str(d) if d.is_dir() else None
