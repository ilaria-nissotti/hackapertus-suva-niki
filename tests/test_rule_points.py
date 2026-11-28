"""Base is display only: the selection floor and the judge gate work on rule points (without base),
and select exactly the same judgements as the former floors on base + rule points."""

import csv
import random
from dataclasses import replace

import pytest

from suva.scoring.compare import Confusion, FoldScores, confusion, cross_validated_folds, fold_top_n
from suva.scoring.evaluate import write_scores
from suva.scoring.judge import candidates
from suva.scoring.rules import DEFAULT_RULES_PATH, load_rules, parse_rules
from suva.scoring.score import ScoreResult, score_record
from suva.scoring.select import select, select_scored

OLD_FLOOR = -29  # the former selection.floor_points and judge.candidate_floor_points, on base + rule points

RULES = {
    "base": -95,
    "selection": {"top_n": 4, "floor_rule_points": OLD_FLOOR + 95, "per": "month"},
    "judge": {"candidate_floor_rule_points": OLD_FLOOR + 95},
    "rules": [
        {"id": "accident", "field": "subject", "match": {"any": ["Unfallversicherung"]}, "weight": 51,
         "explanation": "Accident insurance."},
        {"id": "chamber", "field": "chamber", "match": {"any": ["8C", "9C"]}, "weight": 32, "explanation": "8C/9C."},
        {"id": "suva", "field": "parties", "match": {"any": ["Suva"]}, "weight": 6, "explanation": "Suva."},
        {"id": "uvg_articles", "field": "articles", "match": {"regex": " UVG$"}, "case_sensitive": True,
         "per_match": 6, "max": 30, "explanation": "UVG articles."},
        {"id": "not_entered", "field": "dispositiv", "match": {"any": ["nicht eingetreten"]}, "weight": -43,
         "explanation": "Inadmissible."},
    ],
}


def random_records(n=400, seed=5):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        out.append({
            "key": f"8C_{i}/2025@2025-{10 + i % 3}-{1 + i % 28:02d}", "docket": f"8C_{i}/2025",
            "decision_date": f"2025-{10 + i % 3}-{1 + i % 28:02d}", "relevant": rng.random() < 0.1, "split": "train",
            "subject": "Unfallversicherung" if rng.random() < 0.4 else "Steuern",
            "chamber": rng.choice(["8C", "9C", "2C", "6B"]), "parties": "Suva" if rng.random() < 0.3 else "A.",
            "articles": [f"Art. {k} UVG" for k in range(rng.randrange(7))],
            "dispositiv": "nicht eingetreten" if rng.random() < 0.3 else "abgewiesen", "laws": [],
        })
    return out


def old_selection(records, ruleset, n, old_floor, per="month"):
    """The former behaviour: rank and cut on base + rule points, floor in the same units."""
    shifted = [(r, ScoreResult(r["key"], score_record(r, ruleset).display_score)) for r in records]
    return {s.key for s in select_scored(shifted, n, old_floor, per)}


def old_judge_candidates(records, ruleset, old_floor):
    out = [(r, s) for r in records if (s := score_record(r, ruleset)).display_score >= old_floor]
    return {r["key"] for r, _ in out}


# --- Part 1: same selection and judge candidates as before ------------------------------------------

def test_selection_is_identical_to_the_old_floor_on_base_plus_rule_points():
    ruleset = parse_rules(RULES)
    records = random_records()
    new = {s.key for s in select(records, ruleset)}
    assert new == old_selection(records, ruleset, 4, OLD_FLOOR)
    assert new  # not trivially empty
    for n in (1, 10, None):  # and for other budgets
        sel = replace(ruleset.selection, top_n=n)
        assert ({s.key for s in select(records, replace(ruleset, selection=sel))}
                == old_selection(records, ruleset, n, OLD_FLOOR))


def test_judge_candidates_are_identical_to_the_old_gate():
    ruleset = parse_rules(RULES)
    records = random_records()
    new = {r["key"] for r, _ in candidates(records, ruleset)}
    assert new == old_judge_candidates(records, ruleset, OLD_FLOOR)
    assert 0 < len(new) < len(records)


def test_shipped_rules_file_floors_in_rule_points_match_a_score_floor_shifted_by_base():
    ruleset = load_rules(DEFAULT_RULES_PATH)
    floor, judge_floor = ruleset.selection.floor_rule_points, ruleset.judge_floor_rule_points
    assert floor is not None and judge_floor is not None
    records = random_records()
    # the same judgements as a floor on base + rule points at floor + base, for the shipped weights
    assert ({s.key for s in select(records, ruleset)}
            == old_selection(records, ruleset, ruleset.selection.top_n, floor + ruleset.base))
    assert ({r["key"] for r, _ in candidates(records, ruleset)}
            == old_judge_candidates(records, ruleset, judge_floor + ruleset.base))


def test_base_no_longer_changes_selection_or_judge_candidates():
    records = random_records()
    a, b = parse_rules(RULES), parse_rules(RULES | {"base": 500})
    assert [s.key for s in select(records, a)] == [s.key for s in select(records, b)]
    assert [r["key"] for r, _ in candidates(records, a)] == [r["key"] for r, _ in candidates(records, b)]


