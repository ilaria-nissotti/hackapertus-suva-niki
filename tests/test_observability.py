"""Failures are logged with operation, IDs and reason; secrets, prompts and texts never are."""

import logging
from contextlib import closing
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

import httpx
import pytest

import app as dashboard
import briefing
import summaries
from conftest import fake_client, make_settings, seed_kb
from suva.config import configure_logging
from suva.fetch import fetch
from suva.http import PoliteClient
from suva.llm import LLMClient
from test_flow_pipeline import DEV, write_parquet
from test_review_app import SUVA_CASE

CONFIG = {"LLM_NAME": "m"}
JSON = {"Accept": "application/json"}
SECRET = "sk-test-secret-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "current_config", lambda: CONFIG)
    monkeypatch.setattr(dashboard, "model_config", lambda: CONFIG)
    with patch.dict(dashboard.app.config, DATABASE=tmp_path / "review.sqlite", KB=tmp_path / "kb.sqlite",
                    TODAY=date(2026, 11, 27)):
        yield dashboard.app.test_client()


@pytest.fixture
def case(tmp_path):
    return seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)


def messages(caplog, logger=None):
    return [r.getMessage() for r in caplog.records if logger is None or r.name == logger]


def add_approved_draft(key):
    record = dashboard.load_record(key)
    with closing(dashboard.connect()) as db, db:
        db.execute("INSERT INTO results VALUES (?,?,?,?,NULL,?)",
                   (key, "de", dashboard.generation_version(record, CONFIG), '{"summary": "s"}', "Genehmigt."))


def test_crashed_background_briefing_is_logged_and_shown_as_an_error(client, case, monkeypatch, caplog):
    add_approved_draft(case)
    monkeypatch.setattr(briefing, "voice_config", lambda lang: (SECRET, "voice"))
    tasks = []
    monkeypatch.setattr(dashboard, "pool", SimpleNamespace(submit=tasks.append))
    monkeypatch.setattr(dashboard, "pending", set())
    monkeypatch.setattr(briefing, "generate", lambda *a: (_ for _ in ()).throw(RuntimeError("lock dir gone")))
    with caplog.at_level(logging.INFO):
        assert client.post("/briefing", headers=JSON, data={"case": case, "lang": "de"}).status_code == 202
        tasks.pop()()  # run the job as the pool thread would, after the request released pending_lock
    crash = [m for m in messages(caplog) if m.startswith("briefing_job_crashed")]
    assert crash and f"case={case}" in crash[0] and "error=RuntimeError" in crash[0]
    audio = client.get("/api/dashboard", query_string={"case": case}).json["audio"]
    assert audio["error"] == "Briefing failed unexpectedly. Try again." and audio["active"] is False
    assert SECRET not in caplog.text


def test_briefing_provider_failure_is_logged_with_stage_and_status(client, case, caplog):
    record = dashboard.load_record(case)
    args = (dashboard.connect, dashboard.app.config["DATABASE"], "k" * 64, record, "Genehmigt.", "de", CONFIG,
            SECRET, "voice")
    with patch("briefing.write_script", return_value=("Skript", ["Zeile"])), \
            patch("briefing.narrate", side_effect=HTTPError("https://api.elevenlabs.io", 429, "Busy", {}, None)), \
            caplog.at_level(logging.INFO):
        assert briefing.generate(*args) is False
    line, = [m for m in messages(caplog, "briefing") if m.startswith("briefing_failed")]
    assert f"case={case}" in line and "stage=ElevenLabs" in line and "http=429" in line
    assert SECRET not in caplog.text and "Skript" not in caplog.text


def test_failed_summary_is_logged_without_the_judgment_text(client, case, monkeypatch, caplog):
    monkeypatch.setattr(dashboard, "make_facts", lambda c, t: (_ for _ in ()).throw(OSError("Apertus HTTP 503")))
    with caplog.at_level(logging.INFO):
        assert dashboard.process(case, "facts", config=CONFIG) is False
    line, = [m for m in messages(caplog) if m.startswith("summary_failed")]
    assert f"case={case}" in line and "kind=facts" in line and "error=OSError" in line
    assert "Der Versicherte" not in caplog.text


def test_ready_summary_and_approval_are_logged(client, case, monkeypatch, caplog):
    monkeypatch.setattr(dashboard, "make_facts", lambda c, t: {"facts": "f"})
    with caplog.at_level(logging.INFO):
        dashboard.process(case, "facts", config=CONFIG)
        client.post("/approve", headers=JSON, data={"case": case, "lang": "de", "summary": "Text", "version": "v"})
    assert f"summary_ready case={case} kind=facts model=m" in messages(caplog)
    assert f"approve_conflict case={case} lang=de reason=no_draft" in messages(caplog)


