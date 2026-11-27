"""Integration: run `python -m suva <command>` through cli.main on a scratch KB and data dir."""

import json
from datetime import date

import pytest
import yaml

from conftest import fake_client, make_settings
from suva import cli
from suva.parse.run import parse_all
from suva.scoring.rules import load_rules
from suva.scoring.store import score_keys
from suva.sources.base import SourceRecord
from suva.storage import SQLiteRepository
from test_review_app import SUVA_CASE
from test_scoring import JUDGEMENT, RULES


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: s)
    return s


def kb(settings):
    repo = SQLiteRepository(settings.sqlite_path)
    repo.migrate()
    return repo


def seed(settings, docket, decided, text=SUVA_CASE, label=None):
    repo = kb(settings)
    repo.upsert_judgement(SourceRecord("opencaselaw", docket, docket, decided, text=text))
    if label:
        repo.upsert_known_positive(dict(ref=docket, docket=docket, court="bger", in_xlsx=1, in_exports=0))
    repo.close()


def test_init_db_applies_migrations_then_reports_up_to_date(settings, capsys):
    assert cli.main(["init-db"]) == 0
    assert "applied: 001" in capsys.readouterr().out
    assert cli.main(["init-db"]) == 0
    assert "nothing (up to date)" in capsys.readouterr().out


def test_parse_on_empty_kb_succeeds(settings, capsys):
    assert cli.main(["parse"]) == 0
    assert json.loads(capsys.readouterr().out)["judgements"] == 0


