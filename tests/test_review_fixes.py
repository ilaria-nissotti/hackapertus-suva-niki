"""Regression tests for the review-app fixes: stale polling, lost approvals, crashed jobs, startup checks."""

import json
from contextlib import closing
from datetime import date
from unittest.mock import patch

import pytest

import app as dashboard
import jobs
from conftest import seed_kb
from test_review_app import SUVA_CASE

CONFIG = {"LLM_NAME": "m"}
JSON = {"Accept": "application/json"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "current_config", lambda: CONFIG)
    monkeypatch.setattr(dashboard, "model_config", lambda: CONFIG)
    monkeypatch.setattr(jobs, "pending", set())
    with patch.dict(dashboard.app.config, DATABASE=tmp_path / "review.sqlite", KB=tmp_path / "kb.sqlite",
                    TODAY=date(2026, 11, 27)):
        yield dashboard.app.test_client()


@pytest.fixture
def case(tmp_path):
    return seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)


def store_draft(key, lang="de", summary="Die Beschwerde wird abgewiesen.", approved=None):
    record = dashboard.load_record(key)
    version = jobs.generation_version(record, CONFIG)
    with closing(dashboard.connect()) as db, db:
        db.execute("INSERT INTO results VALUES (?,?,?,?,NULL,?)",
                   (key, lang, version, json.dumps({"summary": summary, "quotes": []}), approved))
    return version


# --- 1. a job finishing while the dashboard reads must not stop the browser's polling -------------

def test_draft_finishing_during_the_dashboard_read_is_never_reported_as_idle_and_empty(client, case, monkeypatch):
    jobs.pending.add((case, "de"))
    real_get_result = jobs.get_result

    def job_finishes_right_after_the_read(db, record, kind, config):
        row = real_get_result(db, record, kind, config)
        if kind == "de" and (case, "de") in jobs.pending:
            store_draft(case)  # the worker commits ...
            jobs.pending.discard((case, "de"))  # ... and then clears its flag
        return row

    monkeypatch.setattr(jobs, "get_result", job_finishes_right_after_the_read)
    de = client.get("/api/dashboard", query_string={"case": case}).json["selected"]["de"]
    assert de["active"] or de["version"]  # either keep polling or already show the draft
    assert de["active"]  # the flag was read first, so the browser polls once more


def test_briefing_finishing_during_the_dashboard_read_keeps_the_browser_polling(client, case, monkeypatch):
    store_draft(case, approved="Genehmigt.")
    monkeypatch.setattr("briefing.voice_config", lambda lang: ("key", "voice"))
    record = dashboard.load_record(case)
    with closing(dashboard.connect()) as db:
        key = dashboard.briefing_context(db, record, "de", CONFIG)[0][0]
    jobs.pending.add(("briefing", key))
    finished = []

    class FinishingDb:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            rows = self.db.execute(sql, params)
            if "FROM briefings" in sql and ("briefing", key) in jobs.pending:
                with closing(dashboard.connect()) as other, other:
                    other.execute("INSERT INTO briefings (cache_key, script, evidence, mp3) VALUES (?,?,?,?)",
                                  (key, "Skript", "[]", b"ID3"))
                jobs.pending.discard(("briefing", key))
                finished.append(1)
            return rows

    with closing(dashboard.connect()) as db:
        status = dashboard.briefing_status(FinishingDb(db), record, "de", CONFIG)
    assert finished and (status["active"] or status["ready"])


# --- 4. a crashed summary job leaves a visible error, not silence --------------------------------

def test_crashed_summary_job_shows_an_error_on_the_dashboard(client, case, monkeypatch):
    monkeypatch.setattr(jobs, "process", lambda key, kind: (_ for _ in ()).throw(RuntimeError("boom")))
    assert jobs.run_summary_job(case, "de") is False
    de = client.get("/api/dashboard", query_string={"case": case}).json["selected"]["de"]
    assert de == {"version": None, "data": {}, "error": "The summary job failed unexpectedly. Try again.",
                  "approved": None, "active": False}


def test_crashed_job_never_overwrites_a_finished_draft(client, case, monkeypatch):
    store_draft(case)
    monkeypatch.setattr(jobs, "process", lambda key, kind: (_ for _ in ()).throw(RuntimeError("boom")))
    jobs.run_summary_job(case, "de")
    de = client.get("/api/dashboard", query_string={"case": case}).json["selected"]["de"]
    assert de["version"] and de["error"] is None


def test_retry_after_a_crash_replaces_the_error(client, case, monkeypatch):
    monkeypatch.setattr(jobs, "process", lambda key, kind: (_ for _ in ()).throw(RuntimeError("boom")))
    jobs.run_summary_job(case, "facts")
    monkeypatch.undo()
    monkeypatch.setattr(jobs, "current_config", lambda: CONFIG)
    monkeypatch.setattr(jobs, "make_facts", lambda c, t: {"facts": "f"})
    assert jobs.process(case, "facts", config=CONFIG) is True


def test_crash_for_an_unknown_case_is_only_logged(client, case, monkeypatch, caplog):
    monkeypatch.setattr(jobs, "process", lambda key, kind: (_ for _ in ()).throw(RuntimeError("boom")))
    assert jobs.run_summary_job("8C_404/2026@2026-10-01", "de") is False
    assert "summary_job_crashed" in caplog.text and "summary_error_not_stored" not in caplog.text


# --- 9. summarize-top picks the same cases, in the same order, as the dashboard ------------------

def test_summarize_top_breaks_capped_score_ties_by_points_like_the_dashboard(client, tmp_path, monkeypatch):
    from suva.storage import SQLiteRepository

    keys = [seed_kb(tmp_path / "kb.sqlite", f"8C_{i}/2026", SUVA_CASE, score=100.0, band="high") for i in (1, 2, 3)]
    repo = SQLiteRepository(tmp_path / "kb.sqlite")
    repo.save_scores([repo.get_score(k) | {"points": p} for k, p in zip(keys, (400, 600, 368))])
    repo.close()
    drafted = []
    monkeypatch.setattr(jobs, "run_summary_job", lambda key, lang: drafted.append(key) or True)
    assert jobs.summarize_top(1, 1) == 0
    assert set(drafted) == {keys[1]}  # 600 points ranks first, as on the dashboard
    shown = [r["key"] for r in client.get("/api/dashboard").json["rows"]]
    assert shown[0] == keys[1]
