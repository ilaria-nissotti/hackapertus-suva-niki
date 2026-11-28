from datetime import date

import pytest

from suva.parse.run import parse_all
from suva.scoring import analyze
from suva.scoring.dataset import build_dataset, load_dataset
from suva.scoring.evaluate import evaluate
from suva.scoring.fit import fit_weights
from suva.scoring.judge import Verdict, compare_with_judge, excerpt, judge_record, parse_verdict
from suva.scoring.rules import DEFAULT_RULES_PATH, RulesError, load_rules, parse_rules
from suva.scoring.score import score_record
from suva.sources.base import SourceRecord

RULES = {
    "threshold": 50,
    "base": -30,
    "bands": {"high": 70, "low": 10},
    "rules": [
        {"id": "suva", "field": "parties", "match": {"any": ["Suva", "CNA"]}, "weight": 40,
         "explanation": "Suva is a party."},
        {"id": "uvg", "field": "laws", "match": {"any": ["UVG"]}, "weight": 15, "explanation": "UVG cited."},
        {"id": "health", "field": "subject", "match": {"any": ["Krankenversicherung"]}, "weight": -60,
         "explanation": "Health insurance."},
    ],
}


def rec(key="8C_1/2025@2025-10-02", relevant=False, **fields):
    base = {"key": key, "docket": key.split("@")[0], "relevant": relevant, "split": "train",
            "parties": "", "subject": "", "laws": [], "chamber": "8C"}
    return base | fields


def test_shipped_rules_file_is_valid():
    ruleset = load_rules(DEFAULT_RULES_PATH)
    assert ruleset.rules and 0 <= ruleset.threshold <= 100
    assert ruleset.judge.get("rubric")


def test_text_rules_match_whole_words_case_insensitive():
    ruleset = parse_rules(RULES)
    suva = ruleset.by_id("suva")
    assert suva.matches(rec(parties="A. gegen SUVA, Luzern"))
    assert suva.matches(rec(parties="contre Caisse nationale (CNA), intimée"))
    assert not suva.matches(rec(parties="Suvarnabhumi AG"))  # not a whole word


def test_list_rules_match_exact_values():
    ruleset = parse_rules(RULES)
    assert ruleset.by_id("uvg").matches(rec(laws=["ATSG", "UVG"]))
    assert not ruleset.by_id("uvg").matches(rec(laws=["UVV"]))


def test_score_turns_points_into_a_0_100_s_curve_with_bands():
    ruleset = parse_rules(RULES)
    s = score_record(rec(parties="Suva", laws=["UVG"]), ruleset)
    assert s.points == 25 and s.score == pytest.approx(92.41, abs=0.01) and s.relevant and s.band == "high"
    assert [f.id for f in s.fired] == ["suva", "uvg"] and "Suva is a party." in s.explanation
    nothing = score_record(rec(), ruleset)
    assert nothing.points == -30 and nothing.score == pytest.approx(4.74, abs=0.01) and nothing.band == "low"
    low = score_record(rec(subject="Krankenversicherung"), ruleset)
    assert 0 < low.score < nothing.score  # never capped: more negative evidence, lower score
    middle = score_record(rec(parties="Suva"), ruleset)
    assert middle.score == pytest.approx(73.11, abs=0.01) and middle.band == "high"
    assert score_record(rec(laws=["UVG"]), ruleset).band == "check"


def test_graded_rule_counts_matches_up_to_its_cap():
    ruleset = parse_rules(RULES | {"rules": [
        {"id": "uvg_articles", "field": "articles", "match": {"regex": " UVG$"}, "per_match": 4, "max": 10,
         "explanation": "UVG articles cited."}]})
    rule = ruleset.by_id("uvg_articles")
    assert rule.count(rec(articles=["Art. 6 UVG", "Art. 4 ATSG"])) == 1
    assert score_record(rec(articles=["Art. 6 UVG", "Art. 4 ATSG"]), ruleset).points == -26
    many = score_record(rec(articles=[f"Art. {i} UVG" for i in range(5)]), ruleset)
    assert many.points == -20 and "(5×)" in many.explanation  # capped at max 10
    assert rule.feature(rec(articles=[f"Art. {i} UVG" for i in range(5)])) == 2.5
    text_rule = parse_rules(RULES | {"rules": [
        {"id": "unfall", "field": "considerations", "match": {"any": ["Unfall"]}, "per_match": 1,
         "explanation": "x"}]}).rules[0]
    assert text_rule.count(rec(considerations="Unfall ... Unfall ... Unfallversicherung")) == 2


