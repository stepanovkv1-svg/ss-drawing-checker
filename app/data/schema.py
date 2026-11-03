"""Схемы баз SQLite и миграции (ТЗ 7.3: версия схемы, миграция с резервной копией)."""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

# Миграции базы объекта: номер версии -> SQL. Только добавлять новые номера, старые не менять.
OBJECT_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE object (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        name TEXT NOT NULL,
        only_local INTEGER NOT NULL DEFAULT 0,
        source_mode TEXT NOT NULL DEFAULT 'copy',      -- copy | link
        created_at TEXT NOT NULL
    );
    CREATE TABLE file (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        rel_path TEXT NOT NULL,                         -- путь внутри папки объекта (sources/...) или абсолютный при link
        kind TEXT NOT NULL,                             -- PDF | XLSX | MD | DOCX
        sha256 TEXT NOT NULL,
        size INTEGER NOT NULL,
        status TEXT NOT NULL,                           -- прочитан | ошибка | изменён | нет файла
        error TEXT,
        added_at TEXT NOT NULL,
        UNIQUE (sha256)
    );
    CREATE TABLE page (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES file(id) ON DELETE CASCADE,
        page_no INTEGER NOT NULL,
        width_pt REAL NOT NULL, height_pt REAL NOT NULL,
        width_mm INTEGER NOT NULL, height_mm INTEGER NOT NULL,
        format TEXT NOT NULL,
        rotation INTEGER NOT NULL,
        text_chars INTEGER NOT NULL,
        UNIQUE (file_id, page_no)
    );
    CREATE TABLE history (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id INTEGER,
        field TEXT,
        old TEXT,
        new TEXT
    );
    """,
}

APP_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE recent_object (path TEXT PRIMARY KEY, name TEXT NOT NULL, last_opened TEXT NOT NULL);
    CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def schema_version(con: sqlite3.Connection) -> int:
    try:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def migrate(path: Path, migrations: dict[int, str]) -> sqlite3.Connection:
    """Открывает базу и доводит схему до последней версии. Перед миграцией существующей базы — копия .bak."""
    target = max(migrations)
    existed = path.exists()
    con = connect(path)
    cur = schema_version(con)
    if cur > target:
        con.close()
        raise RuntimeError(f"База создана более новой версией программы (схема {cur}, поддерживается {target}).")
    if cur < target:
        if existed and cur > 0:
            con.close()
            shutil.copy2(path, path.with_name(f"{path.stem}.schema{cur}.bak"))
            con = connect(path)
        with con:
            for v in range(cur + 1, target + 1):
                con.executescript(migrations[v])
                con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(v),))
    return con
