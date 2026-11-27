"""Scoring edge cases: the S-curve, band edges, rule counting, validation, fit and evaluation.

Tests describe what the code does today. `# BUG:` marks behaviour that looks wrong.
"""

from datetime import date

import pytest

from suva.parse.run import parse_all
from suva.scoring.evaluate import calibration, evaluate, metrics, ranking, write_scores
from suva.scoring.fit import fit_weights, recall_threshold, write_fitted
from suva.scoring.rules import RuleSet, RulesError, load_rules, parse_rules
from suva.scoring.score import ScoreResult, band_for, rule_features, score_record, to_score
from suva.scoring.store import is_current, rules_hash, score_keys
from suva.sources.base import SourceRecord
from test_scoring import JUDGEMENT, RULES, rec


def rule(**overrides):
    base = {"id": "r", "field": "text", "match": {"any": ["Suva"]}, "weight": 10, "explanation": "x"}
    return base | overrides


def one_rule_set(**overrides):
    return parse_rules({"rules": [rule(**overrides)]})


# --- to_score: the S-curve -------------------------------------------------------------------

def test_zero_points_is_exactly_fifty():
    assert to_score(0, 10) == 50.0


def test_score_stays_inside_0_100_for_extreme_points():
    assert 0.0 < to_score(-1e9, 10) < to_score(1e9, 10) == 100.0


def test_negative_points_are_clamped_at_minus_60_log_odds():
    assert to_score(-600, 10) == to_score(-1000, 10)
    assert to_score(-599, 10) > to_score(-600, 10)


def test_high_scores_saturate_to_100_long_before_the_clamp():
    # BUG: the docstring says scores stay unrounded "so very low or very high scores still sort",
    # but float precision makes everything above roughly z=36.8 exactly 100.0, so these tie.
    # Expected: 368 points should still rank above 367 points (e.g. sort by points as a tie-break).
    assert to_score(367, 10) < 100.0
    assert to_score(368, 10) == to_score(600, 10) == 100.0


def test_direct_ruleset_with_zero_scale_divides_by_zero():
    # parse_rules forbids scale <= 0, but a RuleSet built in code does not.
    with pytest.raises(ZeroDivisionError):
        score_record(rec(), RuleSet(rules=[], scale=0))


# --- band_for / relevant ----------------------------------------------------------------------

@pytest.mark.parametrize("score, band", [
    (70.0, "high"), (69.999, "check"), (30.0, "check"), (29.999, "low"), (100.0, "high"), (0.0, "low"),
])
def test_band_edges_are_inclusive_at_high_and_low(score, band):
    assert band_for(score, RuleSet(rules=[])) == band  # defaults: high 70, low 30


def test_equal_low_and_high_bands_never_give_check():
    rs = parse_rules({"rules": [], "bands": {"high": 40, "low": 40}})
    assert {band_for(s, rs) for s in (0, 39.99, 40, 40.01, 100)} == {"low", "high"}


def test_relevant_is_inclusive_at_the_threshold():
    rs = parse_rules({"rules": [], "threshold": 50})
    assert score_record(rec(), rs).score == 50.0 and score_record(rec(), rs).relevant


def test_no_rule_fired_gives_a_clear_explanation():
    assert score_record(rec(), parse_rules({"rules": []})).explanation == "No rule matched."


# --- Rule.count ------------------------------------------------------------------------------

def test_missing_field_counts_zero():
    r = one_rule_set().rules[0]
    assert r.count({}) == 0 and r.count({"text": None}) == 0


def test_empty_text_counts_zero():
    assert one_rule_set().rules[0].count({"text": ""}) == 0


@pytest.mark.parametrize("field", ["text", "articles"])
def test_any_plus_regex_is_rejected(field):
    with pytest.raises(RulesError, match="either match.any or match.regex"):
        parse_rules({"rules": [{"id": "r", "field": field, "match": {"any": ["Suva"], "regex": "UVG"},
                                "per_match": 1, "explanation": "x"}]})


def test_all_plus_regex_is_still_allowed():
    r = parse_rules({"rules": [{"id": "r", "field": "text", "match": {"all": ["Suva"], "regex": "UVG"},
                                "per_match": 1, "explanation": "x"}]}).rules[0]
    assert r.count({"text": "Suva UVG UVG"}) == 2 and r.count({"text": "UVG"}) == 0


def test_all_terms_must_all_appear():
    r = one_rule_set(match={"all": ["Suva", "Rente"]}).rules[0]
    assert r.count({"text": "Suva Rente Rente"}) == 3
    assert r.count({"text": "Suva only"}) == 0


