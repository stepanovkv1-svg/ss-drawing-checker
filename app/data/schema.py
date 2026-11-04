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
    2: """
    CREATE TABLE text_item (
        id INTEGER PRIMARY KEY,
        page_id INTEGER NOT NULL REFERENCES page(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,   -- пункты PDF
        source TEXT NOT NULL,              -- слой | OCR | пользователь
        confidence REAL,                   -- для OCR, %
        version TEXT NOT NULL
    );
    CREATE INDEX text_item_page ON text_item(page_id);
    CREATE TABLE stamp (
        page_id INTEGER PRIMARY KEY REFERENCES page(id) ON DELETE CASCADE,
        recognized INTEGER NOT NULL,
        source TEXT NOT NULL,              -- слой | OCR
        note TEXT,
        confirmed INTEGER NOT NULL DEFAULT 0,
        version TEXT NOT NULL
    );
    CREATE TABLE stamp_field (
        id INTEGER PRIMARY KEY,
        page_id INTEGER NOT NULL REFERENCES stamp(page_id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        rects TEXT NOT NULL,               -- JSON: [[x0,y0,x1,y1], ...]
        confidence TEXT NOT NULL,
        edited INTEGER NOT NULL DEFAULT 0,
        UNIQUE (page_id, name)
    );
    CREATE TABLE signature (
        id INTEGER PRIMARY KEY,
        page_id INTEGER NOT NULL REFERENCES stamp(page_id) ON DELETE CASCADE,
        role TEXT NOT NULL, surname TEXT NOT NULL, date TEXT NOT NULL, rects TEXT NOT NULL
    );
    CREATE TABLE xlsx_table (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES file(id) ON DELETE CASCADE,
        sheet TEXT NOT NULL,
        header_row INTEGER NOT NULL,
        schema TEXT NOT NULL,              -- JSON: {буква: {header, role}}
        confidence TEXT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0,
        version TEXT NOT NULL
    );
    CREATE TABLE xlsx_row (
        id INTEGER PRIMARY KEY,
        table_id INTEGER NOT NULL REFERENCES xlsx_table(id) ON DELETE CASCADE,
        row_no INTEGER NOT NULL,
        cells TEXT NOT NULL                -- JSON: {буква: значение}
    );
    ALTER TABLE page ADD COLUMN extract_status TEXT;      -- NULL | готово | OCR: неуверенно | ошибка: …
    """,
    3: """
    CREATE TABLE classification (
        page_id INTEGER PRIMARY KEY REFERENCES page(id) ON DELETE CASCADE,
        data TEXT NOT NULL,                -- JSON: марка, раздел, подсистемы, типы — значение, уверенность, признаки
        level TEXT NOT NULL,               -- уверенно | спорно | не определено
        conflict TEXT NOT NULL DEFAULT '', -- противоречие «шифр ↔ содержание»
        source TEXT NOT NULL,              -- авто | пользователь
        dict_version TEXT NOT NULL,
        decision_key TEXT NOT NULL
    );
    CREATE TABLE class_decision (
        key TEXT PRIMARY KEY,              -- документ, шифр, номер и наименование листа (ТЗ 4.5)
        data TEXT NOT NULL,
        decided_at TEXT NOT NULL
    );
    """,
    4: """
    CREATE TABLE check_run (
        id INTEGER PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,              -- идёт | готово | остановлено | ошибка
        versions TEXT NOT NULL             -- JSON: программа, словари, правила
    );
    CREATE TABLE check_stage (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES check_run(id) ON DELETE CASCADE,
        code TEXT NOT NULL,                -- БП-01 …
        title TEXT NOT NULL,
        status TEXT NOT NULL,              -- выполнено | частично | пропущено | ручная проверка | ошибка
        message TEXT NOT NULL DEFAULT '',
        findings INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE finding (
        id INTEGER PRIMARY KEY,
        fingerprint TEXT NOT NULL UNIQUE,  -- правило + причина: повторная проверка узнаёт то же замечание
        rule TEXT NOT NULL,                -- БП-02 … / инструкция
        grp INTEGER NOT NULL,              -- 1 Оформление, 2 Техническая часть, 3 Расстановка оборудования
        cls TEXT NOT NULL,                 -- подтверждённая ошибка | возможная ошибка | требуется уточнение
        severity TEXT NOT NULL DEFAULT 'Не задана',
        text TEXT NOT NULL,
        basis TEXT NOT NULL,               -- основание: правило / инструкция
        proof TEXT NOT NULL,               -- доказательство: значения из извлечения
        decision TEXT NOT NULL DEFAULT 'не рассмотрено',   -- не рассмотрено | принято | отклонено
        review TEXT NOT NULL DEFAULT '',   -- '' | требуется пересмотр (внутреннее состояние)
        edited INTEGER NOT NULL DEFAULT 0,
        run_id INTEGER REFERENCES check_run(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE place (
        id INTEGER PRIMARY KEY,
        finding_id INTEGER NOT NULL REFERENCES finding(id) ON DELETE CASCADE,
        page_id INTEGER REFERENCES page(id) ON DELETE CASCADE,
        file_name TEXT NOT NULL,
        page_no INTEGER,
        sheet TEXT NOT NULL DEFAULT '',     -- номер листа по штампу
        x0 REAL, y0 REAL, x1 REAL, y1 REAL, -- пункты PDF; NULL — весь лист или комплект
        method TEXT NOT NULL,               -- слой | OCR | штамп | зона штампа | вручную | весь лист | XLSX
        cell TEXT NOT NULL DEFAULT '',      -- адрес ячейки XLSX
        shot BLOB,
        ord INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX place_finding ON place(finding_id);
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
