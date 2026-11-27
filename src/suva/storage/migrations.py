"""Numbered, append-only schema migrations. Never edit an applied one; add a new one.

Each pipeline step adds its own tables (judgements, passages + FTS5, triage,
summaries, feedback) when its design is decided.
"""

MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_audit_log",
        """
        CREATE TABLE audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            kind       TEXT NOT NULL,
            actor      TEXT NOT NULL,
            payload    TEXT NOT NULL
        );
        CREATE INDEX audit_log_kind ON audit_log (kind, id);
        CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
        CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
        """,
    ),
]
