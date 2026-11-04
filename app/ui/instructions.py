"""Вкладка «Инструкции» (ТЗ 8.1): список с переключателями, область, версии, редактор, загрузка MD и DOCX,
«Как программа поняла инструкцию», конструктор требований, конфликты (ФТ-14…ФТ-16, С2, С3)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..documents.classify import dictionary
from ..findings.model import GROUPS, SEVERITIES
from ..requirements import conflicts as cf
from ..requirements import library as lib
from ..requirements.model import (APPROVED, NOT_READY, OPS, STAMP_FIELDS, STATUSES, TABLE_ROLES, TABLE_TYPES, ZONES,
                                  Requirement, Scope)
from ..requirements.parse import parse_text

STATUS_COLORS = {APPROVED: "#e6f4ea", "неоднозначен": "#fff3cd", "не разобран": "#eeeeee"}
SIGN_ROLES = ["подпись:Разраб.", "подпись:Пров.", "подпись:Н. контр.", "подпись:ГИП", "подпись:Утв."]

# Параметры операций для конструктора: (ключ, подпись, вид, варианты)
PARAMS = {
    "ВО-01": [("field", "Графа штампа", "edit_choice", STAMP_FIELDS + SIGN_ROLES),
              ("mode", "Проверка", "choice", ["заполнено", "список", "шаблон"]),
              ("values", "Допустимые значения (через ;)", "list", None),
              ("pattern", "Шаблон (регулярное выражение)", "text", None),
              ("what", "Для подписи проверять", "choice", ["фамилия", "дата"])],
    "ВО-02": [("text", "Текст", "text", None), ("present", "Требование", "bool", ["должен быть", "не допускается"]),
              ("regex", "Регулярное выражение", "check", None), ("zone", "Где искать", "choice", ZONES)],
    "ВО-03": [("table", "Таблица", "choice", TABLE_TYPES), ("column", "Столбец (назначение или заголовок)",
              "edit_choice", TABLE_ROLES), ("mode", "Проверка", "choice", ["есть", "заполнено"])],
    "ВО-04": [("pattern", "Шаблон обозначения (например ККС-\\d+)", "text", None),
              ("to", "Где должно быть", "choice", ["Спецификация", "Кабельный журнал"])],
    "ВО-05": [("pattern", "Шаблон обозначения (например К\\d+)", "text", None), ("tolerance", "Допуск, шт.", "int", None)],
    "ВО-06": [("source", "Источник значения", "edit_choice",
               [f"поле штампа:{f}" for f in STAMP_FIELDS] + [f"столбец:{r}" for r in TABLE_ROLES]),
              ("min", "Не менее", "num", None), ("max", "Не более", "num", None),
              ("values", "Или список значений (через ;)", "list", None)],
    "ВО-07": [("element", "Элемент", "text", None), ("signs", "Надписи-признаки (через ;)", "list", None)],
    "ВО-08": [],
}


def params_text(r: Requirement) -> str:
    out = []
    for k, v in r.params.items():
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            v = "; ".join(map(str, v))
        if k == "present":
            v = "должен быть" if v else "не допускается"
        out.append(f"{k}: {v}")
    return ", ".join(out)


class ScopeDialog(QDialog):
    def __init__(self, parent, scope: Scope, title="Область действия"):
        super().__init__(parent)
        self.setWindowTitle(title)
        d = dictionary()
        self.lists = {}
        form = QFormLayout(self)
        form.addRow(QLabel("Ничего не отмечено — без ограничения. Отмеченное в разных строках должно совпасть всё."))
        for key, label, items in [("sections", "Разделы", {k: f"{k} — {v}" for k, v in d["sections"].items()}),
                                  ("subsystems", "Подсистемы", {k: f"{k} — {v['name']}" for k, v in d["subsystems"].items()}),
                                  ("marks", "Марки комплекта", {k: k for k in sorted(d["marks"])}),
                                  ("doc_types", "Типы документа", {n: n for n, _, _ in d["doc_types"]})]:
            w = QListWidget()
            w.setMaximumHeight(110)
            for code, name in items.items():
                it = QListWidgetItem(name)
                it.setData(Qt.UserRole, code)
                it.setCheckState(Qt.Checked if code in getattr(scope, key) else Qt.Unchecked)
                w.addItem(it)
            self.lists[key] = w
            form.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def scope(self) -> Scope:
        return Scope(**{k: [w.item(i).data(Qt.UserRole) for i in range(w.count())
                            if w.item(i).checkState() == Qt.Checked] for k, w in self.lists.items()})


class RequirementDialog(QDialog):
    """Конструктор требования: операция + параметры + область + критичность + раздел реестра + шаблон текста."""

    def __init__(self, parent, req: Requirement):
        super().__init__(parent)
        self.setWindowTitle("Конструктор требования")
        self.resize(640, 560)
        self.req = req
        self._scope = req.scope
        self.fragment = QPlainTextEdit(req.fragment)
        self.fragment.setMaximumHeight(70)
        self.op = QComboBox()
        self.op.addItem("— не задана (ручная проверка) —", "")
        for code, name in OPS.items():
            self.op.addItem(f"{code} — {name}" + (" (на этом шаге не выполняется)" if code in NOT_READY else ""), code)
        self.op.setCurrentIndex(max(0, self.op.findData(req.op)))
        self.params_box = QWidget()
        self.params_form = QFormLayout(self.params_box)
        self.widgets = {}
        self.op.currentIndexChanged.connect(lambda *_: self.build_params({}))
        self.scope_lbl = QLabel(req.scope.text())
        b_scope = QPushButton("Область…")
        b_scope.clicked.connect(self.edit_scope)
        srow = QHBoxLayout()
        srow.addWidget(self.scope_lbl, 1)
        srow.addWidget(b_scope)
        self.sev = QComboBox()
        self.sev.addItems(SEVERITIES)
        self.sev.setCurrentText(req.severity)
        self.grp = QComboBox()
        for k, v in GROUPS.items():
            self.grp.addItem(f"{k} — {v}", k)
        self.grp.setCurrentIndex(max(0, self.grp.findData(req.grp)))
        self.template = QLineEdit(req.template)
        self.template.setPlaceholderText("По умолчанию «{где}: {что}.» Можно: {требование}, {значение}, {поле}")
        self.norm = QLineEdit(req.norm_ref)
        self.norm.setPlaceholderText("например, ГОСТ Р 21.101-2020, п. 5.3 (проверка цитат — на Э2)")
        self.status = QComboBox()
        self.status.addItems(STATUSES)
        self.status.setCurrentText(req.status)
        self.note = QLabel(req.note)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#8a6d00")
        form = QFormLayout(self)
        form.addRow("Фрагмент инструкции", self.fragment)
        form.addRow("Операция", self.op)
        form.addRow(self.params_box)
        form.addRow("Область требования", srow)
        form.addRow("Критичность", self.sev)
        form.addRow("Раздел реестра", self.grp)
        form.addRow("Шаблон текста замечания", self.template)
        form.addRow("Ссылка на норматив", self.norm)
        form.addRow("Статус", self.status)
        form.addRow(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.build_params(req.params)

    def build_params(self, values: dict):
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self.widgets = {}
        for key, label, kind, opts in PARAMS.get(self.op.currentData() or "", []):
            v = values.get(key)
            if kind in ("choice", "edit_choice"):
                w = QComboBox()
                w.setEditable(kind == "edit_choice")
                w.addItems(opts)
                if v is not None:
                    w.setCurrentText(str(v))
            elif kind == "bool":
                w = QComboBox()
                w.addItems(opts)
                w.setCurrentIndex(0 if v is None or v else 1)
            elif kind == "check":
                w = QCheckBox()
                w.setChecked(bool(v))
            elif kind == "int":
                w = QSpinBox()
                w.setRange(0, 10000)
                w.setValue(int(v or 0))
            else:
                w = QLineEdit("; ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v)))
            self.widgets[key] = (kind, w)
            self.params_form.addRow(label, w)
        if (self.op.currentData() or "") in NOT_READY:
            self.params_form.addRow(QLabel(NOT_READY[self.op.currentData()] + " — требование уйдёт в ручную проверку."))

    def edit_scope(self):
        dlg = ScopeDialog(self, self._scope, "Область требования (уточняет область инструкции)")
        if dlg.exec():
            self._scope = dlg.scope()
            self.scope_lbl.setText(self._scope.text())

    def result_req(self) -> Requirement:
        params = {}
        for key, (kind, w) in self.widgets.items():
            if kind in ("choice", "edit_choice"):
                params[key] = w.currentText().strip()
            elif kind == "bool":
                params[key] = w.currentIndex() == 0
            elif kind == "check":
                params[key] = w.isChecked()
            elif kind == "int":
                params[key] = w.value()
            elif kind == "list":
                vals = [x.strip() for x in w.text().replace(",", ";").split(";") if x.strip()] if key != "signs" else \
                    [x.strip() for x in w.text().split(";") if x.strip()]
                if vals:
                    params[key] = vals
            elif kind == "num":
                t = w.text().strip().replace(",", ".")
                if t:
                    try:
                        params[key] = float(t)
                    except ValueError:
                        pass
            elif w.text().strip():
                params[key] = w.text().strip()
        r = Requirement(self.fragment.toPlainText().strip(), self.status.currentText(), self.op.currentData() or "",
                        params, self._scope, self.sev.currentText(), self.grp.currentData(), self.template.text().strip(),
                        "" if self.status.currentText() == APPROVED else self.req.note, self.norm.text().strip(),
                        self.req.uid)
        return r


class InstructionsPanel(QWidget):
    changed = Signal()

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.iid: int | None = None
        self.reqs: list[Requirement] = []
        self.dirty = False

        self.list = QListWidget()
        self.list.itemChanged.connect(self.on_toggle)
        self.list.currentItemChanged.connect(lambda *_: self.select())
        b_new = QPushButton("Новая")
        b_new.clicked.connect(self.new)
        b_load = QPushButton("Загрузить MD / DOCX…")
        b_load.clicked.connect(self.load_file)
        b_ren = QPushButton("Переименовать")
        b_ren.clicked.connect(self.rename)
        b_del = QPushButton("Удалить")
        b_del.clicked.connect(self.delete)
        self.conflicts = QListWidget()
        self.conflicts.itemDoubleClicked.connect(self.resolve)
        b_res = QPushButton("Решить конфликт…")
        b_res.clicked.connect(lambda: self.resolve(self.conflicts.currentItem()))
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Инструкции</b> (галочка — применять при проверке)"))
        ll.addWidget(self.list, 2)
        r1 = QHBoxLayout()
        r1.addWidget(b_new)
        r1.addWidget(b_load)
        ll.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(b_ren)
        r2.addWidget(b_del)
        ll.addLayout(r2)
        ll.addWidget(QLabel("<b>Конфликты требований</b> — до решения оба требования не применяются"))
        ll.addWidget(self.conflicts, 1)
        ll.addWidget(b_res)

        self.head = QLabel("Выберите или создайте инструкцию")
        self.scope_lbl = QLabel("")
        self.b_scope = QPushButton("Область инструкции…")
        self.b_scope.clicked.connect(self.edit_scope)
        self.versions = QComboBox()
        self.versions.activated.connect(self.load_version)
        hr = QHBoxLayout()
        hr.addWidget(self.scope_lbl, 1)
        hr.addWidget(self.b_scope)
        hr.addWidget(QLabel("Версия:"))
        hr.addWidget(self.versions)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Текст инструкции: по одному требованию в строке или пункте списка.")
        self.editor.textChanged.connect(self.mark_dirty)
        self.b_parse = QPushButton("Разобрать текст")
        self.b_parse.setToolTip("Строки, которые вы уже правили в конструкторе, сохраняются")
        self.b_parse.clicked.connect(self.parse)
        self.summary = QLabel("")
        pr = QHBoxLayout()
        pr.addWidget(self.b_parse)
        pr.addWidget(self.summary, 1)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["№", "Фрагмент", "Статус", "Операция", "Параметры", "Область",
                                              "Критичность", "Выполнение"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda r, _c: self.edit_req(r))
        b_edit = QPushButton("Конструктор…")
        b_edit.clicked.connect(lambda: self.edit_req(self.table.currentRow()))
        b_ok = QPushButton("Утвердить выбранные")
        b_ok.clicked.connect(self.approve)
        b_add = QPushButton("Добавить требование…")
        b_add.clicked.connect(self.add_req)
        b_rm = QPushButton("Удалить требование")
        b_rm.clicked.connect(self.remove_req)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Комментарий к версии (необязательно)")
        self.b_save = QPushButton("Сохранить версию")
        self.b_save.clicked.connect(self.save)
        rr = QHBoxLayout()
        for b in (b_edit, b_ok, b_add, b_rm):
            rr.addWidget(b)
        rr.addStretch(1)
        sr = QHBoxLayout()
        sr.addWidget(self.note, 1)
        sr.addWidget(self.b_save)
        right = QSplitter(Qt.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.addWidget(self.head)
        tl.addLayout(hr)
        tl.addWidget(self.editor, 1)
        tl.addLayout(pr)
        bot = QWidget()
        bl = QVBoxLayout(bot)
        bl.addWidget(QLabel("<b>Как программа поняла инструкцию</b> — выполняются только утверждённые требования; "
                            "остальные идут в ручную проверку и видны в покрытии"))
        bl.addWidget(self.table, 1)
        bl.addLayout(rr)
        bl.addLayout(sr)
        right.addWidget(top)
        right.addWidget(bot)
        right.setSizes([300, 420])
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([300, 800])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self.set_enabled(False)
        self.refresh()

    # ------------------------------------------------------------ список
    def set_enabled(self, on: bool):
        for w in (self.editor, self.b_parse, self.table, self.b_save, self.b_scope, self.versions, self.note):
            w.setEnabled(on)

    def refresh(self, keep: int | None = None):
        keep = keep if keep is not None else self.iid
        self.list.blockSignals(True)
        self.list.clear()
        for ins in lib.instructions(self.con):
            it = QListWidgetItem(f"{ins.name}  (v{ins.ver})" if ins.ver else f"{ins.name}  (не сохранена)")
            it.setData(Qt.UserRole, ins.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if ins.enabled else Qt.Unchecked)
            self.list.addItem(it)
            if ins.id == keep:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        self.refresh_conflicts()
        if self.list.currentItem() is None:
            self.iid = None
            self.show_instruction(None)
        elif self.list.currentItem().data(Qt.UserRole) != self.iid or keep is not None:
            self.select(force=True)

    def refresh_conflicts(self):
        self.conflicts.clear()
        dec = cf.decisions(self.con)
        self._conflicts = cf.find_conflicts(lib.enabled(self.con))
        for c in self._conflicts:
            ch = dec.get(c.key)
            state = "не решён" if ch is None else ("применяются оба" if ch == cf.BOTH else
                                                  "не применяется ни одно" if ch == cf.NEITHER else "решён")
            it = QListWidgetItem(f"[{state}] {cf.describe(c)}")
            it.setData(Qt.UserRole, c.key)
            if ch is None:
                it.setBackground(QBrush(QColor("#fde2e1")))
            self.conflicts.addItem(it)

    def on_toggle(self, it: QListWidgetItem):
        lib.set_enabled(self.con, it.data(Qt.UserRole), it.checkState() == Qt.Checked)
        self.refresh_conflicts()
        self.changed.emit()

    def _ask_discard(self) -> bool:
        if not self.dirty:
            return True
        return QMessageBox.question(self, "Инструкция", "Есть несохранённые изменения. Отбросить их?") == QMessageBox.Yes

    def select(self, force=False):
        it = self.list.currentItem()
        iid = it.data(Qt.UserRole) if it else None
        if iid == self.iid and not force:
            return
        self.iid = iid
        self.show_instruction(lib.get(self.con, iid) if iid else None)

    def show_instruction(self, ins):
        self.editor.blockSignals(True)
        self.versions.clear()
        if not ins:
            self.head.setText("Выберите или создайте инструкцию")
            self.editor.clear()
            self.reqs = []
            self.scope_lbl.clear()
            self.set_enabled(False)
        else:
            self.set_enabled(True)
            self.head.setText(f"<b>{ins.name}</b>" + (f" — файл {ins.source_file}" if ins.source_file else ""))
            self.scope_lbl.setText("Область: " + ins.scope.text())
            self.editor.setPlainText(ins.text)
            self.reqs = list(ins.requirements)
            for v in lib.versions(self.con, ins.id):
                self.versions.addItem(f"v{v['ver']} · {v['created_at']}" + (f" · {v['note']}" if v["note"] else ""), v["id"])
        self.editor.blockSignals(False)
        self.dirty = False
        self.fill_table()

    def new(self):
        name, ok = QInputDialog.getText(self, "Новая инструкция", "Название:")
        if ok and name.strip():
            self.iid = lib.create(self.con, name)
            self.refresh(self.iid)

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Инструкция", "", "Инструкции (*.md *.txt *.docx)")
        if not path:
            return
        try:
            self.iid = lib.import_file(self.con, Path(path))
        except Exception as e:  # noqa: BLE001 — файл мог оказаться не тем
            QMessageBox.warning(self, "Инструкция", f"Не удалось прочитать файл: {e}")
            return
        self.refresh(self.iid)
        self.changed.emit()

    def rename(self):
        if not self.iid:
            return
        ins = lib.get(self.con, self.iid)
        name, ok = QInputDialog.getText(self, "Инструкция", "Название:", text=ins.name)
        if ok and name.strip():
            lib.rename(self.con, self.iid, name)
            self.refresh(self.iid)

    def delete(self):
        if self.iid and QMessageBox.question(self, "Инструкция", "Удалить инструкцию со всеми версиями? "
                                             "Прошлые проверки сохранят свою копию.") == QMessageBox.Yes:
            lib.delete(self.con, self.iid)
            self.iid = None
            self.refresh()
            self.changed.emit()

    def edit_scope(self):
        ins = lib.get(self.con, self.iid) if self.iid else None
        if not ins:
            return
        dlg = ScopeDialog(self, ins.scope)
        if dlg.exec():
            lib.set_scope(self.con, ins.id, dlg.scope())
            self.scope_lbl.setText("Область: " + dlg.scope().text())
            self.refresh_conflicts()

    def load_version(self, idx: int):
        vid = self.versions.itemData(idx)
        if vid is None or not self._ask_discard():
            return
        text, reqs = lib.version(self.con, vid)
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self.reqs = reqs
        self.dirty = idx != 0
        self.fill_table()
        if idx:
            self.summary.setText(self.summary.text() + " · открыта старая версия: «Сохранить версию» вернёт её")

    # ------------------------------------------------------------ требования
    def mark_dirty(self):
        self.dirty = True
        self.b_save.setText("Сохранить версию *")

    def parse(self):
        old = {r.uid: r for r in self.reqs}
        new = parse_text(self.editor.toPlainText())
        self.reqs = [old.get(r.uid, r) for r in new] + [r for r in self.reqs if r.uid.startswith("ручн")]
        self.mark_dirty()
        self.fill_table()

    def fill_table(self):
        self.table.setRowCount(0)
        counts = {s: 0 for s in STATUSES}
        for n, r in enumerate(self.reqs, 1):
            counts[r.status] = counts.get(r.status, 0) + 1
            row = self.table.rowCount()
            self.table.insertRow(row)
            vals = [str(n), r.fragment, r.status, r.op, params_text(r), r.scope.text() if not r.scope.empty() else "",
                    r.severity, r.exec_class]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setToolTip(r.note or r.fragment)
                self.table.setItem(row, c, it)
            self.table.item(row, 2).setBackground(QBrush(QColor(STATUS_COLORS.get(r.status, "#ffffff"))))
        self.table.resizeColumnToContents(0)
        self.summary.setText(f"Утверждено: {counts[APPROVED]}; неоднозначно: {counts['неоднозначен']}; "
                             f"не разобрано: {counts['не разобран']} (ручная проверка)")
        if not self.dirty:
            self.b_save.setText("Сохранить версию")

    def edit_req(self, row: int):
        if row < 0 or row >= len(self.reqs):
            return
        dlg = RequirementDialog(self, self.reqs[row])
        if dlg.exec():
            self.reqs[row] = dlg.result_req()
            self.mark_dirty()
            self.fill_table()

    def add_req(self):
        import uuid
        r = Requirement("", uid="ручн-" + uuid.uuid4().hex[:8])
        dlg = RequirementDialog(self, r)
        if dlg.exec():
            self.reqs.append(dlg.result_req())
            self.mark_dirty()
            self.fill_table()

    def remove_req(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for r in rows:
            del self.reqs[r]
        if rows:
            self.mark_dirty()
            self.fill_table()

    def approve(self):
        for row in {i.row() for i in self.table.selectedItems()}:
            r = self.reqs[row]
            if r.op and r.op not in NOT_READY:
                r.status, r.note = APPROVED, ""
        self.mark_dirty()
        self.fill_table()

    def save(self):
        if not self.iid:
            return
        lib.save_version(self.con, self.iid, self.editor.toPlainText(), self.reqs, note=self.note.text().strip())
        self.note.clear()
        self.dirty = False
        self.refresh(self.iid)
        self.changed.emit()

    # ------------------------------------------------------------ конфликты
    def resolve(self, it):
        if not it:
            return
        c = next((x for x in self._conflicts if x.key == it.data(Qt.UserRole)), None)
        if not c:
            return
        opts = [f"Применять: {c.label(c.a)}", f"Применять: {c.label(c.b)}", "Применять оба", "Не применять ни одно",
                "Снять решение"]
        choice, ok = QInputDialog.getItem(self, "Конфликт требований", c.why, opts, 0, False)
        if not ok:
            return
        val = {0: c.a[1].uid, 1: c.b[1].uid, 2: cf.BOTH, 3: cf.NEITHER, 4: ""}[opts.index(choice)]
        cf.decide(self.con, c, val)
        self.refresh_conflicts()
        self.changed.emit()

    def enabled_for_run(self):
        return lib.enabled(self.con), cf.decisions(self.con)
