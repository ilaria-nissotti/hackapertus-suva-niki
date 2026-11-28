"""Compare scoring configurations fairly: how much does each group of signals add?

A configuration is a subset of rules.yaml: the topic rules alone, then with the significance
rules. Each one is judged by 5-fold cross-validation over the training split, grouped by docket:

  for each fold: fit the weights (fit.py) on the other 4 folds, score the held-out fold;
  then rank all held-out scores together and compute the ranking metrics of evaluate.py.

Every judgement is therefore scored by weights that never saw its label, the weights in
rules.yaml do not matter, and the test split stays untouched for the final number.

Confusion matrix (held-out folds only). Within each held-out fold, the judgements selected
for the reading list count as predicted relevant, Suva's picks as actually relevant; the four
counts are summed over the folds. A fold holds about 1/5 of each month, so it gets its share
of the month's top_n places:

    N for (fold, month) = round(top_n x judgements of that month in the fold
                                       / judgements of that month in all folds)

e.g. top 30, a month of 640 judgements of which 128 are in the fold -> N = 6. The floor is
the one fitted with that fold's weights (rule points, see fit.py). Equal rule points: not
relevant first (select.py), so ties never flatter the counts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from suva.hashing import sha256
from suva.scoring.evaluate import Ranking, ranking
from suva.scoring.fit import apply_fitted, fit_weights
from suva.scoring.rules import RuleSet
from suva.scoring.score import ScoreResult, score_record
from suva.scoring.select import period_of, select_scored

FOLDS = 5


@dataclass(frozen=True)
class Configuration:
    name: str
    groups: tuple[str, ...]  # rule groups included


@dataclass
class FoldScores:
    """One held-out fold: its judgements scored by weights fitted on the other folds."""

    fold: int
    scored: list[tuple[dict[str, Any], ScoreResult]]
    floor_rule_points: float | None  # the floor fitted with this fold's weights


@dataclass
class Confusion:
    """Selected vs picked by Suva, summed over the held-out folds."""

    tp: int  # picked and selected
    fp: int  # not picked, selected
    fn: int  # picked, not selected
    tn: int  # not picked, not selected
    top_n: int | None
    per: str
    floors: list[float | None]  # floor_rule_points per fold, in fold order

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"precision": self.precision, "recall": self.recall}


@dataclass
class ConfigurationResult:
    name: str
    rules: int
    ranking: Ranking
    confusion: Confusion | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.confusion is not None:
            d["confusion"] = self.confusion.as_dict()
        return d


def configurations(ruleset: RuleSet) -> list[Configuration]:
    """Topic rules, then topic + significance rules (if the rules file has any)."""
    steps = [Configuration("topic rules", ("topic",))]
    if any(r.group == "significance" for r in ruleset.rules):
        steps.append(Configuration("+ significance rules", ("topic", "significance")))
    return steps


def subset(ruleset: RuleSet, config: Configuration) -> RuleSet:
    return replace(ruleset, rules=[r for r in ruleset.rules if r.group in config.groups])


def fold_of(docket: str, folds: int = FOLDS) -> int:
    """Stable fold per docket, so all decisions of a docket are held out together."""
    return int(sha256(docket)[:8], 16) % folds


def cross_validated_folds(records: list[dict[str, Any]], ruleset: RuleSet, folds: int = FOLDS) -> list[FoldScores]:
    out = []
    for fold in range(folds):
        fit_on = [r for r in records if fold_of(r["docket"], folds) != fold]
        held_out = [r for r in records if fold_of(r["docket"], folds) == fold]
        fitted = apply_fitted(ruleset, fit_weights(fit_on, ruleset))
        out.append(FoldScores(fold, [(r, score_record(r, fitted)) for r in held_out],
                              fitted.selection.floor_rule_points))
    return out


def cross_validated_scores(records: list[dict[str, Any]], ruleset: RuleSet,
                           folds: int = FOLDS) -> list[tuple[dict[str, Any], ScoreResult]]:
    return [item for f in cross_validated_folds(records, ruleset, folds) for item in f.scored]


def fold_top_n(top_n: int | None, in_fold: int, in_period: int) -> int | None:
    """The fold's share of a period's top_n places (see the module docstring)."""
    if top_n is None:
        return None
    return round(top_n * in_fold / in_period) if in_period else 0


def confusion(folds: list[FoldScores], top_n: int | None, per: str) -> Confusion:
    """TP / FP / FN / TN of the reading list over the held-out folds (never the data a fold was fitted on)."""
    period_size: dict[str, int] = {}
    for f in folds:
        for rec, _ in f.scored:
            period_size[period_of(rec, per)] = period_size.get(period_of(rec, per), 0) + 1
    tp = fp = fn = tn = 0
    for f in folds:
        groups: dict[str, list[tuple[dict[str, Any], ScoreResult]]] = {}
        for item in f.scored:
            groups.setdefault(period_of(item[0], per), []).append(item)
        for period, items in groups.items():
            n = fold_top_n(top_n, len(items), period_size[period])
            chosen = {s.key for s in select_scored(items, n, f.floor_rule_points, "period")}
            for rec, _ in items:
                picked, selected = bool(rec["relevant"]), rec["key"] in chosen
                tp += picked and selected
                fn += picked and not selected
                fp += selected and not picked
                tn += not picked and not selected
    return Confusion(tp, fp, fn, tn, top_n, per, [f.floor_rule_points for f in folds])


def compare(records: list[dict[str, Any]], ruleset: RuleSet, folds: int = FOLDS,
            progress: Callable[[str], None] | None = None) -> list[ConfigurationResult]:
    results = []
    for config in configurations(ruleset):
        if progress:
            progress(config.name)
        part = subset(ruleset, config)
        fold_scores = cross_validated_folds(records, part, folds)
        rk = ranking([item for f in fold_scores for item in f.scored])
        cm = confusion(fold_scores, part.selection.top_n, part.selection.per)
        results.append(ConfigurationResult(config.name, len(part.rules), rk, cm))
    return results
