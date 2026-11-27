"""SQLite implementation. FTS5 now; sqlite-vec is added in the KB step."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from suva.config import Settings
from suva.hashing import sha256
from suva.sources.quality import usable_text
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

    # --- judgements + coverage ledger (step 3) -----------------------------
    def upsert_judgement(self, rec) -> bool:
        now = utcnow()
        d = rec.decision_date.isoformat() if rec.decision_date else None
        pub = rec.publication_date.isoformat() if rec.publication_date else None
        prefix = rec.docket.split("_", 1)[0] if "_" in rec.docket else None
        text = usable_text(rec.text)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT text, publication_date, language FROM judgements WHERE key = ?", (rec.key,)
            ).fetchone()
            new = row is None
            if new:
                self.conn.execute(
                    "INSERT INTO judgements (key, docket, decision_date, chamber_prefix, language,"
                    " publication_date, publication_date_source, text, text_source,"
                    " first_seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.key, rec.docket, d, prefix, rec.language, pub, rec.source if pub else None,
                     text, rec.source if text else None, now, now),
                )
            else:
                current = len(row["text"] or "") if usable_text(row["text"]) else 0
                if text and len(text) > current:
                    self.conn.execute(
                        "UPDATE judgements SET text = ?, text_source = ?, updated_at = ? WHERE key = ?",
                        (text, rec.source, now, rec.key))
                    self._delete_parse(rec.key)
                if pub and row["publication_date"] is None:
                    self.conn.execute(
                        "UPDATE judgements SET publication_date = ?, publication_date_source = ?,"
                        " updated_at = ? WHERE key = ?", (pub, rec.source, now, rec.key))
                if rec.language and row["language"] is None:
                    self.conn.execute("UPDATE judgements SET language = ? WHERE key = ?", (rec.language, rec.key))
            self.conn.execute(
                "INSERT INTO judgement_sources (key, source, source_id, chamber_raw, url, html_url,"
                " source_seen_at, retrieved_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(key, source) DO UPDATE SET source_id = excluded.source_id,"
                " chamber_raw = excluded.chamber_raw, url = COALESCE(excluded.url, url),"
                " html_url = COALESCE(excluded.html_url, html_url),"
                " source_seen_at = excluded.source_seen_at, retrieved_at = excluded.retrieved_at",
                (rec.key, rec.source, rec.source_id, rec.chamber_raw, rec.url, rec.html_url, rec.seen_at, now),
            )
        return new

    def clear_unusable_text(self, key: str) -> bool:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT text FROM judgements WHERE key = ?", (key,)).fetchone()
            if row is None or row["text"] is None or usable_text(row["text"]):
                return False
            self.conn.execute("UPDATE judgements SET text = NULL, text_source = NULL, updated_at = ? WHERE key = ?",
                              (utcnow(), key))
            self._delete_parse(key)
            return True

    def get_judgement(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM judgements WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def list_judgements(self, start=None, end=None, dockets=None) -> list[dict[str, Any]]:
        cols = ("key, docket, decision_date, chamber_prefix, language, publication_date,"
                " publication_date_source, text_source, length(text) AS text_length, html_path,"
                " first_seen_at")
        sql, args = f"SELECT {cols} FROM judgements WHERE 1=1", []
        if start:
            sql += " AND decision_date >= ?"
            args.append(start)
        if end:
            sql += " AND decision_date <= ?"
            args.append(end)
        rows = [dict(r) for r in self.conn.execute(sql + " ORDER BY decision_date, docket", args)]
        if dockets is not None:
            rows = [r for r in rows if r["docket"] in dockets]
        return rows

    def judgement_sources(self, key: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM judgement_sources WHERE key = ? ORDER BY source", (key,))]

    def set_html_path(self, key: str, path: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE judgements SET html_path = ?, updated_at = ? WHERE key = ?",
                              (path, utcnow(), key))

    # --- passages + citations (step 4) ------------------------------------
    def _delete_parse(self, key: str) -> None:
        self.conn.execute("DELETE FROM citations WHERE pid IN (SELECT pid FROM passages WHERE key = ?)", (key,))
        self.conn.execute("DELETE FROM passages WHERE key = ?", (key,))
        self.conn.execute("DELETE FROM parses WHERE key = ?", (key,))

    def save_parse(self, seg, citations, text_hash) -> bool:
        n_cit = sum(len(v) for v in citations.values())
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT text FROM judgements WHERE key = ?", (seg.key,)).fetchone()
            if row is None or (sha256(row["text"]) if row["text"] else None) != text_hash:
                return False
            self._delete_parse(seg.key)
            self.conn.executemany(
                'INSERT INTO passages (pid, key, ord, kind, number, text, start, "end") VALUES (?,?,?,?,?,?,?,?)',
                [(p.pid, seg.key, i, p.kind, p.number, p.text, p.start, p.end) for i, p in enumerate(seg.passages)],
            )
            self.conn.executemany(
                "INSERT INTO citations (pid, kind, canonical, raw, law, article, consideration, start, \"end\")"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                [(pid, c.kind, c.canonical, c.raw, c.law, c.article, c.consideration, c.start, c.end)
                 for pid, cs in citations.items() for c in cs],
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO parses (key, status, issues, parser_version, text_hash, chamber,"
                " parties, subject, passages, citations, parsed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (seg.key, seg.status, json.dumps(seg.issues), seg.parser_version, text_hash, seg.chamber,
                 seg.parties, seg.subject, len(seg.passages), n_cit, utcnow()),
            )
        return True

    def get_parse(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM parses WHERE key = ?", (key,)).fetchone()
        return {**dict(row), "issues": json.loads(row["issues"])} if row else None

    def list_passages(self, key: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM passages WHERE key = ? ORDER BY ord", (key,))]

    def list_citations(self, key: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT c.* FROM citations c JOIN passages p ON p.pid = c.pid WHERE p.key = ?"
            " ORDER BY p.ord, c.start", (key,))]

    def parse_summary(self) -> dict[str, Any]:
        status = dict(self.conn.execute("SELECT status, count(*) FROM parses GROUP BY status").fetchall())
        issues: dict[str, int] = {}
        for (raw,) in self.conn.execute("SELECT issues FROM parses"):
            for i in json.loads(raw):
                issues[i] = issues.get(i, 0) + 1
        unparsed = self.conn.execute(
            "SELECT count(*) FROM judgements j LEFT JOIN parses p ON p.key = j.key WHERE p.key IS NULL"
        ).fetchone()[0]
        return {"status": status, "issues": issues, "unparsed": unparsed}

    def close(self) -> None:
        self.conn.close()


def open_repository(settings: Settings, migrate: bool = True) -> Repository:
    repo = SQLiteRepository(settings.sqlite_path)
    if migrate:
        repo.migrate()
    return repo
