"""Экран «Состав комплекта» (ТЗ 8.1, П1-11): листы по документам — марка, раздел, подсистемы, типы
с признаками, уверенностью, противоречиями, источником решения и версией словаря; спорное подсвечено;
подтверждение классификации, штампов и схем XLSX."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..data import ProjectObject
from ..documents.classify import DOUBT, SURE, UNKNOWN, classify_file, dictionary

COLORS = {DOUBT: QColor("#fff3cd"), UNKNOWN: QColor("#fde2cf")}
USER = QColor("#e7f1fb")
CONFLICT = QColor("#b00020")
COLS = ["Документ", "Лист", "Наименование", "Марка", "Раздел", "Подсистемы", "Типы", "Итог", "Штамп", "Источник"]
FILTERS = ["Все листы", "Требуют подтверждения", "С противоречиями", "Штамп нужно проверить"]


def _tip(title: str, ch) -> str:
    lines = [f"{title}: {', '.join(ch.value) or '—'} ({ch.confidence})"]
    lines += ["• " + e for e in ch.evidence]
    lines += ["⚠ " + c for c in ch.contradictions]
    return "\n".join(lines)


class ClassDialog(QDialog):
    """Изменение классификации выбранных листов. Пустое поле — оставить как есть."""

    def __init__(self, parent, first):
        super().__init__(parent)
        self.setWindowTitle("Классификация листов")
        d = dictionary()
        self.mark = QComboBox()
        self.mark.setEditable(True)
        self.mark.addItems([""] + sorted(d["marks"]))
        self.mark.setCurrentText(first.mark.value[0] if first and first.mark.value else "")
        self.section = QComboBox()
        self.section.addItem("", None)
        for code, name in d["sections"].items():
            self.section.addItem(f"{code} — {name}", code)
        if first and first.section.value:
            self.section.setCurrentIndex(max(0, self.section.findData(first.section.value[0])))
        self.subs = self._checklist({k: f"{k} — {v['name']}" for k, v in d["subsystems"].items()},
                                    first.subsystems.value if first else [])
        self.types = self._checklist({n: n for n, _, _ in d["doc_types"]}, first.doc_types.value if first else [])
        form = QFormLayout(self)
        form.addRow("Марка комплекта", self.mark)
        form.addRow("Раздел", self.section)
        form.addRow("Подсистемы", self.subs)
        form.addRow("Типы документа", self.types)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    @staticmethod
    def _checklist(items: dict, checked: list) -> QListWidget:
        w = QListWidget()
        for key, text in items.items():
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if key in checked else Qt.Unchecked)
            w.addItem(it)
        w.setMinimumHeight(140)
        return w

    @staticmethod
    def _checked(w: QListWidget) -> list[str]:
        return [w.item(i).data(Qt.UserRole) for i in range(w.count()) if w.item(i).checkState() == Qt.Checked]

    def changes(self) -> dict[str, list[str]]:
        out = {"subsystems": self._checked(self.subs), "doc_types": self._checked(self.types)}
        if self.mark.currentText().strip():
            out["mark"] = [self.mark.currentText().strip()]
        if self.section.currentData():
            out["section"] = [self.section.currentData()]
        return out


class CompositionPanel(QWidget):
    open_page = Signal(int, int, int)          # file_id, page_no, page_id

    def __init__(self):
        super().__init__()
        self.obj: ProjectObject | None = None
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open)
        self.b_confirm = QPushButton("Подтвердить выбранные")
        self.b_confirm.setToolTip("Принять автоматическую классификацию выбранных листов как есть")
        self.b_confirm.clicked.connect(self.confirm_selected)
        self.b_edit = QPushButton("Изменить…")
        self.b_edit.clicked.connect(self.edit_selected)
        self.b_redo = QPushButton("Классифицировать заново")
        self.b_redo.setToolTip("Пересчитать по словарю. Ваши решения сохраняются и применяются снова")
        self.b_redo.clicked.connect(self.reclassify)
        self.b_check = QPushButton("Проверить")
        self.b_check.setEnabled(False)
        self.b_check.setToolTip("Базовые проверки появятся на шаге 4")
        self.xlsx = QTableWidget(0, 5)
        self.xlsx.setHorizontalHeaderLabels(["Файл", "Лист книги", "Строк", "Схема столбцов", "Подтверждена"])
        self.xlsx.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.xlsx.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.xlsx.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.xlsx.setMaximumHeight(130)
        self.b_xlsx = QPushButton("Подтвердить схему")
        self.b_xlsx.clicked.connect(self.confirm_xlsx)

        top = QHBoxLayout()
        top.addWidget(QLabel("Показать:"))
        top.addWidget(self.filter)
        top.addStretch(1)
        top.addWidget(self.b_redo)
        btns = QHBoxLayout()
        btns.addWidget(self.b_confirm)
        btns.addWidget(self.b_edit)
        btns.addStretch(1)
        btns.addWidget(self.b_check)
        xl = QHBoxLayout()
        xl.addWidget(QLabel("<b>Таблицы XLSX</b>"))
        xl.addStretch(1)
        xl.addWidget(self.b_xlsx)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(btns)
        lay.addLayout(xl)
        lay.addWidget(self.xlsx)

    # ------------------------------------------------------------------
    def set_object(self, obj: ProjectObject | None):
        self.obj = obj
        self.refresh()

    def _rows(self):
        out = []
        for f in self.obj.files():
            if f.kind != "PDF" or f.status != "прочитан":
                continue
            for p in self.obj.pages(f.id):
                out.append((f, p, self.obj.classification(p["id"]), self.obj.stamp(p["id"])))
        return out

    @staticmethod
    def _stamp_state(st) -> str:
        if not st or not st["recognized"]:
            return "не найден"
        if st["confirmed"]:
            return "подтверждён"
        if st["source"] == "OCR" or any(v["confidence"] == "низкая" for v in st["fields"].values()):
            return "OCR — проверить"
        return "из слоя"

    def refresh(self):
        self.table.setRowCount(0)
        self.xlsx.setRowCount(0)
        if not self.obj:
            self.summary.setText("Объект не открыт.")
            return
        rows = self._rows()
        counts = {SURE: 0, DOUBT: 0, UNKNOWN: 0}
        conflicts = 0
        versions = set()
        mode = self.filter.currentIndex()
        for f, p, cls, st in rows:
            if not cls:
                continue
            counts[cls.level] += 1
            conflicts += bool(cls.conflict or cls.section.contradictions)
            versions.add(cls.dict_version)
            stamp_state = self._stamp_state(st)
            if mode == 1 and cls.level == SURE:
                continue
            if mode == 2 and not (cls.conflict or cls.section.contradictions or cls.mark.contradictions):
                continue
            if mode == 3 and stamp_state != "OCR — проверить":
                continue
            fields = {k: v["value"] for k, v in st["fields"].items()} if st else {}
            r = self.table.rowCount()
            self.table.insertRow(r)
            sheet = fields.get("лист", "")
            vals = [f.name, f"{p['page_no']}" + (f" (по штампу {sheet})" if sheet else ""),
                    fields.get("наименование листа", ""),
                    ", ".join(cls.mark.value), ", ".join(cls.section.value), ", ".join(cls.subsystems.value),
                    ", ".join(cls.doc_types.value), cls.level, stamp_state, cls.source]
            tips = ["", "", fields.get("шифр", ""), _tip("Марка", cls.mark), _tip("Раздел", cls.section),
                    _tip("Подсистемы", cls.subsystems) + "\nПодсистемы — кандидаты: на весь лист не распространяются "
                    "без подтверждения", _tip("Типы", cls.doc_types),
                    (cls.conflict or "") + f"\nСловарь {cls.dict_version}", st["note"] if st else "", ""]
            for c, (v, tip) in enumerate(zip(vals, tips)):
                it = QTableWidgetItem(v)
                it.setToolTip(tip.strip())
                it.setData(Qt.UserRole, (f.id, p["page_no"], p["id"]))
                self.table.setItem(r, c, it)
            for c, ch in ((3, cls.mark), (4, cls.section), (5, cls.subsystems), (6, cls.doc_types)):
                if cls.source == "пользователь":
                    self.table.item(r, c).setBackground(QBrush(USER))
                elif ch.confidence in COLORS and c != 5:
                    self.table.item(r, c).setBackground(QBrush(COLORS[ch.confidence]))
                if ch.contradictions:
                    self.table.item(r, c).setForeground(QBrush(CONFLICT))
            if cls.level in COLORS and cls.source != "пользователь":
                self.table.item(r, 7).setBackground(QBrush(COLORS[cls.level]))
            if stamp_state in ("OCR — проверить", "не найден"):
                self.table.item(r, 8).setBackground(QBrush(COLORS[DOUBT]))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        total = sum(counts.values())
        self.summary.setText(
            f"Листов: {total}. Уверенно: {counts[SURE]}, спорно: {counts[DOUBT]}, не определено: {counts[UNKNOWN]}, "
            f"с противоречиями: {conflicts}. Словарь признаков: {', '.join(sorted(versions)) or '—'}. "
            "Спорное подсвечено и ждёт подтверждения, но не мешает проверкам, которые от него не зависят. "
            "Подробности — во всплывающей подсказке ячейки; двойной щелчок открывает лист.")
        for f in self.obj.files():
            if f.kind != "XLSX":
                continue
            for t in self.obj.xlsx_tables(f.id):
                schema = json.loads(t["schema"])
                r = self.xlsx.rowCount()
                self.xlsx.insertRow(r)
                desc = ", ".join(f"{c}→{v['role']}" for c, v in schema.items() if v["role"])
                vals = [f.name, t["sheet"], str(len(self.obj.xlsx_rows(t["id"]))),
                        f"{desc} (уверенность: {t['confidence']})", "да" if t["confirmed"] else "нет"]
                for c, v in enumerate(vals):
                    it = QTableWidgetItem(v)
                    it.setData(Qt.UserRole, t["id"])
                    self.xlsx.setItem(r, c, it)
                if not t["confirmed"]:
                    self.xlsx.item(r, 4).setBackground(QBrush(COLORS[DOUBT]))

    # ------------------------------------------------------------------
    def _selected(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self.table.item(r, 0).data(Qt.UserRole)[2] for r in rows]

    def _open(self, row: int, _col: int):
        file_id, page_no, page_id = self.table.item(row, 0).data(Qt.UserRole)
        self.open_page.emit(file_id, page_no, page_id)

    def confirm_selected(self):
        ids = self._selected()
        if not ids or not self.obj:
            return
        for pid in ids:
            self.obj.decide_classification(pid)
        self.refresh()

    def edit_selected(self):
        ids = self._selected()
        if not ids or not self.obj:
            return
        dlg = ClassDialog(self, self.obj.classification(ids[0]))
        if dlg.exec() != QDialog.Accepted:
            return
        for pid in ids:
            self.obj.decide_classification(pid, dlg.changes())
        self.refresh()

    def reclassify(self):
        if not self.obj:
            return
        for f in self.obj.files():
            if f.kind == "PDF" and f.status == "прочитан":
                classify_file(self.obj, f.id)
        self.refresh()

    def confirm_xlsx(self):
        rows = sorted({i.row() for i in self.xlsx.selectedItems()})
        if not rows or not self.obj:
            QMessageBox.information(self, "Схема XLSX", "Выберите таблицу в списке.")
            return
        for r in rows:
            self.obj.confirm_xlsx_table(self.xlsx.item(r, 0).data(Qt.UserRole))
        self.refresh()
