"""Точка входа. `--selftest <pdf> <out.json>` — проверка сборки без окна (используется в CI)."""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path


def selftest(pdf: str, out: str) -> int:
    from . import VERSION, paths
    from .data import AppDb, ProjectObject
    from .documents.intake import add_files

    rep: dict = {"version": VERSION}
    try:
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "Объект проверки"
            obj = ProjectObject.create(folder, "Самопроверка")
            res = add_files(obj, [Path(pdf), Path(pdf)])
            rep["intake"] = [r.__dict__ for r in res]
            obj.set_only_local(True)
            obj.close()
            obj = ProjectObject.open(folder)
            rep["only_local"] = obj.only_local
            rep["summary"] = obj.summary()
            rep["pages"] = [dict(p) for f in obj.files() for p in obj.pages(f.id)]
            rep["tesseract"] = paths.tesseract_exe()
            obj.close()
            db = AppDb(Path(td) / "app.db")
            db.remember_object(folder, "Самопроверка")
            rep["recent"] = len(db.recent_objects())
            db.close()
        rep["ok"] = True
    except Exception:  # noqa: BLE001 — фиксируем любую ошибку самопроверки
        rep["ok"] = False
        rep["error"] = traceback.format_exc()
    Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if rep["ok"] else 1


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3])
    from PySide6.QtWidgets import QApplication

    from .data import AppDb
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SS Drawing Checker")
    w = MainWindow(AppDb())
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