def test_judge_points_join_the_score():
    ruleset = parse_rules(RULES | {"judge": {"points": {"high": 20, "low": -20}}})
    base = score_record(rec(parties="Suva"), ruleset)
    high = score_record(rec(parties="Suva"), ruleset, judge="high", judge_reason="New case law.")
    assert high.points == base.points + 20 and "Apertus judge: high (New case law.)" in high.explanation
    assert score_record(rec(parties="Suva"), ruleset, judge="medium").points == base.points  # no points set


@pytest.mark.parametrize("broken, message", [
    ({"rules": [{"id": "x"}]}, "missing 'field'"),
    ({"rules": [{"id": "x", "field": "nowhere", "match": {"any": ["a"]}, "weight": 1, "explanation": "e"}]},
     "field must be one of"),
    ({"rules": [{"id": "x", "field": "subject", "match": {"some": ["a"]}, "weight": 1, "explanation": "e"}]},
     "match needs"),
    ({"rules": [RULES["rules"][0], RULES["rules"][0]]}, "duplicate rule id"),
    ({"rules": [{"id": "x", "field": "subject", "match": {"any": ["a"]}, "explanation": "e"}]},
     "either 'weight'"),
    ({"rules": [{"id": "x", "field": "subject", "match": {"any": ["a"]}, "weight": 1, "max": 3,
                 "explanation": "e"}]}, "'max' only applies"),
    ({"rules": [], "bands": {"high": 20, "low": 40}}, "bands needs"),
    ({"rules": [], "judge": {"points": {"great": 5}}}, "judge.points"),
])
def test_invalid_rules_are_rejected_with_a_clear_message(broken, message):
    with pytest.raises(RulesError, match=message):
        parse_rules(broken)


def test_evaluate_counts_precision_recall_and_evidence():
    ruleset = parse_rules(RULES)
    records = [
        rec("a@1", True, parties="Suva"),
        rec("b@1", True, parties="X"),  # missed
        rec("c@1", False, parties="Suva"),  # false alarm
        rec("d@1", False, parties="Y"),
    ]
    ev = evaluate(records, ruleset, "train")
    m = ev.at_threshold
    assert (m.true_pos, m.false_pos, m.false_neg) == (1, 1, 1)
    assert m.precision == 0.5 and m.recall == 0.5
    assert [c["key"] for c in ev.misses] == ["b@1"] and [c["key"] for c in ev.false_alarms] == ["c@1"]
    suva = next(e for e in ev.evidence if e.id == "suva")
    assert suva.text == "fires in 1/2 relevant, 1/2 not relevant"
    assert ev.bands == {"high": [1, 1], "check": [0, 0], "low": [1, 1]}


def test_ranking_metrics_judge_the_sorted_list():
    from suva.scoring.evaluate import ranking, score_all

    ruleset = parse_rules(RULES)
    records = [rec("a@1", True, parties="Suva", laws=["UVG"]),  # 92.4
               rec("b@1", False, parties="Suva"),  # 73.1
               rec("c@1", True, parties="Suva"),  # 73.1, tie with b: counted after b (pessimistic)
               rec("d@1", False), rec("e@1", True)]  # 4.7 each
    rk = ranking(score_all(records, ruleset))
    assert rk.precision_at[10] == 3 / 5
    assert rk.depth_for_recall == {0.9: 5, 0.95: 5, 1.0: 5}
    assert rk.auc == pytest.approx((2 + 1.5 + 0.5) / 6)
    assert rk.average_precision == pytest.approx((1 / 1 + 2 / 3 + 3 / 5) / 3)


