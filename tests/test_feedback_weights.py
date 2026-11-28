"""The feedback loop: reviewer verdicts -> proposed rule weights -> the expert accepts -> rules.yaml -> rescore."""

import json
import shutil
from contextlib import closing
from types import SimpleNamespace

import pytest

import app as dashboard
import jobs
import review_store
from suva.scoring import feedback
from suva.scoring import rules as rules_module
from suva.scoring.feedback import (change_label, held_out_points, training_set, updated_rules_text,
                                   verdict_folds, with_count_cache)
from suva.scoring.rules import load_rules, parse_rules
from suva.scoring.store import rules_hash
from test_flow_review_app import case, client, page  # noqa: F401

RULES = """\
# A comment the experts rely on
base: -50   # display only

selection:
  top_n: 2
  floor_rule_points: 10   # keeps 90 %
  per: month

rules:
  - id: accident
    field: subject
    match: {any: ["Unfallversicherung"]}
    weight: 40   # the main topic
    explanation: "Accident insurance."
  - id: uvg
    field: laws
    match: {any: ["UVG"]}
    per_match: 5
    max: 15
    explanation: "Cites the UVG."
  - id: costs
    field: subject
    match: {any: ["Kosten"]}
    weight: -10
    explanation: "Only about costs."

judge:
  candidate_floor_rule_points: 10
"""


def record(key, relevant, split="train", subject="Unfallversicherung", laws=("UVG",), date="2025-11-03"):
    docket = key.partition("@")[0]
    return {"key": key, "docket": docket, "decision_date": date, "relevant": relevant, "split": split,
            "subject": subject, "laws": list(laws), "parties": "", "dispositiv": "", "considerations": ""}


def dataset():
    """Accident cases are often picks, cost cases never: enough signal for the regression."""
    out = []
    for i in range(40):
        accident = i % 2 == 0
        out.append(record(f"8C_{i}/2025@2025-11-{1 + i % 28:02d}", relevant=accident and i % 4 == 0,
                          subject="Unfallversicherung" if accident else "Kosten", laws=("UVG",) if accident else ()))
    out.append(record("8C_900/2026@2026-03-05", True, split="test"))
    return out


# --- labels ----------------------------------------------------------------------------------------------

def test_verdicts_override_the_spreadsheet_add_new_cases_and_never_touch_the_test_split():
    data = dataset()
    outside = record("8C_500/2026@2026-10-01", None, split=None)
    same_docket_as_test = record("8C_900/2026@2026-09-01", None, split=None)
    lookup = {r["key"]: r for r in (outside, same_docket_as_test)}.get
    verdicts = {"8C_0/2025@2025-11-01": False,  # a spreadsheet pick the expert rejects
                "8C_500/2026@2026-10-01": True,  # a new case
                "8C_900/2026@2026-03-05": False,  # in the test split
                "8C_900/2026@2026-09-01": True,  # another decision of a test docket
                "8C_404/2026@2026-10-01": True}  # gone from the knowledge base
    records, report = training_set(data, verdicts, lookup)
    by_key = {r["key"]: r for r in records}
    assert by_key["8C_0/2025@2025-11-01"]["relevant"] is False
    assert by_key["8C_0/2025@2025-11-01"]["spreadsheet"] is True
    assert by_key["8C_500/2026@2026-10-01"]["relevant"] is True
    assert by_key["8C_500/2026@2026-10-01"]["spreadsheet"] is None
    assert not any(r["docket"] == "8C_900/2026" for r in records)
    assert report == {"used": 2, "used_relevant": 1, "overrides": 1, "added": 1, "test_split": 2, "missing": 1}


def test_verdict_folds_keep_a_docket_together():
    records = [record("8C_1/2026@2026-10-01", True) | {"verdict": True},
               record("8C_1/2026@2026-10-09", False) | {"verdict": False},
               record("8C_2/2026@2026-10-01", True) | {"verdict": True},
               record("8C_3/2025@2025-11-01", False) | {"verdict": None}]
    folds = verdict_folds(records)
    assert set(folds) == {"8C_1/2026", "8C_2/2026"}  # only dockets with a verdict are held out


def test_held_out_points_score_each_verdict_with_weights_fitted_without_it(monkeypatch):
    ruleset = with_count_cache(parse_rules(__import__("yaml").safe_load(RULES)))
    verdicts = {"8C_2/2025@2025-11-03": True, "8C_5/2025@2025-11-06": True}
    records, _ = training_set(dataset(), verdicts, lambda key: None)
    fitted_on = []
    real_fit = feedback.fit_weights
    monkeypatch.setattr(feedback, "fit_weights", lambda data, *a, **k: fitted_on.append(
        {r["docket"] for r in data}) or real_fit(data, *a, **k))
    points = held_out_points(records, ruleset)
    assert set(points) == set(verdicts)
    assert len(fitted_on) == 2  # one fit per verdict docket (2 dockets -> 2 folds)
    for docket in ("8C_2/2025", "8C_5/2025"):
        assert sum(docket not in seen for seen in fitted_on) == 1  # left out of exactly the fit that scores it


