import pytest

from conftest import make_settings


def test_defaults_and_host(tmp_path):
    s = make_settings(tmp_path)
    assert s.llm_host == "api.example.test"
    assert s.sqlite_path == tmp_path / "kb.sqlite"
    assert s.llm_cache_path == tmp_path / "llm_cache.sqlite"


def test_invalid_cache_mode(tmp_path):
    with pytest.raises(ValueError):
        make_settings(tmp_path, LLM_CACHE_MODE="sometimes")


def test_non_sqlite_url_rejected_for_now(tmp_path):
    s = make_settings(tmp_path, DATABASE_URL="postgresql://x/y")
    with pytest.raises(ValueError):
        _ = s.sqlite_path
