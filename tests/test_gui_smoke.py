"""Окно открывается, объект создаётся, файл принимается в фоне, лист показывается (offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_main_window_flow(tmp_path, sample_pdf, sample_xlsx, monkeypatch):
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication, QInputDialog, QFileDialog

    from app.data import AppDb
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Аэропорт", True)))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path)))
    w = MainWindow(AppDb(tmp_path / "app.db"))
    w.new_object()
    assert w.obj is not None and w.obj.name == "Аэропорт"
    w.start_intake([sample_pdf, sample_xlsx])
    for _ in range(500):
        QCoreApplication.processEvents()
        if w.thread is None:
            break
        w.thread.wait(10)
    assert w.thread is None
    top = w.tree.topLevelItem(0)
    assert top.text(4) == "прочитан" and top.childCount() == 5
    assert "Схема кабельной канализации" in top.child(0).text(0)
    w.on_tree_click(top.child(0), 0)
    assert w.details.currentWidget() is w.details.stamp
    assert w.details.stamp.table.rowCount() >= 9
    # правка поля штампа сохраняется
    cell = w.details.stamp.table.item(5, 1)          # «лист»
    cell.setText("7")
    pid = top.child(0).data(0, Qt.UserRole)[3]
    assert w.obj.stamp(pid)["fields"]["лист"]["value"] == "7"
    xl = w.tree.topLevelItem(1)
    w.on_tree_click(xl.child(0), 0)
    assert w.details.xlsx.table.rowCount() == 2
    w.on_tree_click(top.child(1), 0)
    assert w.sheet.doc is not None
    w.sheet.find.setText("ККС-49")
    w.sheet.do_find()
    assert w.sheet.found.text() == "найдено: 1"
    w.close()
