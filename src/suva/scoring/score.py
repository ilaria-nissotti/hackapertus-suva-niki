"""Deterministic scorer: apply rules.yaml to a judgement record. No language model involved.

    points = base + points of every rule that fires (+ judge points, if a verdict is given)
    score  = 100 / (1 + e^(-points / scale))        # 0..100, S-shaped, never capped

Fitted on the data (`fit-weights`), the score approximates the probability (in %)
that Suva would consider the judgement relevant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from suva.scoring.rules import RuleSet

BANDS = ("high", "check", "low")


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
    score: float  # 0..100
    points: float
    band: str  # high | check | low
    relevant: bool  # score >= threshold
    fired: list[FiredRule] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        if not self.fired:
            return "No rule matched."
        return " | ".join(f"{r.points:+g} {r.explanation}" for r in self.fired)


def to_score(points: float, scale: float) -> float:
    """Unrounded on purpose, so very low or very high scores still sort; round only for display."""
    z = max(-60.0, min(60.0, points / scale))
    return 100.0 / (1.0 + math.exp(-z))


def band_for(score: float, ruleset: RuleSet) -> str:
    if score >= ruleset.bands["high"]:
        return "high"
    return "low" if score < ruleset.bands["low"] else "check"


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
    points = ruleset.base + sum(f.points for f in fired)
    score = to_score(points, ruleset.scale)
    return ScoreResult(record.get("key", ""), score, points, band_for(score, ruleset),
                       score >= ruleset.threshold, fired)


def rule_features(records: list[dict[str, Any]], ruleset: RuleSet) -> list[list[float]]:
    """records x rules: 1/0 for normal rules, capped match counts for graded rules."""
    return [[r.feature(rec) for r in ruleset.rules] for rec in records]
