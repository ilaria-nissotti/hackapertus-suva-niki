import math
import random

import pytest
import yaml

from suva.scoring.cv import expected_at_n, fold_n, per_period_size, run_cv, summarise
from suva.scoring.evaluate import at_n, ranking, score_all
from suva.scoring.fit import fit_weights, recall_floor, write_fitted
from suva.scoring.rules import parse_rules
from suva.scoring.score import ScoreResult, score_record
from suva.scoring.select import period_of, ranked, select, select_scored, select_top

RULES = {
    "base": -30,
    "selection": {"top_n": 3, "floor_rule_points": 10, "per": "month"},  # = score -20 with base -30
    "rules": [
        {"id": "suva", "field": "parties", "match": {"any": ["Suva"]}, "weight": 20, "explanation": "Suva."},
        {"id": "uvg", "field": "laws", "match": {"any": ["UVG"]}, "weight": 15, "explanation": "UVG."},
        {"id": "uvg_articles", "field": "articles", "match": {"regex": " UVG$"}, "case_sensitive": True,
         "per_match": 3, "max": 12, "explanation": "UVG articles."},
        {"id": "health", "field": "subject", "match": {"any": ["Krankenversicherung"]}, "weight": -25,
         "explanation": "Health."},
    ],
}


def rec(key, relevant=False, date="2025-10-15", **fields):
    base = {"key": key, "docket": key.split("@")[0], "relevant": relevant, "split": "train", "decision_date": date,
            "parties": "", "subject": "", "laws": [], "articles": [], "chamber": "8C"}
    return base | fields


def scored_item(key, points, relevant=False, date="2025-10-15"):
    return rec(key, relevant, date), ScoreResult(key, float(points))


def random_records(n=200, seed=1):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        out.append(rec(f"8C_{i}/2025@2025-10-{1 + i % 28:02d}", rng.random() < 0.2,
                       date=f"2025-{10 + i % 3}-{1 + i % 28:02d}",
                       parties="Suva" if rng.random() < 0.4 else "A.",
                       laws=["UVG"] if rng.random() < 0.5 else [],
                       articles=[f"Art. {k} UVG" for k in range(rng.randrange(6))],
                       subject="Krankenversicherung" if rng.random() < 0.2 else ""))
    return out


def old_score(points, scale=10.0):
    """The former 0-100 S-curve, kept here only to show it never changed the order."""
    z = max(-60.0, min(60.0, points / scale))
    return 100.0 / (1.0 + math.exp(-z))


# --- ranking by points -------------------------------------------------------------------------

def test_ranking_by_points_gives_the_same_order_as_the_old_score():
    ruleset = parse_rules(RULES)
    scored = score_all(random_records(), ruleset)
    by_points = [r["key"] for r, _ in ranked(scored)]
    by_old_score = [r["key"] for r, s in sorted(scored, key=lambda rs: (-old_score(rs[1].display_score),
                                                                        rs[0]["relevant"], rs[0]["key"]))]
    assert by_points == by_old_score
    assert len({s.rule_points for _, s in scored}) > 5  # the check is not trivial: many distinct values


def test_base_shifts_points_but_not_the_order():
    records = random_records()
    low = [r["key"] for r, _ in ranked(score_all(records, parse_rules(RULES)))]
    high = [r["key"] for r, _ in ranked(score_all(records, parse_rules(RULES | {"base": 50})))]
    assert low == high


# --- select_top ---------------------------------------------------------------------------------

def test_select_top_returns_at_most_n_and_nothing_below_the_floor():
    ruleset = parse_rules(RULES)
    records = random_records()
    for n in (1, 3, 10, 500):
        for floor in (None, -40, -20, 0, 30):
            chosen = select_top(records, ruleset, n, floor)
            assert len(chosen) <= n
            assert all(floor is None or c.rule_points >= floor for c in chosen)
            assert [c.rank for c in chosen] == list(range(1, len(chosen) + 1))
            # what is left out is never better than what is shown
            shown = {c.key for c in chosen}
            rest = [score_record(r, ruleset).rule_points for r in records if r["key"] not in shown]
            if chosen and rest and len(chosen) == n:
                assert max(rest) <= min(c.rule_points for c in chosen)


