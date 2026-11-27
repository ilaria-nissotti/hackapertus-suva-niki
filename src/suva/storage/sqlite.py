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
        applied = []
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            done = {r["name"] for r in self.conn.execute("SELECT name FROM schema_migrations")}
            for name, sql in MIGRATIONS:
                if name in done:
                    continue
                statement = ""
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        self.conn.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError(f"Incomplete SQL in migration {name}")
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

    # --- supplied data (step 2) -------------------------------------------
    _EXAMPLE_COLS = (
        "docket", "decision_date", "title", "language", "text_de", "text_fr", "author",
        "source_url", "bger_url", "aza_docid", "source_file", "notes",
    )
    _POSITIVE_COLS = (
        "ref", "docket", "court", "in_xlsx", "in_exports", "quarter", "summary_created_at", "notes",
    )

    def _upsert(self, table: str, key: str, cols: tuple[str, ...], row: dict[str, Any]) -> None:
        unknown = set(row) - set(cols)
        if unknown:
            raise ValueError(f"{table}: unknown columns {sorted(unknown)}")
        values = {c: row.get(c) for c in cols}
        values["notes"] = json.dumps(values.get("notes") or [], ensure_ascii=False)
        values["imported_at"] = utcnow()
        names = list(values)
        updates = ", ".join(f"{c} = excluded.{c}" for c in names if c != key)
        with self.conn:
            self.conn.execute(
                f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
                f"ON CONFLICT({key}) DO UPDATE SET {updates}",
                [values[c] for c in names],
            )

    @staticmethod
    def _rows(cur) -> list[dict[str, Any]]:
        return [{**dict(r), "notes": json.loads(r["notes"])} for r in cur.fetchall()]

    def upsert_example(self, row: dict[str, Any]) -> None:
        self._upsert("example_summaries", "docket", self._EXAMPLE_COLS, row)

    def list_examples(self, split: str | None = None) -> list[dict[str, Any]]:
        if split:
            return self._rows(self.conn.execute(
                "SELECT * FROM example_summaries WHERE split = ? ORDER BY docket", (split,)))
        return self._rows(self.conn.execute("SELECT * FROM example_summaries ORDER BY docket"))

    def set_example_split(self, docket: str, split: str) -> None:
        row = self.conn.execute("SELECT split FROM example_summaries WHERE docket = ?", (docket,)).fetchone()
        if row is None:
            raise KeyError(docket)
        if row["split"] not in (None, split):
            raise ValueError(f"{docket} is frozen in split {row['split']!r}; refusing to move it to {split!r}")
        with self.conn:
            self.conn.execute("UPDATE example_summaries SET split = ? WHERE docket = ?", (split, docket))

    def upsert_known_positive(self, row: dict[str, Any]) -> None:
        self._upsert("known_positives", "ref", self._POSITIVE_COLS, row)

    def list_known_positives(self, court: str | None = None) -> list[dict[str, Any]]:
        if court:
            return self._rows(self.conn.execute(
                "SELECT * FROM known_positives WHERE court = ? ORDER BY ref", (court,)))
        return self._rows(self.conn.execute("SELECT * FROM known_positives ORDER BY ref"))

    def close(self) -> None:
        self.conn.close()


def open_repository(settings: Settings, migrate: bool = True) -> Repository:
    repo = SQLiteRepository(settings.sqlite_path)
    if migrate:
        repo.migrate()
    return repo