def test_add_case_outcomes_are_logged(client, case, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/add", headers=JSON, data={"docket": "not a docket"})
        client.post("/add", headers=JSON, data={"docket": "8C_1/2026"})
    assert any(m.startswith("add_case_rejected docket='not a docket'") for m in messages(caplog))
    assert f"case_added case={case} upload=False" in messages(caplog)


def test_conversation_failure_logs_action_and_case_but_never_the_question_or_key(client, case, caplog):
    question = "Hat meine Mandantin Anspruch auf eine Rente?"
    with patch("conversation.model_config", return_value=CONFIG), \
            patch("conversation.ask", side_effect=OSError("Apertus request failed")), caplog.at_level(logging.INFO):
        response = client.post("/conversation/ask", json={"case": case, "lang": "de", "question": question})
    assert response.status_code == 502
    line, = [m for m in messages(caplog, "conversation") if m.startswith("conversation_provider_failed")]
    assert line == f"conversation_provider_failed action=ask case={case} provider=Apertus error=OSError"
    assert question not in caplog.text


def test_transcription_failure_never_logs_the_api_key(client, case, caplog):
    with patch("conversation.voice_config", return_value=(SECRET, "voice")), \
            patch("conversation.urlopen", side_effect=HTTPError("https://api.elevenlabs.io", 401, "No", {}, None)), \
            caplog.at_level(logging.INFO):
        client.post("/conversation/transcribe", data={"case": case, "lang": "de",
                                                      "audio": (BytesIO(b"rec"), "clip", "audio/webm")})
    assert "provider=ElevenLabs http=401" in caplog.text and SECRET not in caplog.text


def test_model_answer_fallback_is_logged_with_its_reason(client, case, caplog):
    with patch("conversation.model_config", return_value=CONFIG), \
            patch("conversation.ask", return_value={"kind": "verdict", "answer": "Invented answer"}), \
            caplog.at_level(logging.INFO):
        client.post("/conversation/ask", json={"case": case, "lang": "de", "question": "Q?"})
    line, = [m for m in messages(caplog, "conversation") if m.startswith("conversation_answer_fallback")]
    assert "kind='verdict'" in line and "Unexpected answer classification" in line
    assert "Invented answer" not in caplog.text


def test_invalid_apertus_json_is_logged_without_the_reply(caplog):
    with patch("summaries.chat", return_value="SECRET MODEL OUTPUT without json"), caplog.at_level(logging.INFO), \
            pytest.raises(ValueError):
        summaries.ask({}, "instruction", {})
    assert messages(caplog, "summaries") == ["apertus_reply_invalid attempt=1 reason=no_json_object chars=32",
                                             "apertus_reply_invalid attempt=2 reason=no_json_object chars=32"]
    assert "SECRET MODEL OUTPUT" not in caplog.text


def test_rejected_draft_logs_why(caplog):
    replies = iter([{"summary": "Die Beschwerde wird abgewiesen und ist nicht begründet.", "evidence_ids": [1]}] * 2)
    record = {"key": "8C_1/2026@2026-10-01", "docket": "8C_1/2026", "full_text": "x"}
    with patch("summaries.ask", side_effect=lambda *a, **k: next(replies)), caplog.at_level(logging.INFO), \
            pytest.raises(ValueError):
        summaries.draft({}, record, {}, "fr", [])
    assert messages(caplog, "summaries")[0] == \
        "draft_rejected case=8C_1/2026@2026-10-01 lang=fr attempt=1 reason=wrong_language"


def test_llm_failure_is_logged_without_prompt_or_key(tmp_path, caplog):
    client, _ = fake_client(fail=TimeoutError("slow"))
    llm = LLMClient(make_settings(tmp_path, LLM_API_KEY=SECRET), client=client)
    with caplog.at_level(logging.INFO), pytest.raises(TimeoutError):
        llm.chat([{"role": "user", "content": "PRIVATE PROMPT"}], prompt_version="t/v1")
    line, = messages(caplog, "suva.llm")
    assert line.startswith("llm_call_failed prompt_version=t/v1 host=api.example.test") and "error=TimeoutError" in line
    assert "PRIVATE PROMPT" not in caplog.text and SECRET not in caplog.text


def test_http_retries_and_give_up_are_logged_with_host_only(caplog):
    def handler(request):
        raise httpx.ConnectError("refused")

    c = PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, retries=1)
    with caplog.at_level(logging.INFO), pytest.raises(httpx.ConnectError):
        c.get("https://e.test/path?token=abc123")
    assert messages(caplog, "suva.http") == [
        "http_retry method=GET host=e.test attempt=1 error=ConnectError",
        "http_gave_up method=GET host=e.test attempts=2 error=ConnectError"]
    assert "abc123" not in caplog.text


def test_http_status_retry_and_block_are_logged(caplog):
    responses = iter([httpx.Response(503), httpx.Response(403)])
    c = PoliteClient(transport=httpx.MockTransport(lambda r: next(responses)), sleep=lambda s: None)
    with caplog.at_level(logging.INFO), pytest.raises(Exception):
        c.get("https://e.test/x")
    assert messages(caplog, "suva.http") == [
        "http_retry method=GET host=e.test attempt=1 status=503 delay_s=2.0",
        "http_blocked method=GET host=e.test status=403"]


def test_fetch_logs_start_source_failure_and_summary(tmp_path, repo, caplog):
    write_parquet(tmp_path / "raw", [("8C_1/2025", "2025-10-02", None, SUVA_CASE)])
    client = PoliteClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")),
                          sleep=lambda s: None)
    with caplog.at_level(logging.INFO):
        fetch(repo, tmp_path / "raw", DEV, client=client)
    lines = messages(caplog, "suva.fetch")
    assert lines[0].startswith("fetch_start window=dev start=2025-10-01 end=2025-10-07")
    assert any(m.startswith("fetch_source_failed source=entscheidsuche stage=listing status=failed") for m in lines)
    assert lines[-1].startswith("fetch_done window=dev sightings=1 new=1 errors=1")


def test_parse_run_is_summarised(repo, caplog):
    with caplog.at_level(logging.INFO):
        from suva.parse.run import parse_all
        parse_all(repo)
    assert messages(caplog, "suva.parse.run") == ["parse_done judgements=0 parsed=0 skipped=0 status={} picks_not_ok=0"]


@pytest.mark.parametrize("level", ["debug", "WARNING", "", "LOUD"])
def test_configure_logging_accepts_any_log_level_value(level):
    configure_logging({"LOG_LEVEL": level})  # an unknown value falls back to INFO instead of crashing
