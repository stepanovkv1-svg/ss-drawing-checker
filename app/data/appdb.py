"""База программы в профиле пользователя: недавние объекты и настройки (ТЗ 7.2)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import paths
from . import schema


class AppDb:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.user_data_dir() / "app.db"
        self.con: sqlite3.Connection = schema.migrate(self.path, schema.APP_MIGRATIONS)

    def remember_object(self, folder: Path, name: str) -> None:
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO recent_object(path, name, last_opened) VALUES (?,?,?)",
                             (str(Path(folder).resolve()), name, schema.now()))

    def forget_object(self, folder: str) -> None:
        with self.con:
            self.con.execute("DELETE FROM recent_object WHERE path=?", (folder,))

    def recent_objects(self) -> list[sqlite3.Row]:
        return self.con.execute("SELECT path, name, last_opened FROM recent_object ORDER BY last_opened DESC").fetchall()

    def get(self, key: str, default: str | None = None) -> str | None:
        r = self.con.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set(self, key: str, value: str) -> None:
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?,?)", (key, value))

    def close(self) -> None:
        self.con.close()
