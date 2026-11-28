"""SQLite persistence for supplied data, judgements, and parse results."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from suva.config import Settings
from suva.hashing import sha256
from suva.sources.quality import usable_text
from suva.sources.base import decision_id
from suva.storage.base import AuditEntry, Repository
from suva.storage.migrations import MIGRATIONS
from suva.windows import Window


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date_filter(start: str | None, end: str | None, window: Window | None) -> tuple[str, list[str]]:
    if window is not None:
        start, end = window.start.isoformat(), window.end.isoformat()
        if window.published_from is not None:
            return (
                " AND (j.publication_date BETWEEN ? AND ?"
                " OR (j.publication_date IS NULL AND j.decision_date BETWEEN ? AND ?))",
                [window.published_from.isoformat(), end, start, end],
            )
    sql, args = "", []
    if start:
        sql += " AND j.decision_date >= ?"
        args.append(start)
    if end:
        sql += " AND j.decision_date <= ?"
        args.append(end)
    return sql, args


class SQLiteRepository(Repository):
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    @contextmanager
    def _write(self):
        # Nested writes belong to the outer operation's transaction.
        if self.conn.in_transaction:
            yield
        else:
            with self.conn:
                self.conn.execute("BEGIN IMMEDIATE")
                yield

    def transaction(self):
        return self._write()

    def schema_is_current(self) -> bool:
        table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not table:
            return False
        done = {r["name"] for r in self.conn.execute("SELECT name FROM schema_migrations")}
        return all(name in done for name, _ in MIGRATIONS)

    def migrate(self) -> list[str]:
        applied = []
        with self._write():
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
        with self._write():
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
        with self._write():
            self.conn.execute(
                f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
                f"ON CONFLICT({key}) DO UPDATE SET {updates}",
                [values[c] for c in names],
            )

    @staticmethod
    def _rows(cur) -> list[dict[str, Any]]:
        return [{**dict(r), "notes": json.loads(r["notes"])} for r in cur.fetchall()]

    def upsert_example(self, row: dict[str, Any]) -> None:
        identity = decision_id(row["docket"], row.get("decision_date"))
        if row.get("decision_id", identity) != identity:
            raise ValueError("Decision ID does not match the docket and decision date")
        row = {**row, "decision_id": identity}
        with self._write():
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
        with self._write():
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
        with self._write():
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

        with self._write():
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
        with self._write():
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
        with self._write():
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

    def list_judgements(self, start=None, end=None, dockets=None, *, window=None) -> list[dict[str, Any]]:
        cols = ("key, docket, decision_date, chamber_prefix, language, publication_date,"
                " publication_date_source, text_source, length(text) AS text_length, html_path,"
                " first_seen_at")
        date_sql, args = _date_filter(start, end, window)
        sql = f"SELECT {cols} FROM judgements j WHERE 1=1" + date_sql
        rows = [dict(r) for r in self.conn.execute(sql + " ORDER BY decision_date, docket", args)]
        if dockets is not None:
            rows = [r for r in rows if r["docket"] in dockets]
        return rows

    def judgement_sources(self, key: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM judgement_sources WHERE key = ? ORDER BY source", (key,))]

    def set_html_path(self, key: str, path: str) -> None:
        with self._write():
            self.conn.execute("UPDATE judgements SET html_path = ?, updated_at = ? WHERE key = ?",
                              (path, utcnow(), key))

    # --- passages + citations (step 4) ------------------------------------
    def _delete_parse(self, key: str) -> None:
        self.conn.execute("DELETE FROM citations WHERE pid IN (SELECT pid FROM passages WHERE key = ?)", (key,))
        self.conn.execute("DELETE FROM passages WHERE key = ?", (key,))
        self.conn.execute("DELETE FROM parses WHERE key = ?", (key,))

    def save_parse(self, seg, citations, text_hash) -> bool:
        n_cit = sum(len(v) for v in citations.values())
        with self._write():
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
                 seg.parties, seg.subject, len(seg.passages), n_cit, datetime.now(timezone.utc).isoformat()),
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

    # --- relevance scores ------------------------------------------------
    _SCORE_COLS = ("key", "score", "points", "band", "fired", "rules_hash", "parser_version", "text_hash", "inputs_hash")

    def save_scores(self, rows: list[dict[str, Any]]) -> None:
        now = utcnow()
        with self._write():
            self.conn.executemany(
                f"INSERT OR REPLACE INTO relevance_scores ({', '.join(self._SCORE_COLS)}, scored_at)"
                f" VALUES ({', '.join('?' * (len(self._SCORE_COLS) + 1))})",
                [(*(json.dumps(r["fired"], ensure_ascii=False) if c == "fired" else r.get(c) for c in self._SCORE_COLS),
                  now) for r in rows],
            )

    @staticmethod
    def _score_row(row) -> dict[str, Any]:
        return {**dict(row), "fired": json.loads(row["fired"])}

    def get_score(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM relevance_scores WHERE key = ?", (key,)).fetchone()
        return self._score_row(row) if row else None

    def list_scores(self) -> list[dict[str, Any]]:
        return [self._score_row(r) for r in self.conn.execute("SELECT * FROM relevance_scores ORDER BY score DESC, points DESC")]

    def list_review_rows(self, start=None, end=None, keys=None, *, include_text=False, window=None) -> list[dict[str, Any]]:
        sql = ("SELECT j.key, j.docket, j.decision_date, j.publication_date, j.publication_date_source,"
               f" j.language, {'j.text' if include_text else 'NULL'} AS text,"
               " p.subject, p.parsed_at, p.text_hash AS parse_text_hash, p.parser_version,"
               " s.score, s.points, s.band, s.fired, s.rules_hash, s.inputs_hash,"
               " s.parser_version AS score_parser_version, s.text_hash AS score_text_hash"
               " FROM judgements j LEFT JOIN parses p ON p.key = j.key"
               " LEFT JOIN relevance_scores s ON s.key = j.key WHERE 1=1")
        date_sql, args = _date_filter(start, end, window)
        sql += date_sql
        if keys is not None:
            if not keys:
                return []
            sql += f" AND j.key IN ({', '.join('?' * len(keys))})"
            args.extend(keys)
        return [{**dict(r), "fired": json.loads(r["fired"]) if r["fired"] else []}
                for r in self.conn.execute(sql + " ORDER BY j.decision_date DESC, j.docket", args)]

    def close(self) -> None:
        self.conn.close()


def open_repository(settings: Settings, migrate: bool = True) -> Repository:
    repo = SQLiteRepository(settings.sqlite_path)
    if migrate:
        repo.migrate()
    return repo
