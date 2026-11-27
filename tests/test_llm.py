import json
from types import SimpleNamespace

import pytest

from conftest import fake_client, make_settings
from suva.llm import CacheMiss, LLMCache, LLMClient

MSG = [{"role": "user", "content": "Hallo"}]


def make(tmp_path, repo, **env):
    s = make_settings(tmp_path, **env)
    client, completions = fake_client()
    return LLMClient(s, repo=repo, client=client), completions


def test_second_call_is_cached(tmp_path, repo):
    llm, completions = make(tmp_path, repo)
    a = llm.chat(MSG, prompt_version="t/v1")
    b = llm.chat(MSG, prompt_version="t/v1")
    assert len(completions.calls) == 1
    assert (a.cached, b.cached) == (False, True)
    assert a.text == b.text == "Bereit"
    assert a.input_hash == b.input_hash


def test_cache_key_depends_on_prompt_version_and_params(tmp_path, repo):
    llm, completions = make(tmp_path, repo)
    llm.chat(MSG, prompt_version="t/v1")
    llm.chat(MSG, prompt_version="t/v2")
    llm.chat(MSG, prompt_version="t/v1", temperature=0.3)
    llm.chat(MSG, prompt_version="t/v1", model="swiss-ai/Apertus-v1.5-70B")
    assert len(completions.calls) == 4


def test_readonly_mode_never_calls_network(tmp_path, repo):
    llm, completions = make(tmp_path, repo)
    llm.chat(MSG, prompt_version="t/v1")  # populate snapshot

    ro, ro_calls = make(tmp_path, repo, LLM_CACHE_MODE="readonly")
    assert ro.chat(MSG, prompt_version="t/v1").cached
    with pytest.raises(CacheMiss):
        ro.chat(MSG, prompt_version="t/other")
    assert ro_calls.calls == []


def test_off_mode_skips_cache(tmp_path, repo):
    llm, completions = make(tmp_path, repo, LLM_CACHE_MODE="off")
    llm.chat(MSG, prompt_version="t/v1")
    llm.chat(MSG, prompt_version="t/v1")
    assert len(completions.calls) == 2
    assert not (tmp_path / "llm_cache.sqlite").exists()


def test_audit_records_hashes_not_secrets(tmp_path, repo):
    llm, _ = make(tmp_path, repo)
    llm.chat(MSG, prompt_version="t/v1")
    llm.chat(MSG, prompt_version="t/v1")
    rows = repo.list_audit(kind="llm_call")
    assert [r["payload"]["cached"] for r in rows] == [True, False]
    p = rows[0]["payload"]
    assert p["host"] == "api.example.test"
    assert p["prompt_version"] == "t/v1"
    dumped = json.dumps([r["payload"] for r in rows])
    assert "secret-key" not in dumped and "Hallo" not in dumped


def test_errors_are_audited_and_raised(tmp_path, repo):
    s = make_settings(tmp_path)
    client, _ = fake_client(fail=TimeoutError("gateway down"))
    llm = LLMClient(s, repo=repo, client=client)
    with pytest.raises(TimeoutError):
        llm.chat(MSG, prompt_version="t/v1")
    assert repo.list_audit(kind="llm_call")[0]["payload"]["error"] == "TimeoutError"


def test_cache_persists_across_instances(tmp_path):
    c1 = LLMCache(tmp_path / "c.sqlite")
    c1.put("k", "m", "v1", {"text": "x"})
    c1.close()
    assert LLMCache(tmp_path / "c.sqlite").get("k") == {"text": "x"}

def test_endpoint_cache_isolation_and_served_model(tmp_path, repo, monkeypatch):
    first, completions = make(tmp_path, repo)
    monkeypatch.setattr(completions, "create", lambda **kwargs: SimpleNamespace(
        model="served-model", choices=[SimpleNamespace(message=SimpleNamespace(content="first"))],
    ))
    first.chat(MSG, prompt_version="t/v1")
    replay = first.chat(MSG, prompt_version="t/v1")
    assert (replay.cached, replay.model, replay.host) == (True, "served-model", "api.example.test")
    assert repo.list_audit()[0]["actor"] == "served-model"

    second, calls = make(tmp_path, repo, LLM_BASE_URL="https://other.example.test/v1")
    assert not second.chat(MSG, prompt_version="t/v1").cached
    assert len(calls.calls) == 1


def test_malformed_response_is_audited(tmp_path, repo, monkeypatch):
    llm, completions = make(tmp_path, repo)
    monkeypatch.setattr(completions, "create", lambda **kwargs: SimpleNamespace(choices=[]))
    with pytest.raises(ValueError, match="no choices"):
        llm.chat(MSG, prompt_version="t/v1")
    assert repo.list_audit()[0]["payload"]["error"] == "ValueError"


def test_ping_bypasses_readonly_cache(tmp_path, monkeypatch):
    from suva import cli, llm

    cached, _ = make(tmp_path, None)
    cached.chat(MSG, prompt_version="ping/v1", max_tokens=20)
    cached.cache.close()
    settings = make_settings(tmp_path, LLM_CACHE_MODE="readonly")
    client, calls = fake_client()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(llm, "_build_openai_client", lambda settings: client)
    assert cli.main(["llm-ping", "--prompt", "Hallo"]) == 0
    assert len(calls.calls) == 1