@pytest.mark.parametrize("current, proposed, label", [
    (10, 10, "No change"), (10, 20, "Counts more"), (10, 4, "Counts less"), (-5, -9, "Counts more against"),
    (-9, -5, "Counts less against"),
    (10, -3, "Now counts against"), (-5, 2, "Now counts in favour"), (0, 4, "Now counts in favour"),
    (7, 0, "No longer counts"),
])
def test_changes_are_described_in_plain_words(current, proposed, label):
    assert change_label(current, proposed) == label


# --- writing rules.yaml ----------------------------------------------------------------------------------

def rules_file(tmp_path, text=RULES):
    path = tmp_path / "rules.yaml"
    path.write_bytes(text.encode())
    return path


def test_accepted_weights_change_only_the_numbers_and_keep_the_comments(tmp_path):
    path = rules_file(tmp_path)
    ruleset = load_rules(path)
    feedback.write_weights(path, ruleset, {"accident": 52, "uvg": 3, "costs": -14}, -61, 8, verdicts=31)
    text = path.read_text(encoding="utf-8")
    new = load_rules(path)
    assert [new.by_id("accident").weight, new.by_id("costs").weight] == [52, -14]
    assert (new.by_id("uvg").per_match, new.by_id("uvg").max) == (3, 9)  # the cap stays at 3 citations
    assert (new.base, new.selection.floor_rule_points, new.judge_floor_rule_points) == (-61, 8, 8)
    for kept in ("# A comment the experts rely on", "weight: 52   # the main topic", "floor_rule_points: 8   # keeps 90 %"):
        assert kept in text
    assert text.count(feedback.MARKER) == 1 and "from 31 expert verdicts" in text
    feedback.write_weights(path, new, {"accident": 50, "uvg": 3, "costs": -14}, -61, 8, verdicts=40)
    assert path.read_text(encoding="utf-8").count(feedback.MARKER) == 1  # replaced, not stacked


def test_windows_line_endings_are_kept(tmp_path):
    path = rules_file(tmp_path, RULES.replace("\n", "\r\n"))
    feedback.write_weights(path, load_rules(path), {"accident": 41, "uvg": 5, "costs": -10}, -50, 10, verdicts=1)
    raw = path.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")


def test_a_judge_floor_that_differs_from_the_selection_floor_is_left_alone(tmp_path):
    path = rules_file(tmp_path, RULES.replace("candidate_floor_rule_points: 10", "candidate_floor_rule_points: 25"))
    feedback.write_weights(path, load_rules(path), {"accident": 41, "uvg": 5, "costs": -10}, -50, 7, verdicts=1)
    assert load_rules(path).judge_floor_rule_points == 25


def test_a_rules_file_without_a_floor_is_refused(tmp_path):
    text = RULES.replace("  floor_rule_points: 10   # keeps 90 %\n", "")
    ruleset = parse_rules(__import__("yaml").safe_load(text))
    with pytest.raises(ValueError, match="floor_rule_points"):
        updated_rules_text(text, ruleset, {"accident": 41, "uvg": 5, "costs": -10}, -50, 7, "note")


# --- the review app --------------------------------------------------------------------------------------

@pytest.fixture
def weights_env(tmp_path, monkeypatch, client, case):
    """A copy of the real rules.yaml, a small labelled dataset and an inline job pool."""
    rules_path = tmp_path / "rules" / "rules.yaml"
    rules_path.parent.mkdir()
    shutil.copy(rules_module.DEFAULT_RULES_PATH, rules_path)
    monkeypatch.setattr(rules_module, "DEFAULT_RULES_PATH", rules_path)
    data_path = tmp_path / "dataset.jsonl"
    data_path.write_text("\n".join(json.dumps(r) for r in real_rules_dataset()), encoding="utf-8")
    monkeypatch.setitem(dashboard.app.config, "DATASET", data_path)
    tasks = []
    monkeypatch.setattr(jobs, "pool", SimpleNamespace(submit=tasks.append))
    return SimpleNamespace(rules=rules_path, tasks=tasks, client=client, case=case)


