"""Приём файлов (ТЗ 4.1, шаг 1): копия в объект, контрольная сумма, проверка читаемости, сведения о листах.
Ошибка одного файла не останавливает остальные."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..data import ObjectError, ProjectObject
from .formats import sheet_format
from .pdf_engine import PdfError, engine


@dataclass
class IntakeResult:
    name: str
    status: str            # прочитан | ошибка | уже есть
    message: str = ""
    pages: int = 0
    extract: object = None


def read_file(obj: ProjectObject, file_id: int) -> IntakeResult:
    f = next(x for x in obj.files() if x.id == file_id)
    path = obj.file_path(f)
    if f.kind == "PDF":
        try:
            doc = engine().open(str(path))
        except PdfError as e:
            obj.set_file_status(f.id, "ошибка", e.reason)
            return IntakeResult(f.name, "ошибка", e.reason)
        try:
            pages = []
            for i in range(doc.page_count()):
                info = doc.page_info(i)
                pages.append({"page_no": info.page_no, "width_pt": info.width_pt, "height_pt": info.height_pt,
                              "width_mm": info.width_mm, "height_mm": info.height_mm,
                              "format": sheet_format(info.width_mm, info.height_mm),
                              "rotation": info.rotation, "text_chars": info.text_chars})
        finally:
            doc.close()
        obj.replace_pages(f.id, pages)
        obj.set_file_status(f.id, "прочитан")
        return IntakeResult(f.name, "прочитан", pages=len(pages))
    if f.kind in ("XLSX", "DOCX"):
        # На шаге 1 — только проверка, что это корректный документ Office; разбор — шаг 2.
        try:
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
            if bad:
                raise zipfile.BadZipFile(bad)
        except (zipfile.BadZipFile, OSError):
            obj.set_file_status(f.id, "ошибка", "файл повреждён или это не документ Office")
            return IntakeResult(f.name, "ошибка", "файл повреждён или это не документ Office")
        obj.set_file_status(f.id, "прочитан")
        return IntakeResult(f.name, "прочитан")
    obj.set_file_status(f.id, "прочитан")
    return IntakeResult(f.name, "прочитан")


def add_files(obj: ProjectObject, paths: list[Path],
              progress: Callable[[int, int, str], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> list[IntakeResult]:
    results = []
    total = len(paths)
    for n, p in enumerate(paths, 1):
        if cancelled and cancelled():
            break
        if progress:
            progress(n, total, Path(p).name)
        try:
            row, new = obj.add_source(Path(p))
        except ObjectError as e:
            results.append(IntakeResult(Path(p).name, "ошибка", str(e)))
            continue
        except OSError as e:
            results.append(IntakeResult(Path(p).name, "ошибка", f"не удалось скопировать: {e.strerror}"))
            continue
        if not new:
            results.append(IntakeResult(row.name, "уже есть", "этот файл уже добавлен в объект"))
            continue
        r = read_file(obj, row.id)
        if r.status == "прочитан":
            from .extract import extract_file   # шаг 2: текст, OCR, штампы, таблицы
            ex = extract_file(obj, row.id, cancelled)
            r.extract = ex
            if row.kind == "PDF" and not (cancelled and cancelled()):
                from .classify import classify_file    # шаг 3: марка, раздел, подсистемы, типы
                classify_file(obj, row.id)
            if ex.errors:
                r.message = "; ".join(ex.errors)
        results.append(r)
    return results
