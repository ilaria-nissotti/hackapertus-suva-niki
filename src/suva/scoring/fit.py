"""Suggest rule weights and base points from the data with a small logistic regression.

Each rule is a feature (1/0, or a capped match count for graded rules). The regression learns
how much each rule counts towards "relevant"; each coefficient times POINTS_PER_LOGIT, rounded, becomes the
rule's points. That factor only sets the size of the numbers: judgements are ranked by
points, so it does not change the order (beyond rounding).

It also suggests selection.floor_rule_points: the highest rule-points value (without base) that
still keeps FLOOR_RECALL of the relevant training judgements. Rule points depend on the weights,
so a floor belongs to one set of weights and has to be re-derived when they change. The fitted
base only matters for the display score.

The result is written to a SEPARATE file (rules.fitted.yaml): experts decide what to copy
into rules.yaml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from suva.scoring.rules import RuleSet
from suva.scoring.score import features, score_record

POINTS_PER_LOGIT = 10  # coefficient (log-odds) x 10 = points; keeps the points in whole numbers
FLOOR_RECALL = 0.9  # the suggested floor keeps at least this share of the relevant training judgements


@dataclass
class FittedWeights:
    weights: dict[str, int]  # rule id -> points (per match for graded rules)
    current: dict[str, float]
    base: int
    floor_rule_points: float  # highest rule-points floor (no base) that keeps FLOOR_RECALL on the training data
    n: int
    n_rel: int
    graded: set[str] = field(default_factory=set)


def logistic_regression(x: np.ndarray, y: np.ndarray, l2: float = 1.0, steps: int = 5000,
                        lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Class-balanced, L2-regularised logistic regression by gradient descent (no sklearn needed).

    Balancing lets the few relevant judgements shape the weights; the intercept is then
    corrected back to the real share of relevant judgements (this only shifts `base`).
    """
    n, k = x.shape
    pos = y.sum()
    sample_w = np.where(y == 1, n / (2 * max(pos, 1)), n / (2 * max(n - pos, 1)))
    w, b = np.zeros(k), 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(x @ w + b)))
        g = (p - y) * sample_w
        w -= lr * (x.T @ g / n + l2 * w / n)
        b -= lr * g.mean()
    return w, b + math.log(max(pos, 1) / max(n - pos, 1))


def recall_floor(points: list[float], labels: list[bool], target: float = FLOOR_RECALL) -> float:
    """Highest whole-number floor at which at least `target` of the relevant judgements have >= floor rule points."""
    rel = sorted((p for p, y in zip(points, labels) if y), reverse=True)
    if not rel:
        raise ValueError("need relevant judgements to choose a floor")
    keep = max(1, math.ceil(target * len(rel)))
    return float(math.floor(rel[keep - 1]))


def fit_weights(records: list[dict[str, Any]], ruleset: RuleSet, l2: float = 1.0) -> FittedWeights:
    ids = ruleset.feature_ids
    x = np.array(features(records, ruleset), dtype=float).reshape(len(records), len(ids))
    y = np.array([1.0 if r["relevant"] else 0.0 for r in records])
    if not len(records) or y.sum() == 0 or y.sum() == len(y):
        raise ValueError("need both relevant and not relevant records to fit weights")
    w, b = logistic_regression(x, y, l2=l2)
    weights = {feature_id: int(round(c * POINTS_PER_LOGIT)) for feature_id, c in zip(ids, w)}
    base = int(round(b * POINTS_PER_LOGIT))
    # the floor is chosen on the rule points the rounded weights actually give (graded caps included, no base)
    weighted = apply_weights(ruleset, weights, base)  # once, not per record: it recompiles every rule's patterns
    points = [score_record(rec, weighted).rule_points for rec in records]
    return FittedWeights(
        weights=weights,
        current={r.id: (r.per_match if r.graded else r.weight) for r in ruleset.rules},
        base=base, floor_rule_points=recall_floor(points, [bool(v) for v in y]),
        n=len(records), n_rel=int(y.sum()), graded={r.id for r in ruleset.rules if r.graded},
    )


def apply_weights(ruleset: RuleSet, weights: dict[str, float], base: float) -> RuleSet:
    """A copy of the rule set with these weights and base (graded caps kept in pages/matches)."""
    rules = []
    for rule in ruleset.rules:
        new = weights[rule.id]
        if rule.graded:
            cap = rule.cap_count
            rules.append(replace(rule, per_match=new, max=None if cap == float("inf") else new * cap))
        else:
            rules.append(replace(rule, weight=new))
    return replace(ruleset, rules=rules, base=base)


def apply_fitted(ruleset: RuleSet, fitted: FittedWeights) -> RuleSet:
    """A copy of the rule set that scores with the fitted weights and base, and selects with the suggested floor."""
    out = apply_weights(ruleset, fitted.weights, fitted.base)
    return replace(out, selection=replace(ruleset.selection, floor_rule_points=fitted.floor_rule_points))


def write_fitted(ruleset: RuleSet, fitted: FittedWeights, path: Path) -> None:
    """Copy of rules.yaml with suggested weights and floor; comments are not preserved."""
    with ruleset.path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for raw in data["rules"]:
        rule = ruleset.by_id(str(raw["id"]))
        new = fitted.weights[rule.id]
        if rule.graded:
            raw["per_match"] = new
            if rule.max is not None and rule.cap_count != float("inf"):
                raw["max"] = int(round(new * rule.cap_count))
        else:
            raw["weight"] = new
    data["base"] = fitted.base
    data["selection"] = dict(data.get("selection") or {}) | {"floor_rule_points": fitted.floor_rule_points}
    header = (f"# Suggested weights fitted on {fitted.n} training judgements ({fitted.n_rel} relevant).\n"
              f"# selection.floor_rule_points keeps {FLOOR_RECALL:.0%} of the relevant training judgements; "
              "top_n and per are copied from rules.yaml.\n"
              "# The floor belongs to these weights: points change when weights change.\n"
              "# Review before copying into rules.yaml. Comments from rules.yaml are not preserved.\n")
    path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
