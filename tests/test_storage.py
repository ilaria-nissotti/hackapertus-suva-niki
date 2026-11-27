import sqlite3

import pytest

from suva.storage import AuditEntry


def test_migrate_is_idempotent(repo):
    assert repo.migrate() == []


def test_audit_append_and_list(repo):
    repo.append_audit(AuditEntry(kind="llm_call", actor="m", payload={"a": 1}))
    repo.append_audit(AuditEntry(kind="reviewer_action", actor="alice", payload={"b": 2}))
    assert [r["kind"] for r in repo.list_audit()] == ["reviewer_action", "llm_call"]
    assert repo.list_audit(kind="llm_call")[0]["payload"] == {"a": 1}


def test_audit_is_append_only(repo):
    repo.append_audit(AuditEntry(kind="llm_call", actor="m"))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repo.conn.execute("UPDATE audit_log SET actor = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repo.conn.execute("DELETE FROM audit_log")


def test_fts5_available(repo):
    # The KB step relies on FTS5 with diacritic folding (§8).
    repo.conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='unicode61 remove_diacritics 2')")
    repo.conn.execute("INSERT INTO t VALUES ('causalité adéquate')")
    assert repo.conn.execute("SELECT count(*) FROM t WHERE t MATCH 'causalite'").fetchone()[0] == 1


def test_failed_migration_rolls_back_schema(repo, monkeypatch):
    from suva.storage import sqlite

    monkeypatch.setattr(sqlite, "MIGRATIONS", [
        ("002_failure", "CREATE TABLE example (id INTEGER);\nINVALID SQL;"),
    ])
    with pytest.raises(sqlite3.OperationalError):
        repo.migrate()
    assert repo.conn.execute("SELECT name FROM sqlite_master WHERE name = 'example'").fetchone() is None
    assert repo.conn.execute(
        "SELECT name FROM schema_migrations WHERE name = '002_failure'"
    ).fetchone() is None

    monkeypatch.setattr(sqlite, "MIGRATIONS", [
        ("002_failure", "CREATE TABLE example (id INTEGER);"),
    ])
    assert repo.migrate() == ["002_failure"]