def test_judge_candidates_floor_override_and_limit():
    ruleset = parse_rules(RULES)
    records = random_records()
    everyone = candidates(records, replace(ruleset, judge={}))  # no floor configured: all judgements
    assert len(everyone) == len(records)
    top = candidates(records, ruleset, floor_rule_points=100, limit=5)
    assert len(top) <= 5 and all(s.rule_points >= 100 for _, s in top)
    assert [s.rule_points for _, s in top] == sorted((s.rule_points for _, s in top), reverse=True)


# --- display score ----------------------------------------------------------------------------------

def test_display_score_is_base_plus_rule_points(tmp_path):
    ruleset = parse_rules(RULES)
    records = random_records(60)
    for item in select(records, ruleset):
        assert item.display_score == ruleset.base + item.rule_points
        assert item.rule_points == sum(f.points for f in item.result.fired)
    out = tmp_path / "scores.csv"
    write_scores(records, ruleset, out)
    with out.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(records)
    assert all(float(r["score"]) == ruleset.base + float(r["rule_points"]) for r in rows)
    ranks = [float(r["rule_points"]) for r in rows]
    assert ranks == sorted(ranks, reverse=True)  # ordered by rule points


# --- Part 2: confusion matrix on held-out folds --------------------------------------------------

def item(key, points, relevant, date="2025-10-15"):
    return ({"key": key, "relevant": relevant, "decision_date": date}, ScoreResult(key, float(points)))


def test_confusion_counts_match_a_hand_built_example():
    # One period of 10 judgements, split in two folds of 5; top_n 4 -> each fold gets round(4 x 5/10) = 2.
    fold0 = FoldScores(0, [item("a", 10, True), item("b", 9, False), item("c", 8, True),
                           item("d", 7, False), item("e", 6, False)], None)
    # fold 0 selects a (TP) and b (FP); c is FN; d, e are TN
    fold1 = FoldScores(1, [item("f", 5, False), item("g", 4, True), item("h", 3, True),
                           item("i", 2, False), item("j", 1, False)], 3.5)
    # fold 1 selects f (FP) and g (TP); h is FN (outside the 2 places); i, j are TN
    cm = confusion([fold0, fold1], top_n=4, per="period")
    assert (cm.tp, cm.fp, cm.fn, cm.tn) == (2, 2, 2, 4)
    assert cm.precision == 0.5 and cm.recall == 0.5
    assert cm.top_n == 4 and cm.per == "period" and cm.floors == [None, 3.5]
    assert "accuracy" not in cm.as_dict() and cm.as_dict()["precision"] == 0.5

    # a higher floor in fold 1 drops g as well: f is the only one selected there
    fold1.floor_rule_points = 4.5
    cm = confusion([fold0, fold1], top_n=4, per="period")
    assert (cm.tp, cm.fp, cm.fn, cm.tn) == (1, 2, 3, 4)


def test_confusion_gives_each_fold_its_share_of_each_month():
    # October: 4 judgements, 2 per fold; November: 2 judgements, both in fold 0. top_n 2 per month.
    fold0 = FoldScores(0, [item("o1", 9, True, "2025-10-01"), item("o2", 1, False, "2025-10-02"),
                           item("n1", 5, False, "2025-11-01"), item("n2", 4, True, "2025-11-02")], None)
    fold1 = FoldScores(1, [item("o3", 8, False, "2025-10-03"), item("o4", 7, True, "2025-10-04")], None)
    # Oct: each fold gets round(2 x 2/4) = 1 -> o1 (TP), o3 (FP). Nov: fold 0 gets round(2 x 2/2) = 2 -> n1 FP, n2 TP
    cm = confusion([fold0, fold1], top_n=2, per="month")
    assert (cm.tp, cm.fp, cm.fn, cm.tn) == (2, 2, 1, 1)
    assert fold_top_n(30, 128, 640) == 6 and fold_top_n(None, 5, 10) is None and fold_top_n(3, 0, 0) == 0


def test_confusion_from_cross_validation_counts_every_judgement_once_held_out():
    rules = RULES | {"selection": {"top_n": 30, "per": "month"}}
    ruleset = parse_rules(rules)
    records = random_records(300)
    folds = cross_validated_folds(records, ruleset)
    keys = [rec["key"] for f in folds for rec, _ in f.scored]
    assert sorted(keys) == sorted(r["key"] for r in records)  # each judgement once, in its held-out fold
    cm = confusion(folds, 30, "month")
    assert isinstance(cm, Confusion)
    assert cm.tp + cm.fp + cm.fn + cm.tn == len(records)
    assert cm.tp + cm.fn == sum(r["relevant"] for r in records)
    assert len(cm.floors) == 5 and all(f is not None for f in cm.floors)  # each fold's fitted floor
    # about top_n per month in total (3 months), give or take rounding and the floors
    assert cm.tp + cm.fp <= 3 * 30 + 5 * 3


@pytest.mark.parametrize("bad", [{"floor_points": -29}, {"floor_rule_points": "x"}])
def test_old_or_malformed_floor_is_rejected(bad):
    from suva.scoring.rules import RulesError
    with pytest.raises(RulesError):
        parse_rules(RULES | {"selection": bad})
