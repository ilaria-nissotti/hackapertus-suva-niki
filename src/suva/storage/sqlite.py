"""SQLite implementation. FTS5 now; sqlite-vec is added in the KB step."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
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
        "decision_id", "docket", "decision_date", "title", "language", "text_de", "text_fr", "author",
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
        with nullcontext() if self.conn.in_transaction else self.conn:
            self.conn.execute(
                f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
                f"ON CONFLICT({key}) DO UPDATE SET {updates}",
                [values[c] for c in names],
            )

    @staticmethod
    def _rows(cur) -> list[dict[str, Any]]:
        return [{**dict(r), "notes": json.loads(r["notes"])} for r in cur.fetchall()]

    def upsert_example(self, row: dict[str, Any]) -> None:
        from suva.ingest.exports import decision_id

        identity = decision_id(row["docket"], row.get("decision_date"))
        if row.get("decision_id", identity) != identity:
            raise ValueError("Decision ID does not match the docket and decision date")
        row = {**row, "decision_id": identity}
        with nullcontext() if self.conn.in_transaction else self.conn:
            if not self.conn.in_transaction:
                self.conn.execute("BEGIN IMMEDIATE")
            existing = self._rows(self.conn.execute(
                "SELECT * FROM example_summaries WHERE decision_id = ?", (row["decision_id"],)
            ))
            if existing and existing[0]["split"]:
                stored = existing[0]
                if any(stored[c] != row.get(c, [] if c == "notes" else None) for c in self._EXAMPLE_COLS):
                    raise ValueError(f"Cannot change frozen example {row['decision_id']}")
                return
            if not existing and self.get_example_manifest() is not None:
                raise ValueError("Use a new database to import a different frozen corpus")
            self._upsert("example_summaries", "decision_id", self._EXAMPLE_COLS, row)

    def list_examples(self, split: str | None = None) -> list[dict[str, Any]]:
        if split:
            return self._rows(self.conn.execute(
                "SELECT * FROM example_summaries WHERE split = ? ORDER BY docket, decision_id", (split,)))
        return self._rows(self.conn.execute("SELECT * FROM example_summaries ORDER BY docket, decision_id"))

    def set_example_split(self, decision_id: str, split: str) -> None:
        with nullcontext() if self.conn.in_transaction else self.conn:
            cur = self.conn.execute(
                "UPDATE example_summaries SET split = ? WHERE decision_id = ? AND (split IS NULL OR split = ?)",
                (split, decision_id, split),
            )
            if cur.rowcount:
                return
            if self.conn.execute(
                "SELECT 1 FROM example_summaries WHERE decision_id = ?", (decision_id,)
            ).fetchone():
                raise ValueError(f"{decision_id} is frozen in a different split")
            raise KeyError(decision_id)

    def replace_supplied(self, examples: list[dict], positives: list[dict]) -> None:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            ids = {e["decision_id"] for e in examples}
            for row in self.list_examples():
                if row["decision_id"] not in ids:
                    if row["split"]:
                        raise ValueError("Cannot remove a frozen example. Use a new database for a new corpus")
                    self.conn.execute("DELETE FROM example_summaries WHERE decision_id = ?", (row["decision_id"],))
            for example in examples:
                self.upsert_example(example)
            self.conn.execute("DELETE FROM known_positives")
            for positive in positives:
                self.upsert_known_positive(positive)

    def get_example_manifest(self) -> dict | None:
        row = self.conn.execute("SELECT payload FROM example_split_manifest WHERE id = 1").fetchone()
        return json.loads(row["payload"]) if row else None

    def freeze_examples(self, manifest: dict) -> dict[str, int]:
        from suva.ingest.split import validate_manifest

        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            examples = self.list_examples()
            validate_manifest(manifest, examples)
            existing = self.get_example_manifest()
            if existing is not None and existing != manifest:
                raise ValueError("Database already has a different frozen manifest")
            assignments = manifest["assignments"]
            for row in examples:
                self.set_example_split(row["decision_id"], assignments[row["decision_id"]])
            self.conn.execute(
                "INSERT OR IGNORE INTO example_split_manifest VALUES (1, ?)", (json.dumps(manifest, sort_keys=True),)
            )
            n_eval = sum(split == "eval" for split in assignments.values())
            return {"newly_assigned": sum(row["split"] is None for row in examples),
                    "fewshot": len(examples) - n_eval, "eval": n_eval}

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
