"""Turn points into the reading list: the top N judgements per period, none below a floor.

Lawyers have a reading budget, so the output is "the 30 judgements of this month most worth
reading", not "everything above a cut-off". The floor only shortens a period's list: in a
quiet month the list stops at the floor instead of being filled up with judgements nothing
speaks for.

    selection:                (rules.yaml)
      top_n: 30               judgements shown per period
      floor_rule_points: 66   never show a judgement below this many rule points
      per: month              month | week | period (the whole input as one list)

Everything here works on rule points (the fired rules, without base). base is the same for every
judgement, so leaving it out changes nothing in the order; it is added back only for display
(Selected.display_score = base + rule points).

Ties: more rule points first; at equal points not-relevant first, then by key. The label is known
only in evaluation, where this keeps ties from flattering the numbers; in production records
carry no label and equal points are ordered by key.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from suva.scoring.rules import PERIODS, RuleSet
from suva.scoring.score import ScoreResult, score_record

Scored = tuple[dict[str, Any], ScoreResult]
Verdicts = dict[str, tuple[str, str]]  # key -> (judge level, reason)


@dataclass
class Selected:
    rank: int  # within its period, 1 = most rule points
    rule_points: float
    period: str
    record: dict[str, Any]
    result: ScoreResult

    @property
    def key(self) -> str:
        return self.record.get("key", "")

    @property
    def display_score(self) -> float:
        """base + rule points, for lawyers; not used for ranking or selection."""
        return self.result.display_score


def period_of(record: dict[str, Any], per: str) -> str:
    """Period label from the decision date: 2025-10 (month), 2025-W41 (ISO week) or 'all'."""
    if per not in PERIODS:
        raise ValueError(f"per must be one of {', '.join(PERIODS)}; got {per!r}")
    if per == "period":
        return "all"
    raw = record.get("decision_date")
    if not raw:
        return "unknown"
    d = date.fromisoformat(str(raw)[:10])
    if per == "month":
        return f"{d.year}-{d.month:02d}"
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def tie_order(item: Scored) -> tuple:
    rec, result = item
    return (-result.rule_points, bool(rec.get("relevant", False)), str(rec.get("key", "")))


def ranked(scored: Iterable[Scored]) -> list[Scored]:
    """Most rule points first, with the tie order described in the module docstring."""
    return sorted(scored, key=tie_order)


def select_scored(scored: Iterable[Scored], n: int | None, floor_rule_points: float | None,
                  per: str = "period") -> list[Selected]:
    """Top n per period (None: no limit), dropping anything below floor_rule_points (None: no floor)."""
    groups: dict[str, list[Scored]] = {}
    for item in scored:
        groups.setdefault(period_of(item[0], per), []).append(item)
    out = []
    for period in sorted(groups):
        rank = 0
        for rec, result in ranked(groups[period]):
            if (n is not None and rank >= n) or (floor_rule_points is not None
                                                 and result.rule_points < floor_rule_points):
                break  # sorted by rule points, so nothing further down passes the floor either
            rank += 1
            out.append(Selected(rank, result.rule_points, period, rec, result))
    return out


def select_top(records: Iterable[dict[str, Any]], ruleset: RuleSet, n: int | None,
               floor_rule_points: float | None, per: str = "period",
               verdicts: Verdicts | None = None) -> list[Selected]:
    """Score the records, then return the top n per period with at least floor_rule_points, with rank and points."""
    verdicts = verdicts or {}
    scored = [(rec, score_record(rec, ruleset, *verdicts.get(rec.get("key", ""), (None, "")))) for rec in records]
    return select_scored(scored, n, floor_rule_points, per)


def select(records: Iterable[dict[str, Any]], ruleset: RuleSet, verdicts: Verdicts | None = None) -> list[Selected]:
    """The selection configured in rules.yaml (selection: top_n, floor_rule_points, per)."""
    sel = ruleset.selection
    return select_top(records, ruleset, sel.top_n, sel.floor_rule_points, sel.per, verdicts)
