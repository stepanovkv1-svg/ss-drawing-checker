"""Окна «Ход проверки» и «Результаты» (ТЗ 8.1): этапы со статусами, замечания с местами и скриншотами,
фильтры, правка текста, класса и критичности, решение инженера, разделение и объединение, правка мест."""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from ..findings import store
from ..findings.model import CLASSES, DECISIONS, GROUPS, REVIEW, SEVERITIES
from ..pipeline import run_checks
from ..pipeline.run import last_run

CLASS_COLORS = {"подтверждённая ошибка": "#fde2e1", "возможная ошибка": "#fff3cd", "требуется уточнение": "#e7f1fb"}
STAGE_COLORS = {"частично": "#fff3cd", "пропущено": "#eeeeee", "ошибка": "#fde2e1", "ручная проверка": "#e7f1fb"}


class CheckWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, obj):
        super().__init__()
        self.obj, self._stop = obj, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            rid = run_checks(self.obj, progress=self.progress.emit, cancelled=lambda: self._stop)
            self.finished.emit(rid)
        except Exception as e:  # noqa: BLE001 — показать ошибку в окне, а не уронить программу
            self.finished.emit(e)


class ResultsPanel(QWidget):
    open_place = Signal(int, int, int, object)      # file_id, page_no, page_id, rect | None
    pick_rect = Signal(int)                          # place_id: задать рамку на листе
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.obj = None
        self.thread: QThread | None = None
        self.fid: int | None = None

        self.b_run = QPushButton("Проверить")
        self.b_run.clicked.connect(self.start)
        self.b_stop = QPushButton("Стоп")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(lambda: self.worker.stop())
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.run_info = QLabel("Проверка ещё не запускалась.")
        self.run_info.setWordWrap(True)
        self.stages = QTableWidget(0, 5)
        self.stages.setHorizontalHeaderLabels(["Код", "Проверка", "Статус", "Найдено", "Пояснение"])
        self.stages.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stages.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.stages.setMaximumHeight(170)

        self.f_cls = QComboBox()
        self.f_cls.addItems(["Все классы"] + CLASSES)
        self.f_grp = QComboBox()
        self.f_grp.addItems(["Все группы"] + list(GROUPS.values()))
        self.f_dec = QComboBox()
        self.f_dec.addItems(["Любое решение"] + DECISIONS + [REVIEW])
        for c in (self.f_cls, self.f_grp, self.f_dec):
            c.currentIndexChanged.connect(self.refresh_findings)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["№", "Группа", "Класс", "Правило", "Замечание", "Листы", "Решение",
                                              "Критичность"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.on_select)

        # карточка замечания
        self.text = QTextEdit()
        self.text.setMaximumHeight(80)
        self.proof = QLabel()
        self.proof.setWordWrap(True)
        self.cls = QComboBox()
        self.cls.addItems(CLASSES)
        self.sev = QComboBox()
        self.sev.addItems(SEVERITIES)
        self.b_save = QPushButton("Сохранить правку")
        self.b_save.clicked.connect(self.save_edit)
        self.b_accept = QPushButton("Принять")
        self.b_accept.clicked.connect(lambda: self.decide("принято"))
        self.b_reject = QPushButton("Отклонить")
        self.b_reject.clicked.connect(lambda: self.decide("отклонено"))
        self.b_reset = QPushButton("Не рассмотрено")
        self.b_reset.clicked.connect(lambda: self.decide("не рассмотрено"))
        self.b_merge = QPushButton("Объединить выбранные")
        self.b_merge.clicked.connect(self.merge)
        self.places = QListWidget()
        self.places.setIconSize(QSize(220, 140))
        self.places.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.places.itemDoubleClicked.connect(self.open_selected_place)
        self.b_open = QPushButton("Открыть лист")
        self.b_open.clicked.connect(self.open_selected_place)
        self.b_frame = QPushButton("Задать рамку на листе…")
        self.b_frame.setToolTip("Откройте место и обведите мышью нужную область на листе")
        self.b_frame.clicked.connect(self.ask_frame)
        self.b_del = QPushButton("Удалить место")
        self.b_del.clicked.connect(self.delete_place)
        self.b_split = QPushButton("Выделить в отдельное замечание")
        self.b_split.clicked.connect(self.split)

        top = QHBoxLayout()
        top.addWidget(self.b_run)
        top.addWidget(self.b_stop)
        top.addWidget(self.progress, 1)
        filt = QHBoxLayout()
        for w in (self.f_cls, self.f_grp, self.f_dec):
            filt.addWidget(w)
        filt.addStretch(1)
        filt.addWidget(self.b_merge)
        upper = QWidget()
        ul = QVBoxLayout(upper)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.addLayout(top)
        ul.addWidget(self.run_info)
        ul.addWidget(self.stages)
        ul.addLayout(filt)
        ul.addWidget(self.table, 1)

        card = QWidget()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Класс:"))
        row.addWidget(self.cls)
        row.addWidget(QLabel("Критичность:"))
        row.addWidget(self.sev)
        row.addWidget(self.b_save)
        row.addStretch(1)
        row.addWidget(self.b_accept)
        row.addWidget(self.b_reject)
        row.addWidget(self.b_reset)
        cl.addWidget(self.text)
        cl.addWidget(self.proof)
        cl.addLayout(row)
        cl.addWidget(self.places, 1)
        prow = QHBoxLayout()
        for b in (self.b_open, self.b_frame, self.b_del, self.b_split):
            prow.addWidget(b)
        prow.addStretch(1)
        cl.addLayout(prow)

        split = QSplitter(Qt.Vertical)
        split.addWidget(upper)
        split.addWidget(card)
        split.setSizes([450, 380])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self.set_card_enabled(False)

    # ------------------------------------------------------------------ запуск
    def set_object(self, obj):
        self.obj = obj
        self.fid = None
        self.refresh()

    def start(self):
        if not self.obj or self.thread:
            return
        self.b_run.setEnabled(False)
        self.b_stop.setVisible(True)
        self.progress.setVisible(True)
        self.thread = QThread()
        self.worker = CheckWorker(self.obj)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_done)
        self.thread.start()

    def on_progress(self, n, total, name):
        self.progress.setRange(0, total)
        self.progress.setValue(n - 1)
        self.progress.setFormat(f"{n}/{total}: {name}")

    def on_done(self, result):
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.b_run.setEnabled(True)
        self.b_stop.setVisible(False)
        self.progress.setVisible(False)
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Проверка", f"Проверка прервана ошибкой: {result}")
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------------ показ
    def refresh(self):
        self.stages.setRowCount(0)
        if not self.obj:
            return
        run, stages = last_run(self.obj.con)
        if run:
            self.run_info.setText(f"Последняя проверка: {run['started_at']} — {run['status']}. "
                                  "Замечаний от классификации нет, кроме БП-09. Программа не повышает критичность сама.")
        for st in stages:
            r = self.stages.rowCount()
            self.stages.insertRow(r)
            for c, v in enumerate([st["code"], st["title"], st["status"], str(st["findings"]), st["message"]]):
                it = QTableWidgetItem(v)
                it.setToolTip(st["message"])
                if st["status"] in STAGE_COLORS:
                    it.setBackground(QBrush(QColor(STAGE_COLORS[st["status"]])))
                self.stages.setItem(r, c, it)
        self.stages.resizeColumnsToContents()
        self.stages.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.refresh_findings()

    def refresh_findings(self):
        keep = self.fid
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if not self.obj:
            self.table.blockSignals(False)
            return
        cls_f = self.f_cls.currentText() if self.f_cls.currentIndex() else None
        grp_f = self.f_grp.currentIndex() or None
        dec_f = self.f_dec.currentText() if self.f_dec.currentIndex() else None
        for n, f in enumerate(store.findings(self.obj.con), 1):
            if cls_f and f["cls"] != cls_f or grp_f and f["grp"] != grp_f:
                continue
            if dec_f and (f["review"] != REVIEW if dec_f == REVIEW else f["decision"] != dec_f):
                continue
            pl = store.places(self.obj.con, f["id"])
            sheets = sorted({p["sheet"] or (f"стр. {p['page_no']}" if p["page_no"] else p["cell"]) for p in pl},
                            key=lambda x: (len(x), x))
            r = self.table.rowCount()
            self.table.insertRow(r)
            decision = f["decision"] + (f" · {REVIEW}" if f["review"] else "")
            vals = [str(n), GROUPS[f["grp"]], f["cls"], f["rule"], f["text"], ", ".join(sheets), decision, f["severity"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, f["id"])
                it.setToolTip(f["text"] + "\n\n" + f["proof"])
                self.table.setItem(r, c, it)
            self.table.item(r, 2).setBackground(QBrush(QColor(CLASS_COLORS[f["cls"]])))
            if f["review"]:
                self.table.item(r, 6).setForeground(QBrush(QColor("#b00020")))
            if f["decision"] == "отклонено":
                for c in range(len(vals)):
                    self.table.item(r, c).setForeground(QBrush(QColor("#888888")))
            if f["id"] == keep:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        self.table.resizeColumnToContents(0)
        self.on_select()

    def _selected_ids(self) -> list[int]:
        return sorted({self.table.item(i.row(), 0).data(Qt.UserRole) for i in self.table.selectedItems()})

    def set_card_enabled(self, on: bool):
        for w in (self.text, self.cls, self.sev, self.b_save, self.b_accept, self.b_reject, self.b_reset, self.places,
                  self.b_open, self.b_frame, self.b_del, self.b_split):
            w.setEnabled(on)

    def on_select(self):
        ids = self._selected_ids()
        self.fid = ids[0] if ids else None
        self.places.clear()
        if not self.fid:
            self.text.clear()
            self.proof.clear()
            self.set_card_enabled(False)
            return
        f = self.obj.con.execute("SELECT * FROM finding WHERE id=?", (self.fid,)).fetchone()
        self.set_card_enabled(True)
        self.text.setPlainText(f["text"])
        self.proof.setText(f"<b>Основание:</b> {f['basis']}<br><b>Доказательство:</b> {f['proof']}"
                           + (f"<br><b style='color:#b00020'>{REVIEW}:</b> замечание больше не находится проверкой, "
                              "но вы его меняли или рассматривали — решите, оставить ли его." if f["review"] else ""))
        self.cls.setCurrentText(f["cls"])
        self.sev.setCurrentText(f["severity"])
        for p in store.places(self.obj.con, self.fid):
            where = p["sheet"] and f"лист {p['sheet']}" or (f"стр. {p['page_no']}" if p["page_no"] else "")
            label = f"{p['file_name']} · {where} {p['cell']}".strip() + f"\n{p['method']}"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, dict(p))
            if p["shot"]:
                pm = QPixmap()
                pm.loadFromData(bytes(p["shot"]))
                it.setIcon(QIcon(pm))
            self.places.addItem(it)

    # ------------------------------------------------------------------ действия
    def save_edit(self):
        if self.fid:
            store.update_finding(self.obj.con, self.fid, text=self.text.toPlainText().strip(),
                                 cls=self.cls.currentText(), severity=self.sev.currentText())
            self.refresh_findings()

    def decide(self, decision: str):
        for fid in self._selected_ids():
            store.update_finding(self.obj.con, fid, decision=decision)
        self.refresh_findings()

    def merge(self):
        ids = self._selected_ids()
        if len(ids) < 2:
            QMessageBox.information(self, "Объединение", "Выделите в списке два замечания или больше (Ctrl+щелчок).")
            return
        self.fid = store.merge_findings(self.obj.con, ids)
        self.refresh_findings()

    def _place(self):
        it = self.places.currentItem()
        return it.data(Qt.UserRole) if it else None

    def open_selected_place(self, *_):
        p = self._place()
        if not p or not p["page_id"]:
            return
        file_id = self.obj.con.execute("SELECT file_id FROM page WHERE id=?", (p["page_id"],)).fetchone()[0]
        rect = None if p["x0"] is None else (p["x0"], p["y0"], p["x1"], p["y1"])
        self.open_place.emit(file_id, p["page_no"], p["page_id"], rect)

    def ask_frame(self):
        p = self._place()
        if p and p["page_id"]:
            self.open_selected_place()
            self.pick_rect.emit(p["id"])

    def delete_place(self):
        p = self._place()
        if p and self.places.count() > 1:
            store.delete_place(self.obj.con, p["id"])
            self.on_select()
        elif p:
            QMessageBox.information(self, "Место", "Это единственное место замечания. Если замечание не нужно — "
                                                   "отклоните его.")

    def split(self):
        ids = [i.data(Qt.UserRole)["id"] for i in self.places.selectedItems()]
        new = store.split_places(self.obj.con, self.fid, ids) if self.fid else None
        if not new:
            QMessageBox.information(self, "Разделение", "Выделите одно или несколько мест (не все).")
            return
        self.fid = new
        self.refresh_findings()