def test_load_verdicts_skips_unusable_answers(tmp_path):
    from suva.scoring.evaluate import load_verdicts

    path = tmp_path / "judge.jsonl"
    path.write_text('{"key": "a", "relevance": "high", "reason": "r"}\n{"key": "b", "relevance": null}\n',
                    encoding="utf-8")
    assert load_verdicts(path) == {"a": ("high", "r")}
    assert load_verdicts(tmp_path / "missing.jsonl") == {}


def test_feature_stats_find_separating_features():
    records = [rec(f"r{i}@1", True, subject="Unfallversicherung", laws=["UVG"]) for i in range(5)]
    records += [rec(f"n{i}@1", False, subject="Krankenversicherung", laws=["KVG"]) for i in range(20)]
    stats, n_rel, n_non = analyze.feature_stats(records)
    assert (n_rel, n_non) == (5, 20)
    top = analyze.positive_candidates(stats, min_rel=3, top=5)
    assert {(s.field, s.value) for s in top} >= {("subject", "unfallversicherung"), ("laws", "UVG")}
    bottom = analyze.negative_candidates(stats, min_non=10, top=5)
    assert ("laws", "KVG") in {(s.field, s.value) for s in bottom}
    assert analyze.suva_check([rec(parties="Suva", relevant=True), rec(parties="IV-Stelle")]) == {
        "suva_party": 1, "suva_party_relevant": 1, "relevant_total": 1, "relevant_without_suva_party": 0}


def test_fit_gives_positive_weight_to_a_separating_rule():
    ruleset = parse_rules(RULES)
    records = [rec(f"r{i}@1", True, parties="Suva") for i in range(10)]
    records += [rec(f"n{i}@1", False, parties="X") for i in range(40)]
    records += [rec("n_suva@1", False, parties="Suva")]
    fitted = fit_weights(records, ruleset)
    assert fitted.weights["suva"] > 10
    assert fitted.weights["uvg"] == 0  # never fires: no evidence either way


JUDGEMENT = ("Bundesgericht\n\nUrteil vom 2. Oktober 2025\n\nVerfahrensbeteiligte\n\nA.________,\n\ngegen\n\n"
             "Suva, Fluhmattstrasse 1, 6002 Luzern,\n\nGegenstand\n\nUnfallversicherung\n\n"
             "Sachverhalt:\n\nA. Der Versicherte stürzte.\n\nErwägungen:\n\n1.\n\nGemäss Art. 6 Abs. 1 UVG ...\n\n"
             "Demnach erkennt das Bundesgericht:\n\n1.\n\nDie Beschwerde wird abgewiesen.") + " x" * 600


def add(repo, docket, decision, text):
    repo.upsert_judgement(SourceRecord(source="opencaselaw", source_id=docket, docket=docket,
                                       decision_date=decision, text=text))


def test_build_dataset_labels_from_excel_only_and_splits_by_date(repo, tmp_path):
    add(repo, "8C_1/2025", date(2025, 11, 3), JUDGEMENT)  # in Excel -> relevant, train
    add(repo, "8C_2/2025", date(2026, 3, 10), JUDGEMENT)  # not in Excel, test
    add(repo, "8C_3/2025", date(2025, 12, 1), JUDGEMENT)  # summary only -> NOT relevant
    add(repo, "8C_4/2025", date(2025, 6, 1), JUDGEMENT)  # outside the period
    repo.upsert_known_positive(dict(ref="8C_1/2025", docket="8C_1/2025", court="bger", in_xlsx=1, in_exports=0))
    repo.upsert_known_positive(dict(ref="8C_3/2025", docket="8C_3/2025", court="bger", in_xlsx=0, in_exports=1))
    repo.upsert_known_positive(dict(ref="8C_9/2025", docket="8C_9/2025", court="bger", in_xlsx=1, in_exports=0))
    parse_all(repo)

    out = tmp_path / "dataset.jsonl"
    rep = build_dataset(repo, out)
    assert rep.records == 3 and rep.relevant == 1
    assert rep.by_split == {"test_not_relevant": 1, "train_not_relevant": 1, "train_relevant": 1}
    assert rep.picks_not_in_dataset == ["8C_9/2025"]

    first = next(r for r in load_dataset(out) if r["docket"] == "8C_1/2025")
    assert first["relevant"] and first["split"] == "train" and first["chamber"] == "8C"
    assert "Suva" in first["parties"] and first["subject"] == "Unfallversicherung"
    assert "UVG" in first["laws"] and "Art. 6 Abs. 1 UVG" in first["articles"]
    assert "abgewiesen" in first["dispositiv"] and "stürzte" in first["facts"]
    assert len(load_dataset(out, "test")) == 1


