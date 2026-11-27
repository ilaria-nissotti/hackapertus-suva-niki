import json

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
    assert "gateway down" in repo.list_audit(kind="llm_call")[0]["payload"]["error"]


def test_cache_persists_across_instances(tmp_path):
    c1 = LLMCache(tmp_path / "c.sqlite")
    c1.put("k", "m", "v1", {"text": "x"})
    c1.close()
    assert LLMCache(tmp_path / "c.sqlite").get("k") == {"text": "x"}
