import math
import random

import pytest

from suva.scoring.cv import (
    MODES,
    run_cv,
    shuffle_labels,
    stratified_folds,
    summarise,
    with_fitted,
)
from suva.scoring.fit import fit_weights
from suva.scoring.rules import parse_rules
from suva.scoring.score import score_record

RULES = {
    "base": -30,
    "selection": {"top_n": 30, "floor_rule_points": 1, "per": "month"},
    "rules": [
        {"id": "suva", "field": "parties", "match": {"any": ["Suva"]}, "weight": 20,
         "explanation": "Suva is a party."},
        {"id": "uvg_articles", "field": "articles", "match": {"regex": " UVG$"}, "case_sensitive": True,
         "per_match": 3, "max": 9, "explanation": "UVG articles."},
    ],
}


def make_records(n=300, rate=0.15, signal=0.98, noise=0.0, seed=7):
    """Relevant judgements mostly name Suva as a party; `noise` controls how often that is wrong."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        relevant = rng.random() < rate
        suva = relevant if rng.random() < signal else not relevant
        if rng.random() < noise:
            suva = rng.random() < 0.5
        out.append({
            "key": f"8C_{i}/2025@2025-11-01", "docket": f"8C_{i}/2025", "relevant": relevant,
            "parties": "Suva" if suva else "A. Muster", "articles": [], "laws": [], "chamber": "8C",
            "subject": "", "split": "train",
        })
    return out


def no_signal_records(n=300, rate=0.15, seed=3):
    rng = random.Random(seed)
    return [dict(r, parties="Suva" if rng.random() < 0.5 else "A. Muster")
            for r in make_records(n, rate, seed=seed)]


@pytest.fixture
def ruleset():
    return parse_rules(RULES)


def test_folds_keep_each_docket_in_one_fold_and_spread_relevant_ones():
    records = make_records()
    fold = stratified_folds(records, k=5, seed=0)
    assert set(fold) == {r["docket"] for r in records}
    relevant_per_fold = [0] * 5
    for r in records:
        if r["relevant"]:
            relevant_per_fold[fold[r["docket"]]] += 1
    assert min(relevant_per_fold) > 0
    assert max(relevant_per_fold) - min(relevant_per_fold) <= 2


def test_with_fitted_applies_points_base_and_floor(ruleset):
    records = make_records()
    fitted = fit_weights(records, ruleset)
    refit = with_fitted(ruleset, fitted)
    assert refit.base == fitted.base
    assert refit.selection.floor_rule_points == fitted.floor_rule_points
    assert refit.selection.top_n == 30 and refit.selection.per == "month"  # only the floor is refitted
    assert refit.by_id("suva").weight == fitted.weights["suva"]
    graded = refit.by_id("uvg_articles")
    assert graded.per_match == fitted.weights["uvg_articles"]
    assert graded.max == pytest.approx(fitted.weights["uvg_articles"] * ruleset.by_id("uvg_articles").cap_count)
    # the original ruleset is untouched
    assert ruleset.by_id("suva").weight == 20


def test_shuffle_keeps_the_number_of_relevant_judgements():
    records = make_records()
    shuffled = shuffle_labels(records, random.Random(1))
    assert sum(r["relevant"] for r in shuffled) == sum(r["relevant"] for r in records)
    assert [r["key"] for r in shuffled] == [r["key"] for r in records]


def test_refit_beats_random_when_the_rules_carry_signal(ruleset):
    records = make_records()
    refit = summarise(run_cv(records, ruleset, "refit", seeds=(0, 1)))
    assert refit["average_precision_ties"][0] > refit["random_ap"][0] + 0.3


def test_shuffled_labels_are_worse_than_refit_on_the_same_folds(ruleset):
    # With real signal in the test folds a shuffled fit still gets the rule's sign right about half
    # the time, so its spread is large. The useful comparison is against the refit, not a single number.
    records = make_records()
    refit = summarise(run_cv(records, ruleset, "refit", seeds=(0, 1)))
    shuffled = summarise(run_cv(records, ruleset, "shuffled", seeds=(0, 1)))
    assert refit["average_precision_ties"][0] > shuffled["average_precision_ties"][0] + 0.1


def test_without_signal_refit_is_no_better_than_random(ruleset):
    records = no_signal_records()
    refit = summarise(run_cv(records, ruleset, "refit", seeds=(0, 1)))
    assert abs(refit["average_precision_ties"][0] - refit["random_ap"][0]) < 0.05


def test_tie_averaged_ap_equals_plain_ap_when_scores_are_distinct():
    from suva.scoring.cv import average_precision_random_ties
    from suva.scoring.evaluate import ranking
    from suva.scoring.score import ScoreResult

    scored = [({"relevant": rel, "key": str(i)}, ScoreResult(str(i), float(s)))
              for i, (rel, s) in enumerate([(1, 90), (0, 80), (1, 70), (0, 60), (1, 50), (0, 40)])]
    assert average_precision_random_ties(scored, random.Random(0)) == pytest.approx(ranking(scored).average_precision)


def test_random_ranking_ap_is_above_the_base_rate_for_small_folds():
    from suva.scoring.cv import random_ranking_ap
    value = random_ranking_ap(60, 9, random.Random(0), n_perm=2000)
    assert 0.17 < value < 0.23  # simulated: about 0.20, base rate 0.15


def test_current_mode_uses_the_rules_file_unchanged(ruleset):
    records = make_records()
    per_fold = run_cv(records, ruleset, "current", seeds=(0,))
    assert per_fold and all(not math.isnan(f["average_precision"]) for f in per_fold)


def test_unknown_mode_is_rejected(ruleset):
    with pytest.raises(ValueError):
        run_cv(make_records(), ruleset, "nonsense")
    assert "refit" in MODES


def test_score_record_matches_the_refit_ruleset(ruleset):
    rec = make_records(10)[0]
    fitted = fit_weights(make_records(), ruleset)
    refit = with_fitted(ruleset, fitted)
    assert score_record(rec, refit).display_score == pytest.approx(
        refit.base + sum(f.points for f in score_record(rec, refit).fired))