def test_dated_pick_labels_only_that_decision(repo, tmp_path):
    add(repo, "8C_5/2025", date(2025, 11, 3), JUDGEMENT)
    add(repo, "8C_5/2025", date(2026, 1, 20), JUDGEMENT)  # same docket, later decision
    repo.upsert_known_positive(dict(ref="8C_5/2025@2025-11-03", docket="8C_5/2025", court="bger",
                                    in_xlsx=1, in_exports=0))
    out = tmp_path / "dataset.jsonl"
    rep = build_dataset(repo, out)
    assert rep.relevant == 1 and rep.picks_ambiguous == [] and rep.picks_not_in_dataset == []
    assert {r["key"]: r["relevant"] for r in load_dataset(out)} == {
        "8C_5/2025@2025-11-03": True, "8C_5/2025@2026-01-20": False}


def test_docket_only_pick_with_several_decisions_is_ambiguous(repo, tmp_path):
    add(repo, "8C_6/2025", date(2025, 11, 3), JUDGEMENT)
    add(repo, "8C_6/2025", date(2026, 1, 20), JUDGEMENT)
    repo.upsert_known_positive(dict(ref="8C_6/2025", docket="8C_6/2025", court="bger", in_xlsx=1, in_exports=0))
    rep = build_dataset(repo, tmp_path / "dataset.jsonl")
    assert rep.relevant == 0 and rep.picks_ambiguous == ["8C_6/2025"] and rep.picks_not_in_dataset == []


def test_bge_headnote_becomes_the_regeste_field(repo, tmp_path):
    from test_segment import BGE_DE

    add(repo, "9C_480/2022", date(2025, 11, 3), BGE_DE)
    parse_all(repo)
    out = tmp_path / "dataset.jsonl"
    build_dataset(repo, out)
    record = load_dataset(out)[0]
    assert "Kongruenz der Leistungen" in record["regeste"] and "Kongruenz" not in record["header"]
    rule = parse_rules({"rules": [{"id": "atsg_69", "field": "regeste", "match": {"any": ["Art. 69 Abs. 1 ATSG"]},
                                   "weight": 5, "explanation": "x"}]}).rules[0]
    assert rule.matches(record)
    shown = excerpt(record)
    assert "Headnote (leading decision): Regeste" in shown and "Kongruenz der Leistungen" in shown
    assert "Headnote" not in excerpt(rec())  # ordinary judgements: no empty headnote line


def test_dataset_reports_parses_from_an_older_parser(repo, tmp_path):
    add(repo, "8C_7/2025", date(2025, 11, 3), JUDGEMENT)
    parse_all(repo)
    out = tmp_path / "dataset.jsonl"
    assert build_dataset(repo, out).stale_parses == 0
    repo.conn.execute("UPDATE parses SET parser_version = 'segment-v0'")
    rep = build_dataset(repo, out)
    assert rep.stale_parses == 1 and load_dataset(out)[0]["parse_stale"]


