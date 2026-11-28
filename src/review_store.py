"""The review app's own database (`DATA_DIR/review.sqlite`): drafts, approvals, briefings and added cases.

Every query on these tables lives here. Judgements, parses and scores stay in the knowledge base (`review_kb.py`).
"""

from pathlib import Path
import sqlite3

SCHEMA = """
    CREATE TABLE IF NOT EXISTS results (
        case_key TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
        payload TEXT, error TEXT, approved TEXT,
        PRIMARY KEY (case_key, kind, version)
    );
    CREATE TABLE IF NOT EXISTS briefings (
        cache_key TEXT PRIMARY KEY, script TEXT, evidence TEXT, mp3 BLOB, error TEXT
    );
    CREATE TABLE IF NOT EXISTS review_cases (
        case_key TEXT PRIMARY KEY, added_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


# Summary results: one row per case, kind (facts, de, fr) and generation version.

def get_result(db, key, kind, version):
    return db.execute("SELECT * FROM results WHERE case_key=? AND kind=? AND version=?",
                      (key, kind, version)).fetchone()


def save_result(db, key, kind, version, payload, error):
    """Store a generation outcome. A row that already holds a draft is never overwritten."""
    with db:
        db.execute("""INSERT INTO results (case_key, kind, version, payload, error)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(case_key,kind,version)
            DO UPDATE SET payload=excluded.payload, error=excluded.error
            WHERE results.payload IS NULL""", (key, kind, version, payload, error))


def save_result_error(db, key, kind, version, error):
    """Record a failure where the dashboard looks, unless a draft already exists."""
    with db:
        db.execute("""INSERT INTO results (case_key, kind, version, payload, error)
            VALUES (?, ?, ?, NULL, ?) ON CONFLICT(case_key,kind,version)
            DO UPDATE SET error=excluded.error WHERE results.payload IS NULL""", (key, kind, version, error))


def approve(db, key, kind, version, summary, previous_approved):
    """Save the approval only if it still is the one the reviewer saw (atomic check). True if saved."""
    with db:
        saved = db.execute("""UPDATE results SET approved=? WHERE case_key=? AND kind=? AND version=?
                              AND COALESCE(approved, '') = ?""", (summary, key, kind, version, previous_approved))
    return saved.rowcount == 1


# Cases a reviewer added by hand.

def add_case(db, key):
    with db:
        db.execute("INSERT OR IGNORE INTO review_cases (case_key) VALUES (?)", (key,))


def added_cases(db):
    return [row[0] for row in db.execute("SELECT case_key FROM review_cases")]


# Audio briefings, keyed by a hash of the approved summary and the generation settings.

def briefing_status(db, cache_key):
    return db.execute("""SELECT script, evidence, mp3 IS NOT NULL AS ready, error
                         FROM briefings WHERE cache_key=?""", (cache_key,)).fetchone()


def briefing_audio(db, cache_key):
    row = db.execute("SELECT mp3 FROM briefings WHERE cache_key=?", (cache_key,)).fetchone()
    return row["mp3"] if row else None


def briefing_error(db, cache_key):
    row = db.execute("SELECT error FROM briefings WHERE cache_key=?", (cache_key,)).fetchone()
    return row["error"] if row else None


def save_briefing_error(db, cache_key, error):
    with db:
        db.execute("""INSERT INTO briefings (cache_key, error) VALUES (?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET error=excluded.error""", (cache_key, error))
