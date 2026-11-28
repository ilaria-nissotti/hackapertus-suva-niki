"""Judgements and rules scores for the review app, read from the suva knowledge base.

The app does no scraping or scoring of its own. `python -m suva fetch` fills the
knowledge base, `parse` splits the text and `score` stores the rules score with its
explanation. This module turns those rows into the records the app shows. A record's
`key` (docket@decision-date) is its identity; `docket` is for display.
"""

from datetime import date
import logging
from pathlib import Path

from suva.parse.run import parse_all
from suva.scoring.rules import load_rules
from suva.scoring.store import inputs_hash, rules_hash, score_keys
from suva.parse.segment import PARSER_VERSION
from suva.sources.quality import usable_text
from suva.sources.base import SourceRecord, decision_id
from suva.storage.sqlite import SQLiteRepository
from suva.windows import Window, challenge_window

STATUS = {"high": "Relevant", "check": "Review", "low": "Low relevance"}
log = logging.getLogger(__name__)


def open_kb(path):
    repo = SQLiteRepository(Path(path))
    if not repo.schema_is_current():
        applied = repo.migrate()
        log.info("kb_migrated path=%s applied=%s", path, ",".join(applied) or "none")
    return repo


def window_for(today=None) -> Window:
    return challenge_window(today or date.today())


def current_rules_hash() -> str:
    return rules_hash(load_rules())


def record(row, rules_digest):
    """One judgement as the app shows it. `full_text` is the stored judgement text."""
    score = row["score"]
    current = (
        score is not None
        and row["rules_hash"] == rules_digest
        and row["parser_version"] == row["score_parser_version"] == PARSER_VERSION
        and row["parse_text_hash"] == row["score_text_hash"]
        and row["inputs_hash"] == inputs_hash(row["language"], row["parsed_at"])
    )
    return {
        "key": row["key"], "docket": row["docket"], "title": row["subject"] or row["docket"],
        "decision_date": row["decision_date"], "publication_date": row["publication_date"],
        "date_source": row["publication_date_source"], "language": row["language"],
        "full_text": row["text"] or "", "source_url": None,
        "score": score, "points": row["points"], "band": row["band"], "fired": row["fired"], "score_current": current,
        "status": ("Unscored" if score is None else STATUS[row["band"]] if current else "Stale"),
    }


def window_records(repo, window: Window, extra_keys=(), rules_digest=None, include_text=False):
    """Judgements in the window (publication date in it or unknown) plus cases added by hand."""
    digest = rules_digest or current_rules_hash()
    rows = repo.list_review_rows(window=window, include_text=include_text)
    seen = {r["key"] for r in rows}
    rows += repo.list_review_rows(keys=[k for k in extra_keys if k not in seen], include_text=include_text)
    return [record(r, digest) for r in rows]


def rank(records):
    """Best first: score, then rule points (they break capped 100 ties); unscored last. Ties keep their order.
    The dashboard queue and `summarize-top` both use this, so "top N" means the same in both."""
    return sorted(records, key=lambda r: (r["score"], r["points"] or 0) if r["score"] is not None else (-1, 0),
                  reverse=True)


def filter_queue(records, query="", status=""):
    """Records whose docket, title or rule explanations contain `query` (casefolded), with that status if given."""
    return [r for r in records
            if query in f"{r['docket']} {r['title']} {' '.join(f['explanation'] for f in r['fired'])}".casefold()
            and (not status or status == r["status"])]


def get_record(repo, key, rules_digest=None):
    rows = repo.list_review_rows(keys=[key], include_text=True)
    if not rows:
        return None
    result = record(rows[0], rules_digest or current_rules_hash())
    result["source_url"] = next((s["url"] for s in repo.judgement_sources(key) if s["url"]), None)
    result["decision"] = next((p["text"] for p in repo.list_passages(key) if p["kind"] == "dispositiv"), None)
    return result


def find_keys(repo, docket, decision_date=None):
    """Keys of the stored decisions of a docket (one key if the decision date is given)."""
    keys = [r["key"] for r in repo.list_judgements(dockets={docket})]
    if decision_date:
        wanted = decision_id(docket, decision_date)
        return [k for k in keys if k == wanted]
    return keys


def add_text(repo, docket, text, decision_date=None, publication_date=None, source_url=None):
    """Store an uploaded judgement text, then parse and score it like any fetched one."""
    if not usable_text(text):
        raise ValueError("Upload a file containing usable judgment text.")
    if decision_date is None:
        raise ValueError("Enter the decision date when uploading judgment text.")
    rec = SourceRecord(source="manual", source_id=decision_id(docket, decision_date), docket=docket,
                       decision_date=decision_date, publication_date=publication_date,
                       url=source_url, text=text)
    rules = load_rules()
    with repo.transaction():
        existing = repo.get_judgement(rec.key)
        if existing and usable_text(existing["text"]) and existing["text"] != text:
            raise ValueError("This judgment already has source text. Add it from the knowledge base.")
        repo.upsert_judgement(rec)
        parse_all(repo, key=rec.key)
        score_keys(repo, [rec.key], rules)
    return rec.key


def case_metadata(repo, keys, rules_digest=None):
    """Decision date, title, topic rules and outcome of judgements, for library facets."""
    from suva.scoring.derive import outcome

    digest = rules_digest or current_rules_hash()
    out = {}
    for row in repo.list_review_rows(keys=list(keys)):
        rec = record(row, digest)
        dispositiv = " ".join(p["text"] for p in repo.list_passages(row["key"]) if p["kind"] == "dispositiv")
        out[row["key"]] = {
            "docket": rec["docket"], "title": rec["title"], "decision_date": rec["decision_date"],
            "publication_date": rec["publication_date"], "status": rec["status"],
            "topics": [{"id": f["id"], "explanation": f["explanation"]} for f in rec["fired"] if f["points"] > 0],
            "outcome": outcome(dispositiv),
            # Laws the judgement itself cites (stored parse), canonical German abbreviation: UVG, ATSG, IVG …
            "laws": sorted({c["law"] for c in repo.list_citations(row["key"]) if c["kind"] == "statute" and c["law"]}),
        }
    return out