def test_decisions_of_one_docket_share_a_split(repo, tmp_path):
    add(repo, "8C_8/2025", date(2026, 2, 10), JUDGEMENT)  # train by date
    add(repo, "8C_8/2025", date(2026, 3, 12), JUDGEMENT)  # test by date, follows its docket
    add(repo, "8C_9/2026", date(2026, 3, 5), JUDGEMENT)
    out = tmp_path / "dataset.jsonl"
    build_dataset(repo, out)
    assert {r["key"]: r["split"] for r in load_dataset(out)} == {
        "8C_8/2025@2026-02-10": "train", "8C_8/2025@2026-03-12": "train", "8C_9/2026@2026-03-05": "test"}


def test_parse_verdict_accepts_json_inside_text_and_rejects_bad_answers():
    assert parse_verdict('Sure: {"relevance": "High", "reason": "UVG causal link."}') == (
        "high", "UVG causal link.", None)
    assert parse_verdict("no idea")[2] == "no_json"
    assert parse_verdict('{"relevance": "maybe", "reason": "x"}')[2] == "invalid_relevance"


def test_judge_record_uses_rubric_and_is_cached(tmp_path, repo):
    from conftest import fake_client, make_settings
    from suva.llm import LLMClient

    client, calls = fake_client(reply='{"relevance": "medium", "reason": "Accident insurance benefits."}')
    llm = LLMClient(make_settings(tmp_path), repo=repo, client=client)
    ruleset = parse_rules(RULES | {"judge": {"rubric": "RUBRIC TEXT"}})
    record = rec(parties="Suva", subject="Unfallversicherung", facts="Sturz", dispositiv="abgewiesen")
    v = judge_record(llm, record, ruleset, 40)
    assert v.relevance == "medium" and v.relevant and not v.cached
    assert calls.calls[0]["messages"][0]["content"].startswith("RUBRIC TEXT")
    assert judge_record(llm, record, ruleset, 40).cached and len(calls.calls) == 1


def test_score_window_stores_scores_with_explanations(repo):
    from suva.scoring.store import score_window
    from suva.windows import challenge_window

    add(repo, "8C_10/2026", date(2026, 10, 1), JUDGEMENT)  # publication unknown: kept, counted
    add(repo, "8C_11/2026", date(2025, 1, 1), JUDGEMENT)  # decided before the window
    parse_all(repo)
    ruleset = parse_rules(RULES)
    rep = score_window(repo, challenge_window(date(2026, 11, 27)), ruleset)
    assert rep.scored == 1 and rep.unknown_publication == 1 and rep.not_parsed == 0

    stored = repo.get_score("8C_10/2026@2026-10-01")
    assert stored["band"] == "high" and [f["id"] for f in stored["fired"]] == ["suva", "uvg"]
    assert stored["fired"][0]["explanation"] == "Suva is a party."
    assert repo.get_score("8C_11/2026@2025-01-01") is None


def test_compare_with_judge_counts_recall_over_all_relevant_judgements():
    from types import SimpleNamespace

    a, b, c, missed = ({"key": k, "relevant": rel} for k, rel in (("a", 1), ("b", 0), ("c", 1), ("missed", 1)))
    candidates = [(a, SimpleNamespace(score=80)), (b, SimpleNamespace(score=60)), (c, SimpleNamespace(score=20))]
    verdicts = [(a, Verdict("a", "low", "", 80, False)), (b, Verdict("b", "high", "", 60, False)),
                (c, Verdict("c", "medium", "", 20, False)), (missed, Verdict("missed", None, "", 10, False, "bad"))]
    rows = {r["name"]: r for r in compare_with_judge([a, b, c, missed], candidates, verdicts, threshold=50)}
    assert rows["rules only"] == {"name": "rules only", "flagged": 2, "precision": 0.5, "recall": 1 / 3}
    assert (rows["judge only (high/medium)"]["flagged"], rows["judge only (high/medium)"]["precision"]) == (2, 0.5)
    assert rows["rules AND judge"]["flagged"] == 1 and rows["rules AND judge"]["precision"] == 0.0
    assert rows["rules OR judge"]["flagged"] == 3 and rows["rules OR judge"]["recall"] == 2 / 3
    assert compare_with_judge([], [], [], 50)[0] == {"name": "rules only", "flagged": 0, "precision": 0.0, "recall": 0.0}
