"""SQLite implementation. FTS5 now; sqlite-vec is added in the KB step."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from suva.config import Settings
from suva.storage.base import AuditEntry, Repository
from suva.storage.migrations import MIGRATIONS


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SQLiteRepository(Repository):
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def migrate(self) -> list[str]:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {r["name"] for r in self.conn.execute("SELECT name FROM schema_migrations")}
        applied = []
        for name, sql in MIGRATIONS:
            if name in done:
                continue
            with self.conn:
                self.conn.executescript(sql)
                self.conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)", (name, utcnow())
                )
            applied.append(name)
        return applied

    def append_audit(self, entry: AuditEntry) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO audit_log (created_at, kind, actor, payload) VALUES (?, ?, ?, ?)",
                (utcnow(), entry.kind, entry.actor, json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)),
            )
        return int(cur.lastrowid)

    def list_audit(self, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_log"
        args: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self.conn.execute(sql, (*args, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def close(self) -> None:
        self.conn.close()


def open_repository(settings: Settings, migrate: bool = True) -> Repository:
    repo = SQLiteRepository(settings.sqlite_path)
    if migrate:
        repo.migrate()
    return repo
