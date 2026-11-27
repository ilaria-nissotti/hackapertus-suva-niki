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
    (
        "002_examples_and_known_positives",
        """
        -- Suva's human summaries (judgements/Export_*.html). split is frozen once set.
        CREATE TABLE example_summaries (
            docket        TEXT PRIMARY KEY,
            decision_date TEXT,
            title         TEXT NOT NULL,
            language      TEXT,
            text_de       TEXT NOT NULL,
            text_fr       TEXT NOT NULL,
            author        TEXT,
            source_url    TEXT,
            bger_url      TEXT,
            aza_docid     TEXT,
            source_file   TEXT NOT NULL,
            notes         TEXT NOT NULL DEFAULT '[]',
            split         TEXT CHECK (split IN ('fewshot', 'eval')),
            imported_at   TEXT NOT NULL
        );
        -- Every judgement Suva chose to summarise (xlsx ∪ exports): the label set.
        CREATE TABLE known_positives (
            ref                TEXT PRIMARY KEY,   -- docket, or raw title for non-BGer rows
            docket             TEXT,
            court              TEXT NOT NULL CHECK (court IN ('bger', 'cantonal', 'unresolved')),
            in_xlsx            INTEGER NOT NULL,
            in_exports         INTEGER NOT NULL,
            quarter            TEXT,
            summary_created_at TEXT,
            notes              TEXT NOT NULL DEFAULT '[]',
            imported_at        TEXT NOT NULL
        );
        """,
    ),
    (
        "003_decision_identity_and_split_manifest",
        """
        ALTER TABLE example_summaries RENAME COLUMN docket TO decision_id;
        ALTER TABLE example_summaries ADD COLUMN docket TEXT;
        UPDATE example_summaries SET docket = decision_id;
        UPDATE example_summaries SET decision_id = docket || '@' || decision_date WHERE decision_date IS NOT NULL;
        CREATE INDEX example_summaries_docket ON example_summaries (docket);
        CREATE TABLE example_split_manifest (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload TEXT NOT NULL
        );
        """,
    ),
    (
        "004_judgements_and_sources",
        """
        -- One row per decision (docket + decision date). Text is plain full text.
        CREATE TABLE judgements (
            key                     TEXT PRIMARY KEY,   -- '8C_129/2025@2025-11-11'
            docket                  TEXT NOT NULL,
            decision_date           TEXT,
            chamber_prefix          TEXT,               -- '8C' (a feature, never a gate)
            language                TEXT,
            publication_date        TEXT,               -- NULL = unknown
            publication_date_source TEXT,
            text                    TEXT,
            text_source             TEXT,
            html_path               TEXT,               -- cached structured HTML, if fetched
            first_seen_at           TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        );
        CREATE INDEX judgements_date ON judgements (decision_date);
        CREATE INDEX judgements_docket ON judgements (docket);
        -- Coverage ledger: every source that listed a decision.
        CREATE TABLE judgement_sources (
            key          TEXT NOT NULL REFERENCES judgements (key),
            source       TEXT NOT NULL,
            source_id    TEXT NOT NULL,
            chamber_raw  TEXT,
            url          TEXT,
            html_url     TEXT,
            source_seen_at TEXT,                        -- the source's own scrape time
            retrieved_at TEXT NOT NULL,                 -- when we saw it there
            PRIMARY KEY (key, source)
        );
        """,
    ),
]
