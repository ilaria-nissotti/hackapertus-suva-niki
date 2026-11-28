"""Deterministic scorer: apply rules.yaml to a judgement record. No language model involved.

    rule points   = points of every rule that fires
                  + judge points (if a verdict is given)
    display score = base + rule points          (only in what lawyers see)

Rule points are what judgements are ranked, selected (select.py) and sent to the judge by.
They are not a probability. `base` is the same for every judgement, so it is kept out of
ranking, selection and the judge gate, and added only for display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from suva.scoring.rules import RuleSet


@dataclass
class FiredRule:
    id: str
    points: float
    explanation: str

    @property
    def weight(self) -> float:  # backwards-compatible name
        return self.points


@dataclass
class ScoreResult:
    key: str
    rule_points: float  # sum of the fired rules (no base): ranking, selection and the judge gate use this
    fired: list[FiredRule] = field(default_factory=list)
    base: float = 0.0  # ruleset.base, kept only to compute the display score

    @property
    def display_score(self) -> float:
        """base + rule points: the number shown to lawyers. Never used to rank or select."""
        return self.base + self.rule_points

    @property
    def explanation(self) -> str:
        if not self.fired:
            return "No rule matched."
        return " | ".join(f"{r.points:+g} {r.explanation}" for r in self.fired)


def score_record(
    record: dict[str, Any], ruleset: RuleSet, judge: str | None = None, judge_reason: str = "",
) -> ScoreResult:
    fired = []
    for rule in ruleset.rules:
        count = rule.count(record)
        if count:
            text = rule.explanation + (f" ({count}×)" if rule.graded and count > 1 else "")
            fired.append(FiredRule(rule.id, rule.points_for(count), text))
    if judge and judge in ruleset.judge_points:
        text = f"Apertus judge: {judge}" + (f" ({judge_reason})" if judge_reason else "")
        fired.append(FiredRule("llm_judge", ruleset.judge_points[judge], text))
    fired.sort(key=lambda f: -abs(f.points))
    return ScoreResult(record.get("key", ""), sum(f.points for f in fired), fired, ruleset.base)


def features(records: list[dict[str, Any]], ruleset: RuleSet) -> list[list[float]]:
    """records x ruleset.feature_ids: 1/0 for normal rules, capped match counts for graded rules."""
    return [[r.feature(rec) for r in ruleset.rules] for rec in records]
