from __future__ import annotations

import pytest

from suva.config import load_settings
from suva.storage import SQLiteRepository


def make_settings(tmp_path, **overrides):
    env = {
        "LLM_NAME": "swiss-ai/Apertus-v1.5-8B",
        "LLM_BASE_URL": "https://api.example.test/v1",
        "LLM_API_KEY": "secret-key",
        "DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'kb.sqlite'}",
        "LLM_CACHE_MODE": "readwrite",
    }
    env.update(overrides)
    return load_settings(environ=env)


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "kb.sqlite")
    r.migrate()
    yield r
    r.close()
