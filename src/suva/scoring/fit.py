"""Suggest rule weights and base points from the data with a small logistic regression.

Each rule is a feature (1/0, or a capped match count for graded rules). The regression
learns how much each rule counts towards "relevant"; coefficients become points on the
same scale the scorer uses (`scale` points = 1 unit of log-odds), so the fitted score
approximates the probability of relevance. The result is written to a SEPARATE file
(rules.fitted.yaml): experts decide what to copy into rules.yaml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from suva.scoring.rules import RuleSet
from suva.scoring.score import rule_features

TARGET_RECALL = 0.9


@dataclass
class FittedWeights:
    weights: dict[str, int]  # rule id -> points (per match for graded rules)
    current: dict[str, float]
    base: int
    threshold: float  # highest score threshold that keeps TARGET_RECALL on the training data
    n: int
    n_rel: int
    graded: set[str] = field(default_factory=set)


def logistic_regression(x: np.ndarray, y: np.ndarray, l2: float = 1.0, steps: int = 5000,
                        lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Class-balanced, L2-regularised logistic regression by gradient descent (no sklearn needed).

    Balancing lets the few relevant judgements shape the weights; the intercept is then
    corrected back to the real share of relevant judgements, so probabilities stay honest.
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


def recall_threshold(scores: list[float], labels: list[bool], target: float = TARGET_RECALL) -> float:
    """Highest threshold at which at least `target` of the relevant judgements score at or above it."""
    rel = sorted((s for s, y in zip(scores, labels) if y), reverse=True)
    if not rel:
        return 50.0
    keep = max(1, math.ceil(target * len(rel)))
    return math.floor(rel[keep - 1])


def fit_weights(records: list[dict[str, Any]], ruleset: RuleSet, l2: float = 1.0) -> FittedWeights:
    x = np.array(rule_features(records, ruleset), dtype=float).reshape(len(records), len(ruleset.rules))
    y = np.array([1.0 if r["relevant"] else 0.0 for r in records])
    if not len(records) or y.sum() == 0 or y.sum() == len(y):
        raise ValueError("need both relevant and not relevant records to fit weights")
    w, b = logistic_regression(x, y, l2=l2)
    scale = ruleset.scale
    points = x @ (w * scale) + b * scale
    scores = list(100 / (1 + np.exp(-np.clip(points / scale, -60, 60))))
    return FittedWeights(
        weights={r.id: int(round(c * scale)) for r, c in zip(ruleset.rules, w)},
        current={r.id: (r.per_match if r.graded else r.weight) for r in ruleset.rules},
        base=int(round(b * scale)), threshold=recall_threshold(scores, [bool(v) for v in y]),
        n=len(records), n_rel=int(y.sum()), graded={r.id for r in ruleset.rules if r.graded},
    )


def write_fitted(ruleset: RuleSet, fitted: FittedWeights, path: Path) -> None:
    """Copy of rules.yaml with suggested weights; comments are not preserved."""
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
    data["threshold"] = fitted.threshold
    header = (f"# Suggested weights fitted on {fitted.n} training judgements ({fitted.n_rel} relevant).\n"
              f"# threshold keeps {TARGET_RECALL:.0%} recall on the training data.\n"
              "# Review before copying into rules.yaml. Comments from rules.yaml are not preserved.\n")
    path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