def test_list_field_accepts_a_single_string_value():
    r = one_rule_set(field="chamber", match={"any": ["8C"]}).rules[0]
    assert r.count({"chamber": "8C"}) == 1 and r.count({"chamber": "9C"}) == 0


def test_case_sensitive_rule_ignores_other_case():
    r = one_rule_set(case_sensitive=True).rules[0]
    assert r.count({"text": "SUVA"}) == 0 and r.count({"text": "Suva"}) == 1


def test_graded_rule_reaching_its_cap_exactly():
    r = parse_rules({"rules": [{"id": "g", "field": "text", "match": {"any": ["Unfall"]}, "per_match": 5,
                                "max": 15, "explanation": "x"}]}).rules[0]
    assert r.points_for(3) == 15 and r.points_for(4) == 15 and r.points_for(2) == 10
    assert r.points_for(0) == 0 and r.points_for(-1) == 0


def test_graded_rule_with_max_zero_fires_for_zero_points():
    rs = parse_rules({"rules": [{"id": "g", "field": "text", "match": {"any": ["Unfall"]}, "per_match": 5,
                                 "max": 0, "explanation": "Accident."}]})
    s = score_record({"text": "Unfall"}, rs)
    # The rule shows up as "fired" in the explanation although it adds nothing.
    assert [(f.id, f.points) for f in s.fired] == [("g", 0)] and s.points == 0


def test_graded_rule_without_max_is_uncapped():
    r = parse_rules({"rules": [{"id": "g", "field": "text", "match": {"any": ["Unfall"]}, "per_match": 2,
                                "explanation": "x"}]}).rules[0]
    assert r.cap_count == float("inf") and r.points_for(100) == 200


def test_rule_features_are_binary_or_capped_counts():
    rs = parse_rules({"rules": [
        rule(id="fixed"),
        {"id": "g", "field": "text", "match": {"any": ["Suva"]}, "per_match": 2, "max": 4, "explanation": "x"},
    ]})
    assert rule_features([{"text": "Suva Suva Suva"}, {"text": ""}], rs) == [[1.0, 2.0], [0.0, 0.0]]


# --- parse_rules validation ------------------------------------------------------------------

@pytest.mark.parametrize("data, message", [
    ({"rules": [], "scale": 0}, "scale must be positive"),
    ({"rules": [], "scale": -1}, "scale must be positive"),
    ({"rules": [], "threshold": "high"}, "threshold must be a number"),
    ({"rules": [], "base": None}, "base must be a number"),
    ({"rules": [], "bands": {"high": 120, "low": 10}}, "bands"),
    ({"rules": [], "bands": {"high": 20, "low": 30}}, "bands"),
    ({"rules": [], "bands": {"high": 70, "low": 30, "mid": 50}}, "bands"),
    ({"rules": [], "judge": {"points": {"great": 5}}}, "judge.points"),
    ({}, "top-level 'rules:'"),
    ({"rules": [rule(match={})]}, "match needs"),
    ({"rules": [rule(match={"any": [""]})]}, "non-empty terms"),
    ({"rules": [rule(weight=True)]}, "weight must be a number"),
    ({"rules": [rule(), rule()]}, "duplicate rule id"),
])
def test_invalid_rules_are_rejected_with_a_message(data, message):
    with pytest.raises(RulesError, match=message):
        parse_rules(data)


@pytest.mark.parametrize("regex", [5, None, ["UVG"], True])
def test_non_string_regex_is_a_rules_error(regex):
    with pytest.raises(RulesError, match=r"rule #1 \('r'\): match.regex must be a string"):
        parse_rules({"rules": [rule(match={"regex": regex})]})


def test_empty_rules_list_is_allowed_and_scores_base_only():
    rs = parse_rules({"rules": [], "base": -20})
    assert score_record(rec(), rs).points == -20


# --- store.is_current vs the review app ------------------------------------------------------

def test_is_current_ignores_inputs_hash_that_the_review_app_checks(repo):
    import review_kb

    key = "8C_1/2025@2025-10-02"
    repo.upsert_judgement(SourceRecord(source="opencaselaw", source_id="8C_1/2025", docket="8C_1/2025",
                                       decision_date=date(2025, 10, 2), text=JUDGEMENT))
    parse_all(repo)
    rs = load_rules()
    score_keys(repo, [key], rs)
    assert review_kb.get_record(repo, key, rules_hash(rs))["score_current"]

    # A second source fills in the language; text and parse stay the same, so the score inputs change.
    repo.upsert_judgement(SourceRecord(source="entscheidsuche", source_id="x", docket="8C_1/2025",
                                       decision_date=date(2025, 10, 2), language="de"))
    assert repo.get_judgement(key)["language"] == "de"
    text = repo.get_judgement(key)["text"]
    assert review_kb.get_record(repo, key, rules_hash(rs))["status"] == "Stale"
    # BUG: is_current still says True. Expected: False, matching review_kb.record (compare inputs_hash too).
    assert is_current(repo.get_score(key), text, rules_hash(rs))


