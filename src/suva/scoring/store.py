"""Score the judgements of a window with rules.yaml and store the result in the KB.

The stored score is what the review app shows: the 0-100 score, its band and the
fired rules ("why selected / why not"). It stays current only while the rules file,
the parse and the judgement text are unchanged; `is_current` checks all three.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from suva.hashing import sha256
from suva.parse.segment import PARSER_VERSION
from suva.scoring.dataset import features_record
from suva.scoring.rules import RuleSet
from suva.scoring.score import score_record
from suva.storage.base import Repository
from suva.windows import Window


@dataclass
class ScoreRunReport:
    window: str
    rules: str
    rules_hash: str
    scored: int = 0
    by_band: dict = field(default_factory=dict)
    not_parsed: int = 0  # scored on metadata only: run `parse` first
    stale_parses: int = 0
    unknown_publication: int = 0  # in the window by decision date, publication date unknown

    def as_dict(self) -> dict:
        return asdict(self)


def rules_hash(ruleset: RuleSet) -> str:
    """Hash of everything that changes a score (comments and evidence notes in rules.yaml don't)."""
    return sha256(repr(([(r.id, r.field, r.weight, r.any, r.all, r.regex, r.case_sensitive, r.per_match, r.max, r.explanation)
                         for r in ruleset.rules], ruleset.base, ruleset.scale, ruleset.threshold, ruleset.bands)))


def inputs_hash(language, parsed_at):
    return sha256([language, parsed_at])


def text_hash(text: str | None) -> str | None:
    return sha256(text) if text else None


def window_judgements(repo: Repository, window: Window) -> list[dict[str, Any]]:
    """Judgements decided in the window whose publication date is in it or unknown."""
    rows = repo.list_judgements(start=window.start.isoformat(), end=window.end.isoformat())
    if window.published_from is None:
        return rows
    return [r for r in rows if window.includes_publication(
        date.fromisoformat(r["publication_date"]) if r["publication_date"] else None)]


def score_window(repo: Repository, window: Window, ruleset: RuleSet) -> ScoreRunReport:
    return _score_rows(repo, window_judgements(repo, window), ruleset, window)


def score_keys(repo: Repository, keys: list[str], ruleset: RuleSet) -> ScoreRunReport:
    """Score single judgements (e.g. one a reviewer added by hand), whatever their dates."""
    wanted = set(keys)
    rows = [row for key in sorted(wanted) if (row := repo.get_judgement(key)) is not None]
    return _score_rows(repo, rows, ruleset, None)


def _score_rows(repo: Repository, judgements: list[dict[str, Any]], ruleset: RuleSet,
                window: Window | None) -> ScoreRunReport:
    rep = ScoreRunReport(str(window) if window else "selected judgements",
                         str(ruleset.path or "<inline>"), rules_hash(ruleset))
    bands, rows = Counter(), []
    for row in judgements:
        record = features_record(repo, row)
        result = score_record(record, ruleset)
        rows.append({
            "key": row["key"], "score": result.score, "points": result.points, "band": result.band,
            "fired": [{"id": f.id, "points": f.points, "explanation": f.explanation} for f in result.fired],
            "rules_hash": rep.rules_hash, "parser_version": None if record["parse_stale"] else record["parser_version"],
            "inputs_hash": inputs_hash(record["language"], record["parsed_at"]),
            "text_hash": text_hash(record["text"]),
        })
        bands[result.band] += 1
        rep.not_parsed += record["parse_status"] is None
        rep.stale_parses += record["parse_stale"]
        rep.unknown_publication += bool(window and window.published_from) and not row["publication_date"]
    repo.save_scores(rows)
    rep.scored, rep.by_band = len(rows), dict(bands)
    return rep


def is_current(score: dict[str, Any] | None, text: str | None, current_rules_hash: str) -> bool:
    return bool(score) and score["rules_hash"] == current_rules_hash \
        and score["parser_version"] == PARSER_VERSION and score["text_hash"] == text_hash(text)