def test_select_top_ranks_and_reports_points():
    ruleset = parse_rules(RULES)
    records = [rec("a@1", parties="Suva", laws=["UVG"]),  # 35 rule points, score 5
               rec("b@1", parties="Suva"),  # 20, score -10
               rec("c@1"),  # 0, below the floor of 10 rule points
               rec("d@1", laws=["UVG"])]  # 15, score -15
    chosen = select_top(records, ruleset, n=10, floor_rule_points=10)
    assert [(c.key, c.rank, c.rule_points, c.display_score) for c in chosen] == [
        ("a@1", 1, 35, 5), ("b@1", 2, 20, -10), ("d@1", 3, 15, -15)]


def test_ties_keep_the_tie_break_order_and_are_cut_at_n():
    items = [scored_item("c@1", 10), scored_item("a@1", 10), scored_item("b@1", 10), scored_item("z@1", 20)]
    chosen = select_scored(items, n=3, floor_rule_points=None)
    assert [c.key for c in chosen] == ["z@1", "a@1", "b@1"]  # equal points: by key, cut at 3
    # in evaluation the label joins the tie-break: not relevant first, so ties never flatter
    labelled = [scored_item("a@1", 10, relevant=True), scored_item("b@1", 10, relevant=False)]
    assert [c.key for c in select_scored(labelled, n=1, floor_rule_points=None)] == ["b@1"]
    # the floor keeps or drops a whole tie group
    assert [c.key for c in select_scored(items, n=None, floor_rule_points=10)] == ["z@1", "a@1", "b@1", "c@1"]
    assert [c.key for c in select_scored(items, n=None, floor_rule_points=11)] == ["z@1"]


def test_selection_is_per_period():
    items = [scored_item("o1@1", 50, date="2025-10-02"), scored_item("o2@1", 40, date="2025-10-20"),
             scored_item("o3@1", 30, date="2025-10-30"), scored_item("n1@1", 5, date="2025-11-03"),
             scored_item("n2@1", -50, date="2025-11-04")]
    chosen = select_scored(items, n=2, floor_rule_points=0, per="month")
    assert [(c.period, c.key, c.rank) for c in chosen] == [("2025-10", "o1@1", 1), ("2025-10", "o2@1", 2),
                                                           ("2025-11", "n1@1", 1)]  # n2 is below the floor
    assert len(select_scored(items, n=2, floor_rule_points=None, per="period")) == 2
    assert period_of(rec("x@1", date="2025-10-06"), "week") == "2025-W41"
    assert period_of({"key": "x"}, "month") == "unknown"
    with pytest.raises(ValueError):
        period_of(rec("x@1"), "day")


def test_select_uses_the_rules_file_selection():
    ruleset = parse_rules(RULES)  # top 3 per month, floor -20
    records = [rec(f"k{i}@1", parties="Suva", laws=["UVG"]) for i in range(5)] + [rec("low@1")]
    chosen = select(records, ruleset)
    assert len(chosen) == 3 and "low@1" not in {c.key for c in chosen}


# --- recall and precision at N ----------------------------------------------------------------

def test_recall_and_precision_at_n_on_a_small_dataset():
    # points: 9 8 7 6 5 4 3 2 1 0, relevant at 9, 7, 4 and 0
    rel = {9, 7, 4, 0}
    items = [scored_item(f"k{p}@1", p, relevant=p in rel) for p in range(10)]
    m = at_n(items, n=3, floor_rule_points=None)
    assert (m.shown, m.true_pos) == (3, 2) and m.recall == 0.5 and m.precision == pytest.approx(2 / 3)
    m = at_n(items, n=10, floor_rule_points=3)  # the floor stops the list at 3 points
    assert (m.shown, m.true_pos) == (7, 3) and m.recall == 0.75 and m.precision == pytest.approx(3 / 7)
    m = at_n(items, n=10, floor_rule_points=None)
    assert m.recall == 1.0 and m.precision == 0.4


def test_recall_at_n_is_counted_per_period():
    items = [scored_item("a@1", 10, True, "2025-10-01"), scored_item("b@1", 5, True, "2025-10-02"),
             scored_item("c@1", 1, True, "2025-11-01"), scored_item("d@1", 0, False, "2025-11-02")]
    m = at_n(items, n=1, floor_rule_points=None, per="month")
    assert m.periods == 2 and m.shown == 2 and m.true_pos == 2 and m.recall == pytest.approx(2 / 3)
    assert [(p["period"], p["relevant"], p["found"]) for p in m.per_period] == [("2025-10", 2, 1),
                                                                                 ("2025-11", 1, 1)]
    assert (m.expected_recall, m.expected_precision) == pytest.approx((m.recall, m.precision))  # no ties


