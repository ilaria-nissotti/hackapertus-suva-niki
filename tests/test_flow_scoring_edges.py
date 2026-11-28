"""Scoring edge cases: rule counting, validation, fit and evaluation (rule points; see test_select.py for the reading list).

Tests describe what the code does today. `# BUG:` marks behaviour that looks wrong.
"""

import pytest

from suva.scoring.evaluate import evaluate, ranking, write_scores
from suva.scoring.fit import fit_weights, write_fitted
from suva.scoring.rules import RuleSet, RulesError, load_rules, parse_rules
from suva.scoring.score import ScoreResult, score_record
from suva.scoring.store import rules_hash
from suva.sources.base import SourceRecord
from test_scoring import RULES, rec


def rule(**overrides):
    base = {"id": "r", "field": "text", "match": {"any": ["Suva"]}, "weight": 10, "explanation": "x"}
    return base | overrides


def one_rule_set(**overrides):
    return parse_rules({"rules": [rule(**overrides)]})


# --- stored scores ---------------------------------------------------------------------------

def test_stored_scores_with_equal_score_are_listed_by_points(repo):
    for p in (400, 600, 368):
        repo.upsert_judgement(SourceRecord("opencaselaw", f"s{p}", f"k{p}", None))
    repo.save_scores([{"key": f"k{p}", "score": 100.0, "points": p, "band": "high", "fired": [], "rules_hash": "h",
                       "parser_version": None, "inputs_hash": None, "text_hash": None} for p in (400, 600, 368)])
    assert [s["points"] for s in repo.list_scores()] == [600, 400, 368]


# --- explanation -----------------------------------------------------------------------------

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
    assert [(f.id, f.points) for f in s.fired] == [("g", 0)] and s.rule_points == 0


def test_graded_rule_without_max_is_uncapped():
    r = parse_rules({"rules": [{"id": "g", "field": "text", "match": {"any": ["Unfall"]}, "per_match": 2,
                                "explanation": "x"}]}).rules[0]
    assert r.cap_count == float("inf") and r.points_for(100) == 200


# --- parse_rules validation ------------------------------------------------------------------

@pytest.mark.parametrize("data, message", [
    ({"rules": [], "scale": 0}, "'scale' is no longer used"),
    ({"rules": [], "scale": -1}, "'scale' is no longer used"),
    ({"rules": [], "threshold": "high"}, "'threshold' is no longer used"),
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
    s = score_record(rec(), rs)
    assert s.rule_points == 0 and s.display_score == -20  # base is display only


def test_rules_hash_ignores_comments_but_not_weights():
    a = parse_rules(RULES)
    b = parse_rules(RULES | {"rules": [r | {"evidence": "note"} for r in RULES["rules"]]})
    c = parse_rules(RULES | {"rules": [RULES["rules"][0] | {"weight": 41}] + RULES["rules"][1:]})
    assert rules_hash(a) == rules_hash(b) != rules_hash(c)


# --- fit ---------------------------------------------------------------------------------------

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
    return [(rec(f"8C_{i}/2025@2025-10-02", relevant=rel), ScoreResult("", s))  # s = rule points
            for i, (s, rel) in enumerate(pairs)]


def test_ranking_on_empty_and_one_class_inputs():
    empty = ranking([])
    assert empty.precision_at == {} and empty.auc == 0.0 and empty.average_precision == 0.0
    only_rel = ranking(scored((90, True)))
    assert only_rel.auc == 0.0 and only_rel.depth_for_recall[1.0] == 1


def test_ranking_ties_are_broken_pessimistically():
    r = ranking(scored((60, True), (60, False)))
    assert r.precision_at[10] == 0.5 and r.depth_for_recall[1.0] == 2 and r.auc == 0.5


def test_evaluate_and_write_scores_on_an_empty_split(tmp_path):
    ev = evaluate([], parse_rules(RULES), "test")
    assert ev.n == 0 and ev.selection.periods == 0
    out = tmp_path / "scores.csv"
    write_scores([], parse_rules(RULES), out)
    assert out.read_text(encoding="utf-8").startswith("rank,key,split,relevant_label,score,rule_points,")