def test_is_current_with_no_score_or_no_text():
    assert not is_current(None, "x", "h")
    assert not is_current({}, "x", "h")


def test_rules_hash_ignores_comments_but_not_weights():
    a = parse_rules(RULES)
    b = parse_rules(RULES | {"rules": [r | {"evidence": "note"} for r in RULES["rules"]]})
    c = parse_rules(RULES | {"rules": [RULES["rules"][0] | {"weight": 41}] + RULES["rules"][1:]})
    assert rules_hash(a) == rules_hash(b) != rules_hash(c)


# --- fit ---------------------------------------------------------------------------------------

def test_recall_threshold_edges():
    assert recall_threshold([10, 20], [False, False]) == 50.0  # no relevant records
    assert recall_threshold([80.7], [True]) == 80
    # 10 relevant -> keep 9 -> the 9th highest relevant score, floored
    scores = [95, 90, 85, 80, 75, 70, 65, 60, 55.5, 5]
    assert recall_threshold(scores, [True] * 10) == 55


def test_fit_needs_both_classes():
    rs = parse_rules(RULES)
    with pytest.raises(ValueError, match="both relevant and not relevant"):
        fit_weights([rec(relevant=True)], rs)
    with pytest.raises(ValueError):
        fit_weights([], rs)


def test_write_fitted_round_trips_through_load_rules(tmp_path):
    src = tmp_path / "rules.yaml"
    import yaml
    src.write_text(yaml.safe_dump(RULES | {"rules": RULES["rules"] + [
        {"id": "g", "field": "articles", "match": {"regex": "UVG$"}, "per_match": 2, "max": 6,
         "explanation": "x"}]}), encoding="utf-8")
    rs = load_rules(src)
    records = [rec(f"8C_{i}/2025@2025-10-02", relevant=i % 2 == 0, parties="Suva" if i % 2 == 0 else "",
                   articles=["Art. 6 UVG"]) for i in range(20)]
    fitted = fit_weights(records, rs)
    out = tmp_path / "rules.fitted.yaml"
    write_fitted(rs, fitted, out)
    again = load_rules(out)
    assert out.read_text(encoding="utf-8").startswith("# Suggested weights fitted on 20")
    assert again.by_id("suva").weight == fitted.weights["suva"] > 0
    assert again.by_id("g").max == round(fitted.weights["g"] * 3)  # cap count kept at 3 matches
    assert again.base == fitted.base


# --- evaluate ----------------------------------------------------------------------------------

def scored(*pairs):
    return [(rec(f"8C_{i}/2025@2025-10-02", relevant=rel), ScoreResult("", s, 0, "check", s >= 50))
            for i, (s, rel) in enumerate(pairs)]


def test_metrics_with_nothing_flagged_and_nothing_relevant():
    m = metrics(scored((10, False), (20, False)), 50)
    assert (m.flagged, m.precision, m.recall) == (0, 0.0, 0.0)
    assert metrics([], 50).flagged == 0


def test_metrics_threshold_is_inclusive():
    m = metrics(scored((50, True), (49.99, True)), 50)
    assert (m.true_pos, m.false_neg) == (1, 1)


def test_ranking_on_empty_and_one_class_inputs():
    empty = ranking([])
    assert empty.precision_at == {} and empty.auc == 0.0 and empty.average_precision == 0.0
    only_rel = ranking(scored((90, True)))
    assert only_rel.auc == 0.0 and only_rel.depth_for_recall[1.0] == 1


def test_ranking_ties_are_broken_pessimistically():
    r = ranking(scored((60, True), (60, False)))
    assert r.precision_at[10] == 0.5 and r.depth_for_recall[1.0] == 2 and r.auc == 0.5


def test_calibration_puts_a_perfect_score_in_the_top_bin():
    bins = calibration(scored((100.0, True), (0.0, False), (9.99, False)))
    assert [b["bin"] for b in bins] == ["0-10", "90-100"] and bins[0]["n"] == 2


def test_evaluate_and_write_scores_on_an_empty_split(tmp_path):
    ev = evaluate([], parse_rules(RULES), "test")
    assert ev.n == 0 and ev.at_threshold.flagged == 0 and ev.calibration == []
    out = tmp_path / "scores.csv"
    write_scores([], parse_rules(RULES), out)
    assert out.read_text(encoding="utf-8").splitlines() == [
        "rank,key,split,relevant_label,score,band,points,flagged,fired_rules,explanation,subject"]
