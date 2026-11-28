"""Honest estimates of how well the scorer ranks judgements it has not been fitted on.

The problem: `evaluate` scores the training set with weights that were fitted on that same
training set, so its numbers are optimistic. This module measures the scorer with grouped
cross-validation instead:

- folds are split by docket (decisions of one docket never straddle two folds), stratified so
  every fold gets its share of relevant dockets;
- the held-out fold is never used to fit anything;
- each mode answers one question:

    current   the rules.yaml weights as they are (optimistic: they were tuned on all of train)
    refit     weights refitted on the other folds (the honest version of the current design)
    shuffled  weights refitted on permuted labels, then tested on the real labels.
              A good score here means the pipeline finds signal in noise (a leak or overfit).
    random    random scores. The floor that any real model has to beat.

Metrics are per held-out fold; the report gives the mean and spread across folds and seeds,
because with ~60 relevant judgements single numbers move a lot.

Recall and precision at N follow the reading list (select.py): the top N of the fold, none
below the floor. A fold is a docket-grouped sample of the whole training period, not a
month, so N is scaled to its size:

    N for a fold = selection.top_n x fold size / judgements per period in train (median)

e.g. top 30 per month, ~620 training judgements per month, a fold of 640 -> N = 31. The
floor is the one in rules.yaml (current) or the one fitted with the weights (refit,
shuffled); random has none. Equal points are averaged over orders, like AP (ties).
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any

from suva.scoring.evaluate import expected_hits, ranking
from suva.scoring.fit import FittedWeights, apply_fitted, fit_weights
from suva.scoring.rules import RuleSet
from suva.scoring.score import ScoreResult, score_record
from suva.scoring.select import period_of

MODES = ("current", "refit", "shuffled", "random")
METRICS = ("average_precision_ties", "average_precision", "random_ap", "recall_at_n", "precision_at_n",
           "precision_at_20", "auc", "depth_90")


def stratified_folds(records: list[dict[str, Any]], k: int = 5, seed: int = 0) -> dict[str, int]:
    """docket -> fold number. Dockets with a relevant decision are spread over the folds first."""
    has_relevant: dict[str, bool] = {}
    for r in records:
        has_relevant[r["docket"]] = has_relevant.get(r["docket"], False) or bool(r["relevant"])
    rng = random.Random(seed)
    fold: dict[str, int] = {}
    for group in (True, False):
        dockets = sorted(d for d, rel in has_relevant.items() if rel == group)
        rng.shuffle(dockets)
        for i, docket in enumerate(dockets):
            fold[docket] = i % k
    return fold


def with_fitted(ruleset: RuleSet, fitted: FittedWeights) -> RuleSet:
    """Copy of the ruleset with the fitted points, base and floor (same as `fit-weights` suggests)."""
    return apply_fitted(ruleset, fitted)


def per_period_size(records: list[dict[str, Any]], per: str) -> float:
    """Median number of judgements per period (month, week, or the whole input).

    The median, because a stray judgement in a month outside the training window (train has one
    dated March 2026) would otherwise count as a whole month and shrink the mean.
    """
    counts: dict[str, int] = {}
    for r in records:
        counts[period_of(r, per)] = counts.get(period_of(r, per), 0) + 1
    return float(statistics.median(counts.values())) if counts else 0.0


def fold_n(top_n: int | None, fold_size: int, period_size: float) -> int | None:
    """selection.top_n scaled from one period to a fold of this size (at least 1)."""
    if top_n is None or not period_size:
        return None
    return max(1, round(top_n * fold_size / period_size))


def expected_at_n(scored: list[tuple[dict[str, Any], ScoreResult]], n: int | None,
                  floor_rule_points: float | None) -> tuple[float, float]:
    """Expected (recall, precision) of the top n above the floor, with equal points in random order."""
    hits, taken = expected_hits(scored, n, floor_rule_points)
    n_rel = sum(bool(rec["relevant"]) for rec, _ in scored)
    return (hits / n_rel if n_rel else math.nan), (hits / taken if taken else math.nan)


def shuffle_labels(records: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """Same number of relevant judgements, assigned to random records."""
    labels = [r["relevant"] for r in records]
    rng.shuffle(labels)
    return [r | {"relevant": label} for r, label in zip(records, labels)]


def average_precision_random_ties(scored: list[tuple[dict[str, Any], ScoreResult]],
                                  rng: random.Random, n_perm: int = 200) -> float:
    """Average precision where judgements with the same points are ordered at random, averaged over orders.

    `evaluate.ranking` breaks ties pessimistically (not relevant first). With rules that give only a
    few distinct point values, that alone pushes a model below random: a no-signal model with 2 point levels
    scored AP 0.08 instead of ~0.14. Averaging over tie orders removes that artefact.
    """
    n_rel = sum(bool(r["relevant"]) for r, _ in scored)
    if not n_rel:
        return math.nan
    total = 0.0
    for _ in range(n_perm):
        order = sorted(scored, key=lambda rs: (-rs[1].rule_points, rng.random()))
        hits, ap = 0, 0.0
        for i, (rec, _) in enumerate(order, 1):
            if rec["relevant"]:
                hits += 1
                ap += hits / i
        total += ap / n_rel
    return total / n_perm


def random_ranking_ap(n: int, n_rel: int, rng: random.Random, n_perm: int = 2000) -> float:
    """Expected AP of a random ranking for a fold of this size; the null to beat, not the base rate.

    For folds of ~60 judgements with ~9 relevant this is about 0.20, well above the base rate of 0.15.
    """
    if not n_rel:
        return math.nan
    total = 0.0
    for _ in range(n_perm):
        order = [1] * n_rel + [0] * (n - n_rel)
        rng.shuffle(order)
        hits, ap = 0, 0.0
        for i, rel in enumerate(order, 1):
            if rel:
                hits += 1
                ap += hits / i
        total += ap / n_rel
    return total / n_perm


def fold_metrics(scored: list[tuple[dict[str, Any], ScoreResult]], rng: random.Random | None = None,
                 n_at: int | None = None, floor_rule_points: float | None = None) -> dict[str, float]:
    rng = rng or random.Random(0)
    n = len(scored)
    rk = ranking(scored)
    depth = rk.depth_for_recall.get(0.9)
    n_rel = sum(bool(r["relevant"]) for r, _ in scored)
    recall_n, precision_n = expected_at_n(scored, n_at, floor_rule_points) if n_at else (math.nan, math.nan)
    return {
        "average_precision": rk.average_precision,
        "average_precision_ties": average_precision_random_ties(scored, rng),
        "random_ap": random_ranking_ap(n, n_rel, rng),
        "recall_at_n": recall_n,
        "precision_at_n": precision_n,
        "n_at": float(n_at) if n_at else math.nan,
        "precision_at_20": rk.precision_at.get(20, math.nan),
        "auc": rk.auc,
        "depth_90": depth / n if depth is not None and n else math.nan,  # share of the fold to read
        "base_rate": sum(bool(r["relevant"]) for r, _ in scored) / n if n else math.nan,
    }


def _run_fold(train, test, ruleset, mode, l2, rng) -> tuple[list[tuple[dict, ScoreResult]], float | None]:
    """Scored held-out records and the floor that goes with the weights used."""
    if mode == "random":
        return [(r, ScoreResult(r["key"], rng.uniform(0, 100))) for r in test], None
    if mode == "current":
        active = ruleset
    else:
        fit_data = shuffle_labels(train, rng) if mode == "shuffled" else train
        active = with_fitted(ruleset, fit_weights(fit_data, ruleset, l2=l2))
    return [(r, score_record(r, active)) for r in test], active.selection.floor_rule_points


def run_cv(records: list[dict[str, Any]], ruleset: RuleSet, mode: str = "refit", k: int = 5,
           seeds: tuple[int, ...] = (0,), l2: float = 1.0, top_n: int | None = None,
           per: str | None = None) -> list[dict[str, float]]:
    """One metrics dict per (seed, fold). top_n and per default to the selection in rules.yaml."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}; got {mode!r}")
    top_n = ruleset.selection.top_n if top_n is None else top_n
    period_size = per_period_size(records, per or ruleset.selection.per)
    out = []
    for seed in seeds:
        fold_of = stratified_folds(records, k, seed)
        rng = random.Random(seed)
        for f in range(k):
            train = [r for r in records if fold_of[r["docket"]] != f]
            test = [r for r in records if fold_of[r["docket"]] == f]
            if not any(r["relevant"] for r in test):
                continue  # nothing to rank against in this fold
            scored, floor = _run_fold(train, test, ruleset, mode, l2, rng)
            n_at = fold_n(top_n, len(test), period_size)
            out.append(fold_metrics(scored, rng, n_at, floor) | {"seed": seed, "fold": f})
    return out


def summarise(per_fold: list[dict[str, float]]) -> dict[str, tuple[float, float]]:
    """metric -> (mean, standard deviation) across folds and seeds; NaN values are skipped."""
    summary = {}
    for m in METRICS + ("base_rate", "n_at"):
        values = [f[m] for f in per_fold if not math.isnan(f[m])]
        summary[m] = (statistics.mean(values), statistics.pstdev(values)) if values else (math.nan, math.nan)
    return summary