def test_at_n_reports_ties_both_pessimistic_and_expected():
    # 4 judgements tie; N = 2 takes two of them. Pessimistic: the 2 not relevant. Expected: 1 relevant.
    items = [scored_item(f"t{i}@1", 0, relevant=i < 2) for i in range(4)]
    m = at_n(items, n=2, floor_rule_points=None)
    assert m.recall == 0 and m.expected_recall == pytest.approx(0.5) and m.expected_precision == pytest.approx(0.5)


def test_expected_at_n_averages_over_the_order_of_ties():
    # one sure hit at the top, then 4 tied judgements with 2 relevant; N = 3 takes 2 of the 4
    items = [scored_item("top@1", 10, True)] + [scored_item(f"t{i}@1", 0, i < 2) for i in range(4)]
    recall, precision = expected_at_n(items, 3, None)
    assert recall == pytest.approx((1 + 2 * 2 / 4) / 3) and precision == pytest.approx(2 / 3)
    assert expected_at_n(items, 3, 5) == pytest.approx((1 / 3, 1.0))  # the floor drops the tied group
    # with distinct points it agrees with the selection itself
    distinct = [scored_item(f"k{p}@1", p, relevant=p % 3 == 0) for p in range(12)]
    m = at_n(distinct, n=5, floor_rule_points=None)
    assert expected_at_n(distinct, 5, None) == pytest.approx((m.recall, m.precision))


def test_cv_scales_n_to_the_fold_and_reports_recall_at_n():
    records = random_records(300)
    assert per_period_size(records, "month") == pytest.approx(100)  # 3 months
    stray = records + [rec("x@1", date="2026-03-02")]  # a lone judgement does not count as a month
    assert per_period_size(stray, "month") == pytest.approx(100)
    assert fold_n(30, 60, 100.0) == 18 and fold_n(None, 60, 100.0) is None and fold_n(1, 1, 100.0) == 1
    ruleset = parse_rules(RULES | {"selection": {"top_n": 30, "per": "month"}})
    per_fold = run_cv(records, ruleset, "refit", seeds=(0,))
    assert per_fold and all(15 <= f["n_at"] <= 21 for f in per_fold)  # folds of ~60 -> N of about 18
    s = summarise(per_fold)
    assert 0 <= s["recall_at_n"][0] <= 1 and 0 <= s["precision_at_n"][0] <= 1


# --- fitting the floor --------------------------------------------------------------------------

def test_recall_floor_keeps_the_target_share_of_relevant_points():
    points = [10, 8, 6, 4, 2, 0, -5, -10, -20, -30]
    labels = [True, False, True, False, True, False, True, False, True, False]  # relevant: 10 6 2 -5 -20
    assert recall_floor(points, labels, 0.8) == -5  # 4 of 5 relevant have >= -5
    assert recall_floor(points, labels, 1.0) == -20
    assert recall_floor([1.5], [True], 0.9) == 1  # whole numbers, rounded down
    with pytest.raises(ValueError):
        recall_floor([1.0], [False])


def test_fitted_floor_matches_the_fitted_points_and_is_written(tmp_path):
    records = random_records(300)
    ruleset = parse_rules(RULES)
    fitted = fit_weights(records, ruleset)
    from suva.scoring.fit import apply_fitted
    active = apply_fitted(ruleset, fitted)
    rel_points = [score_record(r, active).rule_points for r in records if r["relevant"]]
    assert sum(p >= fitted.floor_rule_points for p in rel_points) >= 0.9 * len(rel_points)
    assert active.selection.floor_rule_points == fitted.floor_rule_points and active.selection.top_n == 3

    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(RULES), encoding="utf-8")
    ruleset.path = path
    out = tmp_path / "rules.fitted.yaml"
    write_fitted(ruleset, fitted, out)
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["selection"] == {"top_n": 3, "floor_rule_points": fitted.floor_rule_points, "per": "month"}
    assert "threshold" not in data and "scale" not in data
    parse_rules(data)  # the suggestion loads as a rules file


def test_ranking_metrics_use_points():
    items = [scored_item("a@1", -5, True), scored_item("b@1", -10, False), scored_item("c@1", -50, True)]
    rk = ranking(items)
    assert rk.auc == 0.5 and rk.depth_for_recall[1.0] == 3