def test_score_dev_window_stores_scores(settings, capsys):
    seed(settings, "8C_1/2025", date(2025, 10, 2))
    assert cli.main(["parse"]) == 0
    assert cli.main(["score", "--window", "dev", "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert '"scored": 1' in out and "top 1 stored scores" in out and "8C_1/2025@2025-10-02" in out


def test_score_warns_about_unparsed_judgements(settings, capsys):
    seed(settings, "8C_1/2025", date(2025, 10, 2))
    assert cli.main(["score", "--window", "dev"]) == 0
    assert "1 unparsed" in capsys.readouterr().err


def test_score_prints_stored_scores_from_outside_the_window(settings, capsys):
    seed(settings, "8C_1/2025", date(2025, 10, 2))
    repo = kb(settings)
    parse_all(repo)
    score_keys(repo, ["8C_1/2025@2025-10-02"], load_rules())
    repo.close()

    assert cli.main(["score", "--window", "challenge", "--today", "2026-11-27"]) == 0
    out = capsys.readouterr().out
    assert '"scored": 0' in out
    # BUG: cmd_score lists repo.list_scores(), i.e. every stored score, under "top N stored scores",
    # so a 2025 decision shows up for the 2026 challenge window. Expected: only the scores of the
    # window just scored (here: none).
    assert "top 1 stored scores" in out and "8C_1/2025@2025-10-02" in out


def test_score_with_custom_rules_file(settings, tmp_path, capsys):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(RULES), encoding="utf-8")
    seed(settings, "8C_1/2025", date(2025, 10, 2))
    assert cli.main(["score", "--window", "dev", "--rules", str(path)]) == 0
    assert str(path) in capsys.readouterr().out


def test_bad_today_date_raises_value_error(settings):
    # Not an argparse error: an invalid --today escapes as a traceback.
    with pytest.raises(ValueError):
        cli.main(["score", "--window", "challenge", "--today", "27.11.2026"])


@pytest.mark.parametrize("argv", [
    ["score", "--window", "weekly"],
    ["coverage", "--start", "2025-10-01"],
    ["evaluate", "--split", "validation"],
    ["nonsense"],
    [],
])
def test_invalid_arguments_exit_with_usage_error(settings, argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2


def test_coverage_without_a_fetch_run_falls_back_to_history(settings, capsys):
    seed(settings, "8C_1/2025", date(2025, 10, 7))
    assert cli.main(["coverage", "--start", "2025-10-01", "--end", "2025-10-31"]) == 0
    out = capsys.readouterr().out
    assert "Historical sightings" in out and "2025-10-06" in out


def test_dataset_with_no_judgements_writes_an_empty_file_and_fails(settings, capsys):
    assert cli.main(["dataset"]) == 1
    assert (settings.data_dir / "scoring" / "dataset.jsonl").read_text(encoding="utf-8") == ""
    assert "no judgements in the period" in capsys.readouterr().err


def build_dataset(settings):
    for i in range(6):
        seed(settings, f"8C_{i}/2025", date(2025, 11, 1 + i), text=JUDGEMENT if i % 2 == 0 else
             JUDGEMENT.replace("Suva", "Helsana").replace("Unfallversicherung", "Krankenversicherung"),
             label=i % 2 == 0)
    repo = kb(settings)
    parse_all(repo)
    repo.close()
    assert cli.main(["dataset"]) == 0


def test_dataset_evaluate_fit_end_to_end(settings, tmp_path, capsys):
    build_dataset(settings)
    rows = [json.loads(line) for line in
            (settings.data_dir / "scoring" / "dataset.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6 and sum(r["relevant"] for r in rows) == 3 and {r["split"] for r in rows} == {"train"}

    assert cli.main(["evaluate"]) == 0
    ev = json.loads((settings.data_dir / "scoring" / "evaluation_train.json").read_text(encoding="utf-8"))
    assert ev["n"] == 6 and ev["n_rel"] == 3
    assert (settings.data_dir / "scoring" / "scores_train.csv").exists()

    assert cli.main(["evaluate", "--split", "test"]) == 0  # empty split still writes its outputs
    assert "test split" in capsys.readouterr().out

    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump(RULES), encoding="utf-8")
    assert cli.main(["fit-weights", "--rules", str(rules)]) == 0
    assert (tmp_path / "rules.fitted.yaml").read_text(encoding="utf-8").startswith("# Suggested weights")


def test_evaluate_with_judge_but_no_judge_points_warns(settings, tmp_path, capsys):
    build_dataset(settings)
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump(RULES), encoding="utf-8")
    assert cli.main(["evaluate", "--rules", str(rules), "--with-judge"]) == 0
    assert "needs judge.points" in capsys.readouterr().err


def test_evaluate_threshold_override(settings, capsys):
    build_dataset(settings)
    assert cli.main(["evaluate", "--threshold", "0"]) == 0
    assert "At threshold 0: flagged 6" in capsys.readouterr().out


def test_judge_uses_yaml_threshold_and_records_verdicts(settings, tmp_path, monkeypatch, capsys):
    from suva import llm

    build_dataset(settings)
    client, calls = fake_client(reply='{"relevance": "high", "reason": "Suva party."}')
    monkeypatch.setattr(llm, "_build_openai_client", lambda s: client)
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump(RULES | {"judge": {"candidate_threshold": 60, "points": {"high": 10}}}),
                     encoding="utf-8")

    assert cli.main(["judge", "--rules", str(rules)]) == 0
    out = capsys.readouterr().out
    assert "rules score >= 60" in out  # the YAML value wins over the CLI fallback of 20
    lines = (settings.data_dir / "scoring" / "judge_train.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(calls.calls) == 3 and json.loads(lines[0])["relevance"] == "high"

    assert cli.main(["judge", "--rules", str(rules), "--candidate-threshold", "101", "--limit", "1"]) == 0
    assert "candidates (rules score >= 101): 0 of 6" in capsys.readouterr().out


def test_judge_keeps_going_when_a_call_fails(settings, tmp_path, monkeypatch, capsys):
    from suva import llm

    build_dataset(settings)
    client, _ = fake_client(fail=TimeoutError("down"))
    monkeypatch.setattr(llm, "_build_openai_client", lambda s: client)
    assert cli.main(["judge", "--candidate-threshold", "0", "--limit", "2"]) == 0
    assert capsys.readouterr().err.count("FAILED") == 2


def test_import_supplied_with_missing_directory_raises(settings, tmp_path):
    with pytest.raises(ValueError):
        cli.main(["import-supplied", "--dir", str(tmp_path / "nowhere")])


def test_split_examples_on_empty_kb_gives_a_confusing_error(settings, tmp_path):
    manifest = tmp_path / "split.json"
    # BUG: with no imported examples the error reads "between 1 and -1". Expected: a message that
    # says no examples are imported yet (run `import-supplied` first).
    with pytest.raises(ValueError, match=r"n_eval must be between 1 and -1"):
        cli.main(["split-examples", "--manifest", str(manifest)])
    assert not manifest.exists()  # nothing written on failure
