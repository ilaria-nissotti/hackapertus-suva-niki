"""Score the judgements of a window with rules.yaml and store the result in the KB.

The stored score is what the review app shows: the display score (base + rule points), the rule
points, the fired rules ("why selected / why not") and a band from the reading list (select.py):

    high   in the reading list: the top N of its period, at or above the rule-points floor
    check  at or above the floor, but not in the top N
    low    below the floor

It stays current only while the rules file (weights and selection), the parse and the judgement
text are unchanged; the review app checks this (review_kb.record).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from suva.hashing import sha256
from suva.scoring.dataset import features_record
from suva.scoring.derive import with_derived_fields
from suva.scoring.rules import RuleSet
from suva.scoring.score import ScoreResult, score_record
from suva.scoring.select import select_scored
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


# Bump when the scorer computes different points from the same rules, so stored scores show as stale.
# 2: derived fields (appellant, outcome, reasoning_pages) are added before scoring.
SCORER_VERSION = 2


def rules_hash(ruleset: RuleSet) -> str:
    """Hash of everything that changes a stored score or band (comments and evidence notes in rules.yaml don't)."""
    selection = ruleset.selection
    return sha256(repr(([(r.id, r.field, r.weight, r.any, r.all, r.regex, r.none, r.min, r.case_sensitive,
                          r.per_match, r.max, r.explanation) for r in ruleset.rules],
                        ruleset.base, (selection.top_n, selection.floor_rule_points, selection.per),
                        SCORER_VERSION)))


def inputs_hash(language, parsed_at):
    return sha256([language, parsed_at])


def text_hash(text: str | None) -> str | None:
    return sha256(text) if text else None


def window_judgements(repo: Repository, window: Window) -> list[dict[str, Any]]:
    """Use publication dates in the challenge window when available."""
    return repo.list_judgements(window=window)


def score_window(repo: Repository, window: Window, ruleset: RuleSet) -> ScoreRunReport:
    return _score_rows(repo, window_judgements(repo, window), ruleset, window)


def score_keys(repo: Repository, keys: list[str], ruleset: RuleSet) -> ScoreRunReport:
    """Score single judgements (e.g. one a reviewer added by hand), whatever their dates.

    A top N needs the whole period, so these are judged against the rule-points floor only:
    at or above it they count as in the reading list (high), below it as low."""
    wanted = set(keys)
    rows = [row for key in sorted(wanted) if (row := repo.get_judgement(key)) is not None]
    return _score_rows(repo, rows, ruleset, None)


def reading_list_bands(scored: list[tuple[dict[str, Any], ScoreResult]], ruleset: RuleSet,
                       whole_period: bool) -> dict[str, str]:
    """high / check / low per key, from the reading list (see the module docstring)."""
    floor = ruleset.selection.floor_rule_points
    top_n = ruleset.selection.top_n if whole_period else None
    listed = {item.record["key"] for item in select_scored(scored, top_n, floor, ruleset.selection.per)}
    return {rec["key"]: "high" if rec["key"] in listed
            else "low" if floor is not None and result.rule_points < floor else "check"
            for rec, result in scored}


def _score_rows(repo: Repository, judgements: list[dict[str, Any]], ruleset: RuleSet,
                window: Window | None) -> ScoreRunReport:
    rep = ScoreRunReport(str(window) if window else "selected judgements",
                         str(ruleset.path or "<inline>"), rules_hash(ruleset))
    # The significance rules look at appellant, outcome and reasoning_pages, derived like in the dataset.
    records = [with_derived_fields(features_record(repo, row)) for row in judgements]
    scored = [(record, score_record(record, ruleset)) for record in records]
    band_of = reading_list_bands(scored, ruleset, whole_period=window is not None)
    bands, rows = Counter(), []
    for row, (record, result) in zip(judgements, scored):
        band = band_of[row["key"]]
        rows.append({
            "key": row["key"], "score": result.display_score, "points": result.rule_points, "band": band,
            "fired": [{"id": f.id, "points": f.points, "explanation": f.explanation} for f in result.fired],
            "rules_hash": rep.rules_hash, "parser_version": None if record["parse_stale"] else record["parser_version"],
            "inputs_hash": inputs_hash(record["language"], record["parsed_at"]),
            "text_hash": text_hash(record["text"]),
        })
        bands[band] += 1
        rep.not_parsed += record["parse_status"] is None
        rep.stale_parses += record["parse_stale"]
        rep.unknown_publication += bool(window and window.published_from) and not row["publication_date"]
    repo.save_scores(rows)
    rep.scored, rep.by_band = len(rows), dict(bands)
    return rep
