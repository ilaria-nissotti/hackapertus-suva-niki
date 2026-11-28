"""The review app reads judgements and rules scores from the knowledge base."""

from datetime import date
from io import BytesIO
from unittest.mock import patch

import pytest

import app as dashboard
import jobs
from conftest import seed_kb
from suva.sources.base import SourceRecord
from suva.storage import SQLiteRepository

SUVA_CASE = ("Bundesgericht\n\nUrteil vom 1. Oktober 2026\n\nVerfahrensbeteiligte\n\nA.________,\n\ngegen\n\n"
             "Suva, Fluhmattstrasse 1, 6002 Luzern,\n\nGegenstand\n\nUnfallversicherung\n\n"
             "Sachverhalt:\n\nA. Der Versicherte stürzte.\n\nErwägungen:\n\n1.\n\nGemäss Art. 6 Abs. 1 UVG ...\n\n"
             "Demnach erkennt das Bundesgericht:\n\n1.\n\nDie Beschwerde wird abgewiesen.") + " x" * 600


@pytest.fixture
def client(tmp_path):
    with patch.dict(dashboard.app.config, DATABASE=tmp_path / "review.sqlite", KB=tmp_path / "kb.sqlite",
                    TODAY=date(2026, 11, 27)):
        yield dashboard.app.test_client()


def page(client, **query):
    return client.get("/api/dashboard", query_string=query).json


def test_queue_shows_window_cases_with_rules_score_and_reasons(client, tmp_path):
    key = seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)
    seed_kb(tmp_path / "kb.sqlite", "8C_2/2025", SUVA_CASE, decided=date(2025, 11, 3))  # before the window
    data = page(client, case=key)
    assert [r["key"] for r in data["rows"]] == [key] and data["count"] == 1
    row = data["rows"][0]
    assert row["status"] == "Relevant" and row["score"] is not None and row["issue"]
    fired = data["selected"]["fired"]
    assert fired and all({"id", "points", "explanation"} <= set(f) for f in fired)
    assert data["unknown_publication"] == 1 and data["window"] == ["2026-08-27", "2026-11-27"]
    assert page(client, q="unfallversicherung")["total"] == 1  # search covers the subject


def test_score_becomes_stale_when_the_rules_change(client, tmp_path):
    key = seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)
    with patch("review_kb.current_rules_hash", return_value="other-rules"):
        data = page(client, case=key)
    assert data["rows"][0]["status"] == "Stale" and data["stale"] == 1
    assert not data["selected"]["score_current"]


def test_add_looks_up_the_kb_and_surfaces_ambiguous_dockets(client, tmp_path):
    seed_kb(tmp_path / "kb.sqlite", "8C_3/2025", SUVA_CASE, decided=date(2025, 11, 3))
    seed_kb(tmp_path / "kb.sqlite", "8C_3/2025", SUVA_CASE, decided=date(2026, 1, 20))
    headers = {"Accept": "application/json"}
    response = client.post("/add", data={"docket": "8C_3/2025"}, headers=headers)
    assert response.status_code == 400 and "2025-11-03, 2026-01-20" in response.json["message"]
    response = client.post("/add", data={"docket": "8C_3/2025", "decision_date": "2025-11-03"}, headers=headers)
    assert response.json["case"] == "8C_3/2025@2025-11-03"
    assert "8C_3/2025@2025-11-03" in [r["key"] for r in page(client)["rows"]]  # added case joins the queue
    missing = client.post("/add", data={"docket": "8C_9/2025"}, headers=headers)
    assert missing.status_code == 400 and "suva fetch" in missing.json["message"]


def test_add_upload_is_parsed_and_scored(client, tmp_path):
    seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)
    response = client.post("/add", headers={"Accept": "application/json"}, data={
        "docket": "8C_77/2026", "decision_date": "2026-09-15",
        "text": (BytesIO(SUVA_CASE.encode()), "judgment.txt")})
    key = response.json["case"]
    assert key == "8C_77/2026@2026-09-15"
    selected = page(client, case=key)["selected"]
    assert selected["status"] == "Relevant" and selected["full_text"].startswith("Bundesgericht")
    kb = SQLiteRepository(tmp_path / "kb.sqlite")
    assert kb.get_parse(key)["status"] == "ok" and kb.judgement_sources(key)[0]["source"] == "manual"
    kb.close()


def test_summaries_run_only_for_known_cases(client, tmp_path):
    seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)
    assert client.post("/run", data={"case": "8C_404/2026@2026-10-01", "action": "summarize"}).status_code == 404
    assert client.post("/run", data={"case": "8C_1/2026@2026-10-01", "action": "triage"}).status_code == 400


def test_upload_requires_usable_text_and_a_dated_identity(client, tmp_path):
    headers = {"Accept": "application/json"}

    def upload(text, **fields):
        return client.post("/add", headers=headers, data={
            "docket": "8C_1/2026", "text": (BytesIO(text.encode()), "judgment.txt"), **fields,
        })

    assert upload("", decision_date="2026-10-01").status_code == 400
    assert upload(SUVA_CASE).status_code == 400
    key = seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)
    assert upload(SUVA_CASE + "\nDifferent text", decision_date="2026-10-01").status_code == 400
    assert page(client, case=key)["selected"]["full_text"] == SUVA_CASE
    assert [row["key"] for row in page(client)["rows"]] == [key]


def test_failed_job_releases_its_slot_and_attempts_the_other_language(monkeypatch):
    key = "8C_9999/2026@2026-10-01"
    attempted = []

    def process(case, kind):
        assert case == key
        attempted.append(kind)
        if kind == "de":
            raise RuntimeError("Synthetic worker failure")

    class InlineExecutor:
        def submit(self, task):
            task()

    monkeypatch.setattr(jobs, "process", process)
    monkeypatch.setattr(jobs, "pool", InlineExecutor())
    monkeypatch.setattr(jobs, "pending", set())
    jobs.enqueue(key, ("de", "fr"))
    assert attempted == ["de", "fr"]
    assert jobs.pending == set()
    jobs.enqueue(key, ("de",))
    assert attempted == ["de", "fr", "de"]
    assert jobs.pending == set()


def test_missing_source_text_is_reported_without_a_model_call(client, tmp_path, monkeypatch):
    key = seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", "")
    monkeypatch.setattr(jobs, "model_config", lambda: {"LLM_NAME": "test-model"})

    def unexpected_call(*args):
        pytest.fail("A missing ruling should never reach Apertus")

    monkeypatch.setattr(jobs, "make_facts", unexpected_call)
    assert not jobs.process(key, "de")
    assert page(client, case=key)["selected"]["de"]["error"] == "The judgment has no usable source text."
