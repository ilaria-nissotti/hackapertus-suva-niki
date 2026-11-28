"""The review app's own database (`DATA_DIR/review.sqlite`): drafts, approvals, briefings, added cases,
relevance verdicts and rule-weight proposals.

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
    CREATE TABLE IF NOT EXISTS triage_feedback (
        case_key TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
        rule_points REAL, saved_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS weight_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
        status TEXT NOT NULL, payload TEXT, error TEXT, decided_at TEXT
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS library USING fts5(
        case_key UNINDEXED, lang UNINDEXED, approved_at UNINDEXED, summary,
        tokenize = 'unicode61 remove_diacritics 2'
    );
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    backfill_library(db)
    return db


def backfill_library(db):
    """Index approvals saved before the library existed (once: only while the index is empty)."""
    if db.execute("SELECT 1 FROM library LIMIT 1").fetchone():
        return
    rows = db.execute("""SELECT case_key, kind, approved FROM results r WHERE approved IS NOT NULL
                         AND kind IN ('de', 'fr') AND rowid = (SELECT max(rowid) FROM results
                         WHERE case_key = r.case_key AND kind = r.kind AND approved IS NOT NULL)""").fetchall()
    if rows:
        with db:
            db.executemany("INSERT INTO library (case_key, lang, approved_at, summary) VALUES (?, ?, '', ?)",
                           [tuple(row) for row in rows])


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
        if saved.rowcount == 1:  # the library holds the latest approval per case and language
            db.execute("DELETE FROM library WHERE case_key=? AND lang=?", (key, kind))
            db.execute("INSERT INTO library (case_key, lang, approved_at, summary) VALUES (?, ?, datetime('now'), ?)",
                       (key, kind, summary))
    return saved.rowcount == 1


# Reviewer triage: agreement or disagreement with the rules score, kept apart from the score itself.

def save_triage_feedback(db, key, status, reason, rule_points):
    with db:
        db.execute("""INSERT INTO triage_feedback (case_key, status, reason, rule_points) VALUES (?, ?, ?, ?)
            ON CONFLICT(case_key) DO UPDATE SET status=excluded.status, reason=excluded.reason,
            rule_points=excluded.rule_points, saved_at=datetime('now')""", (key, status, reason, rule_points))


def triage_feedback(db, key=None):
    if key is not None:
        row = db.execute("SELECT * FROM triage_feedback WHERE case_key=?", (key,)).fetchone()
        return dict(row) if row else None
    return [dict(row) for row in db.execute("SELECT * FROM triage_feedback ORDER BY saved_at, case_key")]


def verdict_counts(db, statuses, since=None):
    """Verdicts with one of `statuses`: (total, per status, saved after `since`)."""
    marks = ", ".join("?" * len(statuses))
    per_status = dict(db.execute(f"SELECT status, COUNT(*) FROM triage_feedback WHERE status IN ({marks})"
                                 " GROUP BY status", statuses).fetchall())
    new = db.execute(f"SELECT COUNT(*) FROM triage_feedback WHERE status IN ({marks}) AND saved_at > ?",
                     (*statuses, since or "")).fetchone()[0]
    return sum(per_status.values()), per_status, new


# Rule-weight proposals from the verdicts: computing -> pending (ready for review) -> accepted or discarded.
# A failed calculation is stored as failed with its error. Accepted rows are the history of weight updates.

def start_proposal(db):
    """A new calculation; older proposals still waiting for review are superseded by it."""
    with db:
        db.execute("UPDATE weight_proposals SET status='superseded', decided_at=datetime('now')"
                   " WHERE status IN ('computing', 'pending', 'failed')")
        return db.execute("INSERT INTO weight_proposals (status) VALUES ('computing')").lastrowid


def finish_proposal(db, proposal_id, payload=None, error=None):
    with db:
        db.execute("UPDATE weight_proposals SET status=?, payload=?, error=? WHERE id=? AND status='computing'",
                   ("failed" if error else "pending", payload, error, proposal_id))


def decide_proposal(db, proposal_id, status):
    """Mark a pending proposal accepted or discarded (atomic: only one decision wins). True if decided."""
    with db:
        return db.execute("UPDATE weight_proposals SET status=?, decided_at=datetime('now')"
                          " WHERE id=? AND status='pending'", (status, proposal_id)).rowcount == 1


def get_proposal(db, proposal_id):
    row = db.execute("SELECT * FROM weight_proposals WHERE id=?", (proposal_id,)).fetchone()
    return dict(row) if row else None


def open_proposal(db):
    """The latest proposal that is computing, waiting for review or failed (None if there is none)."""
    row = db.execute("SELECT * FROM weight_proposals WHERE status IN ('computing', 'pending', 'failed')"
                     " ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def accepted_proposals(db, limit=20):
    """Accepted weight updates, newest first."""
    return [dict(row) for row in db.execute("SELECT * FROM weight_proposals WHERE status='accepted'"
                                            " ORDER BY id DESC LIMIT ?", (limit,))]


def interrupt_computing(db):
    """At startup: a calculation still marked computing died with the previous server process."""
    with db:
        db.execute("UPDATE weight_proposals SET status='failed', error=? WHERE status='computing'",
                   ("The calculation was interrupted. Calculate again.",))


# Library: full-text search over approved summaries.

def fts_query(text, any_word=False):
    """User words as an FTS5 query (quoted, so operators are plain text): all words, or any with `any_word`."""
    words = [w.replace('"', '') for w in text.split()]
    return (" OR " if any_word else " ").join(f'"{w}"' for w in words if w)


def search_library(db, query="", lang=None, limit=200, any_word=False):
    """Approved summaries matching every word of `query` (all if empty), best match first."""
    sql = "SELECT case_key, lang, approved_at, summary, {snippet} AS snippet FROM library"
    args, where = [], []
    match = fts_query(query, any_word)
    if match:
        where.append("library MATCH ?")
        args.append(match)
    if lang:
        where.append("lang = ?")
        args.append(lang)
    snippet = "snippet(library, 3, '⟦', '⟧', '…', 24)" if match else "substr(summary, 1, 240)"
    sql = sql.format(snippet=snippet) + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY rank" if match else " ORDER BY approved_at DESC"
    return db.execute(sql + " LIMIT ?", (*args, limit)).fetchall()


# Cases a reviewer added by hand.

def add_case(db, key):
    with db:
        db.execute("INSERT OR IGNORE INTO review_cases (case_key) VALUES (?)", (key,))


def added_cases(db):
    return [row[0] for row in db.execute("SELECT case_key FROM review_cases")]


# Audio briefings, keyed by a hash of the approved summary and the generation settings.

def get_briefing(db, cache_key):
    return db.execute("SELECT script, mp3 FROM briefings WHERE cache_key=?", (cache_key,)).fetchone()


def save_briefing_script(db, cache_key, script, evidence):
    with db:
        db.execute("""INSERT INTO briefings (cache_key, script, evidence)
            VALUES (?, ?, ?) ON CONFLICT(cache_key) DO UPDATE
            SET script=excluded.script, evidence=excluded.evidence, error=NULL""", (cache_key, script, evidence))


def save_briefing_audio(db, cache_key, mp3):
    with db:
        db.execute("UPDATE briefings SET mp3=?, error=NULL WHERE cache_key=?", (mp3, cache_key))


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
