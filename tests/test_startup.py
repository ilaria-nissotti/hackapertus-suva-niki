"""`src/app.py serve` checks its configuration on startup; `--strict` refuses to serve without it."""

import logging
from unittest.mock import patch

import pytest

import app as dashboard
from conftest import make_settings
from test_review_app import SUVA_CASE
from conftest import seed_kb

SECRET = "sk-startup-secret"


@pytest.fixture
def ready(tmp_path, monkeypatch):
    """A fully configured app: model, frontend build, knowledge base and both voices."""
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE)
    monkeypatch.setattr(dashboard, "FRONTEND", tmp_path / "dist")
    settings = make_settings(tmp_path, LLM_API_KEY=SECRET)
    monkeypatch.setattr("bger.load_settings", lambda: settings)
    monkeypatch.setattr("briefing.voice_config", lambda lang: (SECRET, f"voice-{lang}"))
    with patch.dict(dashboard.app.config, DATABASE=tmp_path / "review.sqlite", KB=tmp_path / "kb.sqlite"):
        yield tmp_path


def test_complete_configuration_passes_strict_startup(ready, caplog):
    with caplog.at_level(logging.INFO):
        assert dashboard.startup_problems() == ([], [])
        assert dashboard.check_startup(strict=True) is True
    assert "startup_check_ok optional_missing=0" in caplog.text
    assert "llm_host=api.example.test" in caplog.text and SECRET not in caplog.text


def test_missing_llm_config_is_required(ready, monkeypatch):
    monkeypatch.setattr("bger.load_settings", lambda: make_settings(ready, LLM_BASE_URL=""))
    required, _ = dashboard.startup_problems()
    assert len(required) == 1 and required[0].startswith("LLM: Configure LLM_BASE_URL and LLM_NAME")


def test_remote_http_endpoint_is_required_to_use_https(ready, monkeypatch):
    monkeypatch.setattr("bger.load_settings", lambda: make_settings(ready, LLM_BASE_URL="http://gateway.example/v1"))
    assert dashboard.startup_problems()[0][0].startswith("LLM: Use HTTPS")


def test_missing_frontend_and_kb_are_required(ready, monkeypatch):
    monkeypatch.setattr(dashboard, "FRONTEND", ready / "nowhere")
    with patch.dict(dashboard.app.config, KB=ready / "missing.sqlite"):
        required, _ = dashboard.startup_problems()
    assert [p.split(":")[0] for p in required] == ["frontend not built", f"knowledge base {ready / 'missing.sqlite'} not found"]


def test_missing_elevenlabs_is_optional(ready, monkeypatch, caplog):
    monkeypatch.setattr("briefing.voice_config", lambda lang: (None, None))
    required, optional = dashboard.startup_problems()
    assert required == [] and [p[:14] for p in optional] == ["ElevenLabs de:", "ElevenLabs fr:"]
    with caplog.at_level(logging.INFO):
        assert dashboard.check_startup(strict=True) is True  # audio is optional
    assert "startup_check_ok optional_missing=2" in caplog.text


def test_strict_startup_refuses_and_lenient_startup_warns(ready, monkeypatch, caplog):
    monkeypatch.setattr(dashboard, "FRONTEND", ready / "nowhere")
    with caplog.at_level(logging.INFO):
        assert dashboard.check_startup(strict=True) is False
        assert "startup_refused problems=1" in caplog.text
        caplog.clear()
        assert dashboard.check_startup(strict=False) is True  # unchanged default behaviour: serve anyway
    assert "startup_check_failed required=yes" in caplog.text and "startup_check_ok" not in caplog.text


def test_serve_strict_exits_2_without_starting_the_server(ready, monkeypatch):
    monkeypatch.setattr(dashboard, "FRONTEND", ready / "nowhere")
    monkeypatch.setattr(dashboard.app, "run", lambda **kw: pytest.fail("server must not start"))
    monkeypatch.setattr("sys.argv", ["app.py", "serve", "--strict"])
    assert dashboard.main() == 2


def test_serve_starts_after_a_passing_check(ready, monkeypatch):
    started = []
    monkeypatch.setattr(dashboard.app, "run", lambda **kw: started.append(kw))
    monkeypatch.setattr("sys.argv", ["app.py", "serve", "--strict", "--port", "5999"])
    assert dashboard.main() == 0 and started == [{"host": "127.0.0.1", "port": 5999}]