def real_rules_dataset():
    """Training records the real rules can tell apart (accident cases with Suva are often picks)."""
    out = []
    for i in range(60):
        accident = i % 2 == 0
        out.append(record(f"8C_{i}/2025@2025-11-{1 + i % 28:02d}", relevant=accident and i % 4 == 0,
                          subject="Unfallversicherung" if accident else "Krankenversicherung",
                          laws=("UVG",) if accident else ("KVG",)) | {"chamber": "8C" if accident else "9C",
                                                                       "parties": "Suva" if accident else ""})
    return out


def run_jobs(env):
    while env.tasks:
        env.tasks.pop(0)()


def test_reviewers_verdicts_retrain_the_weights_once_the_expert_accepts(weights_env):
    env, client = weights_env, weights_env.client
    assert client.post("/api/weights/propose").status_code == 400  # nothing to learn from yet
    client.post("/api/triage-feedback", json={"case": env.case, "status": "Relevant"})
    assert page(client)["feedback"]["new"] == 1

    assert client.post("/api/weights/propose").status_code == 202
    status = client.get("/api/weights").json
    assert status["computing"] and status["proposal"]["status"] == "computing"
    run_jobs(env)
    status = client.get("/api/weights").json
    proposal = status["proposal"]
    assert not status["computing"] and proposal["status"] == "pending", proposal["error"]
    data = proposal["data"]
    assert data["verdicts"]["used"] == 1 and data["agreement"]["verdicts"] == 1
    assert {row["id"] for row in data["weights"]} == {r.id for r in load_rules().rules}
    assert data["verdict_cases"][0]["key"] == env.case

    original = env.rules.read_bytes()
    env.rules.write_bytes(original.replace(b"base: -100", b"base: -101"))  # an expert edits the file meanwhile
    response = client.post("/api/weights/accept", json={"id": proposal["id"]})
    assert response.status_code == 409 and "changed" in response.json["message"]
    env.rules.write_bytes(original)

    assert client.post("/api/weights/accept", json={"id": proposal["id"]}).status_code == 200
    updated = load_rules()
    assert {r.id: (r.per_match if r.graded else r.weight) for r in updated.rules} == data["proposed_weights"]
    assert updated.selection.floor_rule_points == data["floor"]["proposed"]
    assert b"# Relevance rules for Federal Supreme Court judgements" in env.rules.read_bytes()
    assert page(client, case=env.case)["selected"]["status"] == "Stale"  # until the rescore ran
    run_jobs(env)
    assert page(client, case=env.case)["selected"]["score_current"]

    status = client.get("/api/weights").json
    assert status["proposal"] is None and len(status["history"]) == 1
    assert status["verdicts"]["new"] == 0 and status["rescore"]["error"] is None
    assert client.post("/api/weights/accept", json={"id": proposal["id"]}).status_code == 409  # decided once


def test_a_discarded_proposal_leaves_the_rules_alone(weights_env):
    env, client = weights_env, weights_env.client
    client.post("/api/triage-feedback", json={"case": env.case, "status": "Not relevant"})
    client.post("/api/weights/propose")
    assert client.post("/api/weights/propose").status_code == 409  # one calculation at a time
    run_jobs(env)
    before = env.rules.read_bytes()
    proposal_id = client.get("/api/weights").json["proposal"]["id"]
    assert client.post("/api/weights/discard", json={"id": proposal_id}).status_code == 200
    assert env.rules.read_bytes() == before
    assert client.get("/api/weights").json["proposal"] is None
    assert client.post("/api/weights/discard", json={"id": proposal_id}).status_code == 409


def test_a_failed_calculation_is_shown_with_its_error(weights_env, monkeypatch):
    env, client = weights_env, weights_env.client
    client.post("/api/triage-feedback", json={"case": env.case, "status": "Relevant"})
    monkeypatch.setitem(dashboard.app.config, "DATASET", env.rules.parent / "missing" / "dataset.jsonl")
    monkeypatch.setattr(feedback, "build_dataset", lambda repo, path: (_ for _ in ()).throw(OSError("no data")))
    client.post("/api/weights/propose")
    run_jobs(env)
    proposal = client.get("/api/weights").json["proposal"]
    assert proposal["status"] == "failed" and "no data" in proposal["error"]
    assert client.post("/api/weights/accept", json={"id": proposal["id"]}).status_code == 409


def test_a_calculation_interrupted_by_a_restart_is_marked_failed(tmp_path):
    with closing(review_store.connect(tmp_path / "review.sqlite")) as db:
        proposal_id = review_store.start_proposal(db)
        review_store.interrupt_computing(db)
        assert review_store.get_proposal(db, proposal_id)["status"] == "failed"


def test_the_rules_hash_of_a_proposal_matches_the_rules_it_started_from(tmp_path):
    path = rules_file(tmp_path)
    ruleset = load_rules(path)
    assert rules_hash(with_count_cache(ruleset)) == rules_hash(ruleset)
