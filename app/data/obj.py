"""Папка объекта (ТЗ 11.3, Р79): object.db, sources/, cache/, reports/, logs/."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import schema

DB_NAME = "object.db"
SUBDIRS = ("sources", "cache", "reports", "logs")
KINDS = {".pdf": "PDF", ".xlsx": "XLSX", ".md": "MD", ".docx": "DOCX"}


class ObjectError(Exception):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class FileRow:
    id: int
    name: str
    rel_path: str
    kind: str
    sha256: str
    size: int
    status: str
    error: str | None


class ProjectObject:
    """Открытый объект проверки. Все изменения пишутся в историю."""

    def __init__(self, folder: Path, con: sqlite3.Connection):
        self.folder = folder
        self.con = con

    # ------------------------------------------------------------ создание и открытие
    @classmethod
    def create(cls, folder: Path, name: str) -> "ProjectObject":
        folder = Path(folder)
        if (folder / DB_NAME).exists():
            raise ObjectError("В этой папке уже есть объект — откройте его.")
        folder.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:
            (folder / d).mkdir(exist_ok=True)
        con = schema.migrate(folder / DB_NAME, schema.OBJECT_MIGRATIONS)
        with con:
            con.execute("INSERT INTO object(id, name, created_at) VALUES (1, ?, ?)", (name.strip() or folder.name, schema.now()))
        return cls(folder, con)

    @classmethod
    def open(cls, folder: Path) -> "ProjectObject":
        folder = Path(folder)
        if not (folder / DB_NAME).exists():
            raise ObjectError("В папке нет объекта проверки (object.db).")
        for d in SUBDIRS:
            (folder / d).mkdir(exist_ok=True)
        con = schema.migrate(folder / DB_NAME, schema.OBJECT_MIGRATIONS)
        obj = cls(folder, con)
        obj.verify_sources()
        return obj

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------ свойства
    @property
    def name(self) -> str:
        return self.con.execute("SELECT name FROM object WHERE id=1").fetchone()[0]

    @property
    def only_local(self) -> bool:
        return bool(self.con.execute("SELECT only_local FROM object WHERE id=1").fetchone()[0])

    def set_only_local(self, value: bool) -> None:
        old = self.only_local
        if old == value:
            return
        with self.con:
            self.con.execute("UPDATE object SET only_local=? WHERE id=1", (int(value),))
            self._history("object", 1, "only_local", old, value)

    def _history(self, entity: str, entity_id, field, old, new) -> None:
        self.con.execute("INSERT INTO history(ts, entity, entity_id, field, old, new) VALUES (?,?,?,?,?,?)",
                         (schema.now(), entity, entity_id, field, None if old is None else str(old),
                          None if new is None else str(new)))

    # ------------------------------------------------------------ файлы
    def files(self) -> list[FileRow]:
        rows = self.con.execute("SELECT id, name, rel_path, kind, sha256, size, status, error FROM file ORDER BY id").fetchall()
        return [FileRow(**dict(r)) for r in rows]

    def file_path(self, f: FileRow) -> Path:
        p = Path(f.rel_path)
        return p if p.is_absolute() else self.folder / p

    def add_source(self, src: Path) -> tuple[FileRow, bool]:
        """Копирует файл в sources/ и регистрирует его. Возвращает (строка, добавлен_ли_новый).
        Тот же файл (по SHA-256) повторно не добавляется. Чтение содержимого — отдельный шаг (intake)."""
        src = Path(src)
        kind = KINDS.get(src.suffix.lower())
        if not kind:
            raise ObjectError(f"{src.name}: тип файла не поддерживается (нужны PDF, XLSX, MD или DOCX).")
        digest = sha256_of(src)
        row = self.con.execute("SELECT id FROM file WHERE sha256=?", (digest,)).fetchone()
        if row:
            return self._file(row[0]), False
        dest = self.folder / "sources" / src.name
        n = 1
        while dest.exists():
            dest = self.folder / "sources" / f"{src.stem} ({n}){src.suffix}"
            n += 1
        shutil.copy2(src, dest)
        with self.con:
            cur = self.con.execute(
                "INSERT INTO file(name, rel_path, kind, sha256, size, status, added_at) VALUES (?,?,?,?,?,?,?)",
                (src.name, str(dest.relative_to(self.folder).as_posix()), kind, digest, dest.stat().st_size,
                 "добавлен", schema.now()))
            self._history("file", cur.lastrowid, "added", None, src.name)
        return self._file(cur.lastrowid), True

    def _file(self, file_id: int) -> FileRow:
        r = self.con.execute("SELECT id, name, rel_path, kind, sha256, size, status, error FROM file WHERE id=?",
                             (file_id,)).fetchone()
        return FileRow(**dict(r))

    def set_file_status(self, file_id: int, status: str, error: str | None = None) -> None:
        with self.con:
            self.con.execute("UPDATE file SET status=?, error=? WHERE id=?", (status, error, file_id))

    def remove_file(self, file_id: int) -> None:
        f = self._file(file_id)
        with self.con:
            self.con.execute("DELETE FROM file WHERE id=?", (file_id,))
            self._history("file", file_id, "removed", f.name, None)
        p = self.file_path(f)
        if p.is_relative_to(self.folder / "sources") and p.exists():
            p.unlink()

    def verify_sources(self) -> list[str]:
        """Сверка контрольных сумм при открытии (ТЗ 7.3). Возвращает список предупреждений."""
        warnings = []
        for f in self.files():
            p = self.file_path(f)
            if not p.exists():
                self.set_file_status(f.id, "нет файла", "Исходный файл отсутствует в папке объекта")
                warnings.append(f"{f.name}: файл отсутствует")
            elif sha256_of(p) != f.sha256:
                self.set_file_status(f.id, "изменён", "Файл изменён после добавления; результаты по нему устарели")
                warnings.append(f"{f.name}: файл изменён")
        return warnings

    # ------------------------------------------------------------ листы
    def replace_pages(self, file_id: int, pages: list[dict]) -> None:
        with self.con:
            self.con.execute("DELETE FROM page WHERE file_id=?", (file_id,))
            self.con.executemany(
                "INSERT INTO page(file_id, page_no, width_pt, height_pt, width_mm, height_mm, format, rotation, text_chars)"
                " VALUES (:file_id, :page_no, :width_pt, :height_pt, :width_mm, :height_mm, :format, :rotation, :text_chars)",
                [dict(p, file_id=file_id) for p in pages])

    def pages(self, file_id: int) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM page WHERE file_id=? ORDER BY page_no", (file_id,)).fetchall()

    def stamp(self, page_id: int) -> dict | None:
        st = self.con.execute("SELECT * FROM stamp WHERE page_id=?", (page_id,)).fetchone()
        if not st:
            return None
        fields = {r["name"]: dict(r) for r in self.con.execute(
            "SELECT name, value, rects, confidence, edited FROM stamp_field WHERE page_id=?", (page_id,))}
        sigs = [dict(r) for r in self.con.execute(
            "SELECT role, surname, date FROM signature WHERE page_id=? ORDER BY id", (page_id,))]
        return {"recognized": bool(st["recognized"]), "source": st["source"], "note": st["note"],
                "confirmed": bool(st["confirmed"]), "fields": fields, "signatures": sigs}

    def set_stamp_field(self, page_id: int, name: str, value: str) -> None:
        """Правка поля штампа пользователем: сохраняется и не перезаписывается при пересчёте."""
        old = self.con.execute("SELECT value FROM stamp_field WHERE page_id=? AND name=?", (page_id, name)).fetchone()
        with self.con:
            if old:
                self.con.execute("UPDATE stamp_field SET value=?, edited=1, confidence='пользователь' "
                                 "WHERE page_id=? AND name=?", (value, page_id, name))
            else:
                self.con.execute("INSERT INTO stamp_field(page_id, name, value, rects, confidence, edited) "
                                 "VALUES (?,?,?,'[]','пользователь',1)", (page_id, name, value))
            self._history("stamp_field", page_id, name, old[0] if old else None, value)

    def text_items(self, page_id: int) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM text_item WHERE page_id=? ORDER BY id", (page_id,)).fetchall()

    def xlsx_tables(self, file_id: int) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM xlsx_table WHERE file_id=? ORDER BY id", (file_id,)).fetchall()

    def xlsx_rows(self, table_id: int) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM xlsx_row WHERE table_id=? ORDER BY row_no", (table_id,)).fetchall()

    def summary(self) -> dict:
        r = self.con.execute(
            "SELECT COUNT(*) AS files, SUM(status='прочитан') AS ok, SUM(status!='прочитан') AS bad FROM file").fetchone()
        pages = self.con.execute("SELECT COUNT(*) FROM page").fetchone()[0]
        return {"files": r["files"] or 0, "ok": r["ok"] or 0, "bad": r["bad"] or 0, "pages": pages}
