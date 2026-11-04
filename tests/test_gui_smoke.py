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
    for _ in range(6000):              # до 60 с: OCR скана и штампа-картинки
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
    w.details.xlsx.confirm()
    assert w.obj.xlsx_tables(xl.data(0, Qt.UserRole)[1])[0]["confirmed"] == 1
    # «Состав комплекта»: листы с классификацией, подтверждение выбранных, открытие листа
    w.tabs.setCurrentIndex(1)
    comp = w.composition
    assert comp.table.rowCount() == 5 and "Листов: 5" in comp.summary.text()
    assert comp.table.item(0, 3).text() == "НСС" and comp.table.item(0, 4).text() == "СС"
    comp.table.selectRow(1)
    pid2 = comp.table.item(1, 0).data(Qt.UserRole)[2]
    comp.confirm_selected()
    assert w.obj.classification(pid2).source == "пользователь"
    assert comp.xlsx.rowCount() == 2 and comp.xlsx.item(0, 4).text() == "да"
    comp._open(1, 0)
    assert w.details.currentWidget() is w.details.stamp
    w.details.stamp.confirm()
    assert w.obj.stamp(pid2)["confirmed"] is False or w.obj.stamp(pid2)["recognized"] is False
    w.on_tree_click(top.child(1), 0)
    assert w.sheet.doc is not None
    w.sheet.find.setText("ККС-49")
    w.sheet.do_find()
    assert w.sheet.found.text() == "найдено: 1"
    # «Замечания»: проверка в фоне, этапы, карточка, место на листе, рамка вручную
    w.composition.b_check.click()
    assert w.tabs.currentWidget() is w.results
    res = w.results
    for _ in range(6000):
        QCoreApplication.processEvents()
        if res.thread is None:
            break
        res.thread.wait(10)
    assert res.thread is None
    assert res.stages.rowCount() == 10
    assert res.table.rowCount() >= 3
    rules = {res.table.item(r, 3).text() for r in range(res.table.rowCount())}
    assert {"БП-03", "БП-07"} <= rules
    row = next(r for r in range(res.table.rowCount()) if res.table.item(r, 3).text() == "БП-03")
    res.table.selectRow(row)
    assert res.places.count() >= 1 and not res.places.item(0).icon().isNull()     # скриншот места
    res.places.setCurrentRow(0)
    res.ask_frame()
    assert w.sheet.picking and w.sheet.rects
    w.sheet.picking = False
    w.on_rect_picked((100.0, 100.0, 200.0, 150.0))
    pid = res.places.item(0).data(Qt.UserRole)["id"]
    assert w.obj.con.execute("SELECT method FROM place WHERE id=?", (pid,)).fetchone()[0] == "вручную"
    res.decide("принято")
    fid = res.fid
    assert w.obj.con.execute("SELECT decision FROM finding WHERE id=?", (fid,)).fetchone()[0] == "принято"
    w.close()


def test_instructions_tab(tmp_path, sample_pdf, monkeypatch):
    """Инструкция: загрузка MD, разбор, конструктор, версия; проверка выполняет требования и показывает покрытие."""
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication, QFileDialog

    from app.data import AppDb, ProjectObject
    from app.documents.intake import add_files
    from app.requirements import library as lib
    from app.ui.instructions import RequirementDialog
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    md = tmp_path / "Оформление.md"
    md.write_text("# Оформление\n- Стадия в штампе — «П».\n- Высоту камер согласовать.\n"
                  "- Не допускается надпись «ККС-49».\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(md), "")))
    w = MainWindow(AppDb(tmp_path / "app.db"))
    panel = w.instructions
    panel.load_file()
    assert panel.list.count() == 1 and panel.table.rowCount() == 3
    assert panel.table.item(0, 2).text() == "утверждён" and panel.table.item(1, 2).text() == "не разобран"
    # конструктор: второму пункту задаём операцию ВО-02 и утверждаем
    dlg = RequirementDialog(panel, panel.reqs[1])
    dlg.op.setCurrentIndex(dlg.op.findData("ВО-02"))
    dlg.widgets["text"][1].setText("Высота")
    dlg.status.setCurrentText("утверждён")
    dlg.sev.setCurrentText("Существенно")
    panel.reqs[1] = dlg.result_req()
    panel.fill_table()
    panel.save()
    ins = lib.get(w.appdb.con, panel.iid)
    assert ins.ver == 2 and ins.requirements[1].op == "ВО-02" and ins.requirements[1].severity == "Существенно"
    assert panel.versions.count() == 2
    # проверка с инструкцией
    obj = ProjectObject.create(tmp_path / "о", "т")
    add_files(obj, [sample_pdf])
    w.set_object(obj)
    w.run_checks()
    res = w.results
    for _ in range(6000):
        QCoreApplication.processEvents()
        if res.thread is None:
            break
        res.thread.wait(10)
    codes = [res.stages.item(r, 0).text() for r in range(res.stages.rowCount())]
    assert codes.count("ВО-01") == 1 and codes.count("ВО-02") == 2
    rules = {res.table.item(r, 3).text() for r in range(res.table.rowCount())}
    assert "ВО-02 · Оформление" in rules                       # «ККС-49» есть на листе 2
    applied = obj.con.execute("SELECT name, ver FROM applied_instruction").fetchall()
    assert [tuple(r) for r in applied] == [("Оформление", 2)]
    # выключенная инструкция не применяется
    panel.list.item(0).setCheckState(Qt.Unchecked)
    assert lib.enabled(w.appdb.con) == []
    w.close()
