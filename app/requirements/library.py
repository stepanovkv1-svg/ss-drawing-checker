"""Библиотека инструкций в базе программы: создание, версии, область, включение (ФТ-14, Р33, Р39)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..data import schema
from .model import Requirement, Scope
from .parse import parse_text, read_text


@dataclass
class Instruction:
    id: int
    name: str
    enabled: bool
    scope: Scope
    ver: int = 0
    version_id: int | None = None
    text: str = ""
    requirements: list[Requirement] = field(default_factory=list)
    source_file: str = ""

    def snapshot(self) -> dict:
        return {"instruction_id": self.id, "name": self.name, "ver": self.ver, "scope": self.scope.__dict__,
                "text": self.text, "requirements": [r.to_json() for r in self.requirements]}


def _load(con, row) -> Instruction:
    ins = Instruction(row["id"], row["name"], bool(row["enabled"]), Scope.from_json(json.loads(row["scope"])))
    v = con.execute("SELECT * FROM instruction_version WHERE instruction_id=? ORDER BY ver DESC LIMIT 1",
                    (row["id"],)).fetchone()
    if v:
        ins.ver, ins.version_id, ins.text, ins.source_file = v["ver"], v["id"], v["text"], v["source_file"]
        ins.requirements = [Requirement.from_json(d) for d in json.loads(v["requirements"])]
    return ins


def instructions(con: sqlite3.Connection) -> list[Instruction]:
    return [_load(con, r) for r in con.execute("SELECT * FROM instruction ORDER BY name COLLATE NOCASE, id")]


def get(con: sqlite3.Connection, iid: int) -> Instruction | None:
    r = con.execute("SELECT * FROM instruction WHERE id=?", (iid,)).fetchone()
    return _load(con, r) if r else None


def enabled(con: sqlite3.Connection) -> list[Instruction]:
    return [i for i in instructions(con) if i.enabled and i.ver]


def create(con: sqlite3.Connection, name: str, text: str = "", source_file: str = "") -> int:
    now = schema.now()
    with con:
        iid = con.execute("INSERT INTO instruction(name, created_at, updated_at) VALUES (?,?,?)",
                          (name.strip() or "Инструкция", now, now)).lastrowid
    if text:
        save_version(con, iid, text, parse_text(text), source_file, "загрузка")
    return iid


def import_file(con: sqlite3.Connection, path: Path) -> int:
    path = Path(path)
    return create(con, path.stem, read_text(path), path.name)


def save_version(con: sqlite3.Connection, iid: int, text: str, reqs: list[Requirement], source_file: str = "",
                 note: str = "") -> int:
    """Новая версия. Если ничего не изменилось — версия не создаётся."""
    last = con.execute("SELECT * FROM instruction_version WHERE instruction_id=? ORDER BY ver DESC LIMIT 1",
                       (iid,)).fetchone()
    data = json.dumps([r.to_json() for r in reqs], ensure_ascii=False, sort_keys=True)
    if last and last["text"] == text and last["requirements"] == data:
        return last["ver"]
    ver = (last["ver"] if last else 0) + 1
    now = schema.now()
    with con:
        con.execute("INSERT INTO instruction_version(instruction_id, ver, text, requirements, source_file, note,"
                    " created_at) VALUES (?,?,?,?,?,?,?)",
                    (iid, ver, text, data, source_file or (last["source_file"] if last else ""), note, now))
        con.execute("UPDATE instruction SET updated_at=? WHERE id=?", (now, iid))
    return ver


def versions(con: sqlite3.Connection, iid: int) -> list[sqlite3.Row]:
    return con.execute("SELECT id, ver, source_file, note, created_at FROM instruction_version "
                       "WHERE instruction_id=? ORDER BY ver DESC", (iid,)).fetchall()


def version(con: sqlite3.Connection, version_id: int) -> tuple[str, list[Requirement]]:
    v = con.execute("SELECT text, requirements FROM instruction_version WHERE id=?", (version_id,)).fetchone()
    return v["text"], [Requirement.from_json(d) for d in json.loads(v["requirements"])]


def set_enabled(con: sqlite3.Connection, iid: int, on: bool) -> None:
    with con:
        con.execute("UPDATE instruction SET enabled=?, updated_at=? WHERE id=?", (int(on), schema.now(), iid))


def set_scope(con: sqlite3.Connection, iid: int, scope: Scope) -> None:
    with con:
        con.execute("UPDATE instruction SET scope=?, updated_at=? WHERE id=?",
                    (json.dumps(scope.__dict__, ensure_ascii=False), schema.now(), iid))


def rename(con: sqlite3.Connection, iid: int, name: str) -> None:
    with con:
        con.execute("UPDATE instruction SET name=?, updated_at=? WHERE id=?", (name.strip(), schema.now(), iid))


def delete(con: sqlite3.Connection, iid: int) -> None:
    with con:
        con.execute("DELETE FROM instruction WHERE id=?", (iid,))


def export_md(ins: Instruction) -> str:
    return ins.text
