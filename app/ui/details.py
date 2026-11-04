"""Панель сведений: поля штампа листа (с правкой) и таблицы XLSX со схемой столбцов."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QPushButton,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..data import ProjectObject

STAMP_ORDER = ["шифр", "объект", "раздел", "наименование листа", "стадия", "лист", "листов", "организация", "формат"]
LOW = QBrush(QColor("#fff3cd"))
USER = QBrush(QColor("#e7f1fb"))


class StampPanel(QWidget):
    edited = Signal()

    def __init__(self):
        super().__init__()
        self.obj: ProjectObject | None = None
        self.page_id: int | None = None
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Поле штампа", "Значение", "Источник"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemChanged.connect(self.on_changed)
        self.sigs = QLabel()
        self.sigs.setWordWrap(True)
        self.b_confirm = QPushButton("Штамп проверен")
        self.b_confirm.setToolTip("Подтвердить, что поля штампа прочитаны верно (особенно после OCR)")
        self.b_confirm.clicked.connect(self.confirm)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.note)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(self.sigs, 1)
        row.addWidget(self.b_confirm)
        lay.addLayout(row)

    def confirm(self):
        st = self.obj.stamp(self.page_id) if self.obj and self.page_id else None
        if st and st["recognized"]:
            self.obj.confirm_stamp(self.page_id)
            self.b_confirm.setText("Штамп подтверждён")
            self.b_confirm.setEnabled(False)
            self.edited.emit()

    def show_page(self, obj: ProjectObject, page_id: int, extract_status: str | None):
        self.obj, self.page_id = obj, page_id
        st = obj.stamp(page_id)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.b_confirm.setEnabled(bool(st and st["recognized"] and not st["confirmed"]))
        self.b_confirm.setText("Штамп подтверждён" if st and st["confirmed"] else "Штамп проверен")
        if not st:
            self.note.setText("Данные листа ещё не извлечены.")
            self.table.blockSignals(False)
            return
        src = "текстовый слой" if st["source"] == "слой" else "OCR — проверьте значения"
        head = f"Извлечение: {extract_status or '—'}. Штамп: " + ("распознан" if st["recognized"] else "не распознан")
        self.note.setText(head + f" ({src}). " + (st["note"] or "") + " Значения можно исправить двойным щелчком.")
        names = STAMP_ORDER + [n for n in st["fields"] if n not in STAMP_ORDER]
        for name in names:
            f = st["fields"].get(name)
            r = self.table.rowCount()
            self.table.insertRow(r)
            a = QTableWidgetItem(name)
            a.setFlags(a.flags() & ~Qt.ItemIsEditable)
            b = QTableWidgetItem(f["value"] if f else "")
            b.setData(Qt.UserRole, name)
            c = QTableWidgetItem("правка" if f and f["edited"] else (f["confidence"] if f else "не найдено"))
            c.setFlags(c.flags() & ~Qt.ItemIsEditable)
            if not f or f["confidence"] == "низкая":
                b.setBackground(LOW)
            elif f["edited"]:
                b.setBackground(USER)
            for col, it in enumerate((a, b, c)):
                self.table.setItem(r, col, it)
        self.table.blockSignals(False)
        sig = "; ".join(f"{s['role']}: {s['surname'] or '—'} {s['date']}" for s in st["signatures"])
        self.sigs.setText("Подписи: " + (sig or "не найдены"))

    def on_changed(self, item: QTableWidgetItem):
        if item.column() != 1 or not self.obj:
            return
        self.obj.set_stamp_field(self.page_id, item.data(Qt.UserRole), item.text().strip())
        self.table.blockSignals(True)
        item.setBackground(USER)
        self.table.item(item.row(), 2).setText("правка")
        self.table.blockSignals(False)
        self.edited.emit()


class XlsxPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.obj = None
        self.sheet = QComboBox()
        self.sheet.currentIndexChanged.connect(self.show_table)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.b_role = QPushButton("Назначение столбца…")
        self.b_role.clicked.connect(self.edit_role)
        self.b_ok = QPushButton("Подтвердить схему")
        self.b_ok.clicked.connect(self.confirm)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(self.sheet, 1)
        top.addWidget(self.b_role)
        top.addWidget(self.b_ok)
        lay.addLayout(top)
        lay.addWidget(self.info)
        lay.addWidget(self.table, 1)

    def show_file(self, obj: ProjectObject, file_id: int):
        self.obj = obj
        self.tables = obj.xlsx_tables(file_id)
        self.sheet.blockSignals(True)
        self.sheet.clear()
        for t in self.tables:
            self.sheet.addItem(t["sheet"])
        self.sheet.blockSignals(False)
        self.show_table(0)

    def show_table(self, idx: int):
        if not self.obj or idx < 0 or idx >= len(self.tables):
            self.table.clear()
            return
        t = self.tables[idx]
        schema = json.loads(t["schema"])
        letters = list(schema)
        unknown = [schema[c]["header"] for c in letters if not schema[c]["role"]]
        self.info.setText(f"Строка заголовка: {t['header_row']}. Схема "
                          + ("подтверждена." if t["confirmed"] else
                             f"определена автоматически (уверенность: {t['confidence']}) — проверьте и подтвердите.")
                          + (f" Столбцы без назначения: {', '.join(unknown)}." if unknown else ""))
        rows = self.obj.xlsx_rows(t["id"])
        self.table.setColumnCount(len(letters) + 1)
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels(["Строка"] + [
            f"{c}: {schema[c]['header']}\n→ {schema[c]['role'] or '?'}" for c in letters])
        for r, row in enumerate(rows):
            cells = json.loads(row["cells"])
            self.table.setItem(r, 0, QTableWidgetItem(str(row["row_no"])))
            for k, c in enumerate(letters, 1):
                self.table.setItem(r, k, QTableWidgetItem(cells.get(c, "")))
        self.table.resizeColumnsToContents()


    def _current(self):
        idx = self.sheet.currentIndex()
        return self.tables[idx] if self.obj and 0 <= idx < len(self.tables) else None

    def edit_role(self):
        from ..documents.xlsx import COLUMN_SYNONYMS
        t = self._current()
        if not t:
            return
        schema = json.loads(t["schema"])
        cols = [f"{c}: {v['header']} → {v['role'] or '?'}" for c, v in schema.items()]
        col, ok = QInputDialog.getItem(self, "Назначение столбца", "Столбец:", cols, 0, False)
        if not ok:
            return
        letter = col.split(":")[0]
        roles = ["(нет)"] + [r for r, _ in COLUMN_SYNONYMS]
        role, ok = QInputDialog.getItem(self, "Назначение столбца", f"Что в столбце {letter}?", roles, 0, False)
        if not ok:
            return
        schema[letter]["role"] = None if role == "(нет)" else role
        self.obj.confirm_xlsx_table(t["id"], schema)
        self.refresh_keep(t["file_id"])

    def confirm(self):
        t = self._current()
        if t:
            self.obj.confirm_xlsx_table(t["id"])
            self.refresh_keep(t["file_id"])

    def refresh_keep(self, file_id):
        idx = self.sheet.currentIndex()
        self.show_file(self.obj, file_id)
        self.sheet.setCurrentIndex(idx)


class DetailsPanel(QStackedWidget):
    def __init__(self):
        super().__init__()
        self.empty = QLabel("Выберите лист или таблицу.")
        self.stamp = StampPanel()
        self.xlsx = XlsxPanel()
        for w in (self.empty, self.stamp, self.xlsx):
            self.addWidget(w)

    def show_page(self, obj, page_id, status):
        self.stamp.show_page(obj, page_id, status)
        self.setCurrentWidget(self.stamp)

    def show_xlsx(self, obj, file_id):
        self.xlsx.show_file(obj, file_id)
        self.setCurrentWidget(self.xlsx)

    def clear(self):
        self.setCurrentWidget(self.empty)
