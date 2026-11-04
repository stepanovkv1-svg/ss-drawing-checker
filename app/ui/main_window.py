"""Главное окно: объекты, файлы объекта, просмотр листа (Э1, шаг 1)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, VERSION, paths
from ..data import AppDb, ObjectError, ProjectObject
from ..documents.intake import add_files
from .details import DetailsPanel
from .sheet_view import SheetView

ACCEPT = {".pdf", ".xlsx", ".md", ".docx"}


class IntakeWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(list)

    def __init__(self, obj: ProjectObject, files: list[Path]):
        super().__init__()
        self.obj, self.files, self._stop = obj, files, False

    def stop(self):
        self._stop = True

    def run(self):
        res = add_files(self.obj, self.files, progress=self.progress.emit, cancelled=lambda: self._stop)
        self.finished.emit(res)


class DropTree(QTreeWidget):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = []
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                files += [x for x in sorted(p.rglob("*")) if x.suffix.lower() in ACCEPT]
            elif p.suffix.lower() in ACCEPT:
                files.append(p)
        if files:
            self.dropped.emit(files)
        e.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, appdb: AppDb):
        super().__init__()
        self.appdb = appdb
        self.obj: ProjectObject | None = None
        self.thread: QThread | None = None
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.resize(1500, 900)

        # ---- левая колонка: объекты
        self.objects = QListWidget()
        self.objects.itemDoubleClicked.connect(lambda it: self.open_object(Path(it.data(Qt.UserRole))))
        b_new = QPushButton("Новый объект…")
        b_new.clicked.connect(self.new_object)
        b_open = QPushButton("Открыть папку…")
        b_open.clicked.connect(self.open_object_dialog)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Объекты</b>"))
        ll.addWidget(self.objects, 1)
        ll.addWidget(b_new)
        ll.addWidget(b_open)

        # ---- средняя колонка: файлы объекта
        self.obj_title = QLabel("Объект не открыт")
        self.only_local = QCheckBox("Только локально (никаких внешних AI-запросов для этого объекта)")
        self.only_local.setEnabled(False)
        self.only_local.toggled.connect(self.toggle_only_local)
        self.tree = DropTree()
        self.tree.setHeaderLabels(["Файл / лист", "Формат", "Поворот", "Текстовый слой", "Статус"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.itemClicked.connect(self.on_tree_click)
        self.tree.dropped.connect(self.start_intake)
        b_add = QPushButton("Добавить файлы…")
        b_add.clicked.connect(self.add_files_dialog)
        self.b_remove = QPushButton("Убрать файл")
        self.b_remove.clicked.connect(self.remove_selected)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.b_stop = QPushButton("Стоп")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(self.stop_intake)
        self.summary = QLabel("")
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.addWidget(self.obj_title)
        ml.addWidget(self.only_local)
        ml.addWidget(QLabel("Перетащите сюда PDF, XLSX, DOCX или MD (можно папку). Раздел определяется автоматически."))
        ml.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(b_add)
        row.addWidget(self.b_remove)
        row.addWidget(self.progress, 1)
        row.addWidget(self.b_stop)
        ml.addLayout(row)
        ml.addWidget(self.summary)

        # ---- правая колонка: лист и сведения о нём
        self.sheet = SheetView()
        self.details = DetailsPanel()
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.sheet)
        right.addWidget(self.details)
        right.setSizes([620, 280])

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(right)
        split.setSizes([230, 520, 750])
        self.setCentralWidget(split)
        self.statusBar().showMessage("Сеть не используется. Данные программы: " + str(paths.user_data_dir()))
        self.refresh_objects()
        self.set_object_enabled(False)

    # ------------------------------------------------------------ объекты
    def refresh_objects(self):
        self.objects.clear()
        for r in self.appdb.recent_objects():
            it = QListWidgetItem(f"{r['name']}\n{r['path']}")
            it.setData(Qt.UserRole, r["path"])
            it.setToolTip(r["path"])
            self.objects.addItem(it)

    def new_object(self):
        name, ok = QInputDialog.getText(self, "Новый объект", "Название объекта:")
        if not ok or not name.strip():
            return
        parent = QFileDialog.getExistingDirectory(self, "Где создать папку объекта")
        if not parent:
            return
        folder = Path(parent) / "".join(c for c in name.strip() if c not in '<>:"/\\|?*')
        try:
            obj = ProjectObject.create(folder, name)
        except (ObjectError, OSError) as e:
            QMessageBox.warning(self, "Объект не создан", str(e))
            return
        self.set_object(obj)

    def open_object_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка объекта")
        if folder:
            self.open_object(Path(folder))

    def open_object(self, folder: Path):
        try:
            obj = ProjectObject.open(folder)
        except (ObjectError, RuntimeError, OSError) as e:
            QMessageBox.warning(self, "Объект не открыт", f"{folder}\n\n{e}")
            if not (folder / "object.db").exists():
                self.appdb.forget_object(str(folder))
                self.refresh_objects()
            return
        bad = [f for f in obj.files() if f.status in ("изменён", "нет файла")]
        self.set_object(obj)
        if bad:
            QMessageBox.information(self, "Проверка исходных файлов",
                                    "\n".join(f"{f.name}: {f.error}" for f in bad))

    def set_object(self, obj: ProjectObject):
        if self.obj:
            self.obj.close()
        self.obj = obj
        self.appdb.remember_object(obj.folder, obj.name)
        self.refresh_objects()
        self.obj_title.setText(f"<b>{obj.name}</b> — {obj.folder}")
        self.only_local.blockSignals(True)
        self.only_local.setChecked(obj.only_local)
        self.only_local.blockSignals(False)
        self.set_object_enabled(True)
        self.sheet.clear()
        self.details.clear()
        self.refresh_files()

    def set_object_enabled(self, on: bool):
        for w in (self.only_local, self.tree, self.b_remove):
            w.setEnabled(on)

    def toggle_only_local(self, on: bool):
        if self.obj:
            self.obj.set_only_local(on)

    # ------------------------------------------------------------ файлы
    def refresh_files(self):
        self.tree.clear()
        if not self.obj:
            return
        for f in self.obj.files():
            top = QTreeWidgetItem([f.name, f.kind, "", "", f.status + (f": {f.error}" if f.error else "")])
            top.setData(0, Qt.UserRole, ("file", f.id))
            if f.status != "прочитан":
                top.setForeground(4, Qt.red)
            for p in self.obj.pages(f.id):
                es = p["extract_status"] or ""
                if p["text_chars"] and "без кодировки" in es:
                    txt = "без кодировки — " + ("OCR" if "OCR" in es else "не читается")
                else:
                    txt = "есть" if p["text_chars"] else "нет — " + (es or "нужен OCR")
                st = self.obj.stamp(p["id"])
                title = f"Лист {p['page_no']}"
                if st and st["fields"].get("лист"):
                    title += f" (по штампу {st['fields']['лист']['value']})"
                if st and st["fields"].get("наименование листа"):
                    title += " — " + st["fields"]["наименование листа"]["value"]
                it = QTreeWidgetItem([title, p["format"], f"{p['rotation']}°", txt,
                                      "" if not st or st["recognized"] else "штамп не распознан"])
                it.setData(0, Qt.UserRole, ("page", f.id, p["page_no"], p["id"], p["extract_status"]))
                top.addChild(it)
            if f.kind == "XLSX":
                for t in self.obj.xlsx_tables(f.id):
                    n = len(self.obj.xlsx_rows(t["id"]))
                    it = QTreeWidgetItem([f"Лист «{t['sheet']}»: {n} строк", "", "", "", ""])
                    it.setData(0, Qt.UserRole, ("xlsx", f.id))
                    top.addChild(it)
            self.tree.addTopLevelItem(top)
        s = self.obj.summary()
        self.summary.setText(f"Файлов: {s['files']}, прочитано: {s['ok']}, с ошибками: {s['bad']}, листов: {s['pages']}")

    def on_tree_click(self, item: QTreeWidgetItem, _col: int):
        data = item.data(0, Qt.UserRole)
        if not data or not self.obj:
            return
        if data[0] == "xlsx":
            self.details.show_xlsx(self.obj, data[1])
            return
        if data[0] != "page":
            return
        _, file_id, page_no, page_id, status = data
        f = next(x for x in self.obj.files() if x.id == file_id)
        self.sheet.show_page(str(self.obj.file_path(f)), page_no, f"{f.name} — лист {page_no}")
        self.details.show_page(self.obj, page_id, status)

    def add_files_dialog(self):
        if not self.obj:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Файлы комплекта", "", "Документы (*.pdf *.xlsx *.docx *.md)")
        if files:
            self.start_intake([Path(f) for f in files])

    def start_intake(self, files: list[Path]):
        if not self.obj or self.thread:
            return
        self.progress.setVisible(True)
        self.b_stop.setVisible(True)
        self.progress.setRange(0, len(files))
        self.thread = QThread()
        self.worker = IntakeWorker(self.obj, files)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_intake_done)
        self.thread.start()

    def stop_intake(self):
        if self.thread:
            self.worker.stop()

    def on_progress(self, n: int, total: int, name: str):
        self.progress.setValue(n - 1)
        self.progress.setFormat(f"{n}/{total}: {name}")

    def on_intake_done(self, results: list):
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.progress.setVisible(False)
        self.b_stop.setVisible(False)
        self.refresh_files()
        problems = [f"{r.name}: {r.message}" for r in results if r.status != "прочитан"]
        if problems:
            QMessageBox.information(self, "Не все файлы прочитаны", "\n".join(problems))

    def remove_selected(self):
        it = self.tree.currentItem()
        if not it or not self.obj:
            return
        data = it.data(0, Qt.UserRole)
        if not data or data[0] != "file":
            return
        f = next(x for x in self.obj.files() if x.id == data[1])
        if QMessageBox.question(self, "Убрать файл", f"Убрать «{f.name}» из объекта? Копия в папке объекта будет удалена.") \
                == QMessageBox.Yes:
            self.sheet.clear()
            self.details.clear()
            self.obj.remove_file(f.id)
            self.refresh_files()

    def closeEvent(self, e):
        if self.thread:
            self.worker.stop()
            self.thread.quit()
            self.thread.wait(5000)
        self.sheet.clear()
        if self.obj:
            self.obj.close()
        super().closeEvent(e)
