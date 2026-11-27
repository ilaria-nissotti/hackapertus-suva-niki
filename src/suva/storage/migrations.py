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
    (
        "005_passages_and_citations",
        """
        -- Result of splitting one judgement's text (step 4). Re-run replaces it.
        CREATE TABLE parses (
            key            TEXT PRIMARY KEY REFERENCES judgements (key),
            status         TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'failed')),
            issues         TEXT NOT NULL DEFAULT '[]',
            parser_version TEXT NOT NULL,
            text_hash      TEXT,              -- sha256 of the text that was parsed
            chamber        TEXT,              -- from the decision's own header
            parties        TEXT,
            subject        TEXT,
            passages       INTEGER NOT NULL,
            citations      INTEGER NOT NULL,
            parsed_at      TEXT NOT NULL
        );
        CREATE TABLE passages (
            pid     TEXT PRIMARY KEY,         -- '8C_129/2025@2025-11-11#E.3.2'
            key     TEXT NOT NULL REFERENCES judgements (key),
            ord     INTEGER NOT NULL,         -- order within the decision
            kind    TEXT NOT NULL,            -- header | facts | reasons | consideration | dispositiv | full
            number  TEXT,                     -- '3.2' for considerations
            text    TEXT NOT NULL,
            start   INTEGER NOT NULL,         -- char offsets in judgements.text
            "end"   INTEGER NOT NULL
        );
        CREATE INDEX passages_key ON passages (key, ord);
        CREATE TABLE citations (
            pid           TEXT NOT NULL REFERENCES passages (pid),
            kind          TEXT NOT NULL,      -- statute | bge | judgement
            canonical     TEXT NOT NULL,      -- 'Art. 6 Abs. 2 UVG' | 'BGE 134 V 109' | '8C_1/2025'
            raw           TEXT NOT NULL,
            law           TEXT,
            article       TEXT,
            consideration TEXT,
            start         INTEGER NOT NULL,   -- char offsets within the passage text
            "end"         INTEGER NOT NULL
        );
        CREATE INDEX citations_pid ON citations (pid);
        CREATE INDEX citations_canonical ON citations (canonical);
        CREATE INDEX citations_law ON citations (law);
        """,
    ),
    (
        "006_relevance_scores",
        """
        -- Deterministic rules score per judgement (`suva score`). Re-run replaces it.
        -- A score is current only while rules_hash, parser_version and text_hash still match.
        CREATE TABLE relevance_scores (
            key            TEXT PRIMARY KEY REFERENCES judgements (key),
            score          REAL NOT NULL,     -- 0..100
            points         REAL NOT NULL,
            band           TEXT NOT NULL CHECK (band IN ('high', 'check', 'low')),
            fired          TEXT NOT NULL,     -- JSON [{id, points, explanation}], strongest first
            rules_hash     TEXT NOT NULL,     -- sha256 of the rules file
            parser_version TEXT,              -- of the parse the features came from (NULL = unparsed)
            text_hash      TEXT,              -- sha256 of the judgement text that was scored
            scored_at      TEXT NOT NULL
        );
        CREATE INDEX relevance_scores_score ON relevance_scores (score);
        """,
    ),
]
