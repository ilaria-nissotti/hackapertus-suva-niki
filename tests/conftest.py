from __future__ import annotations

from types import SimpleNamespace

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


class FakeCompletions:
    def __init__(self, reply="Bereit", fail=None):
        self.calls = []
        self.reply = reply
        self.fail = fail

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return SimpleNamespace(
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=1),
        )


def fake_client(**kw):
    completions = FakeCompletions(**kw)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "kb.sqlite")
    r.migrate()
    yield r
    r.close()


def seed_kb(path, docket, text, decided=None, score=None, band=None):
    """A knowledge base with one parsed, rules-scored judgement (for the review app tests).
    `score`/`band` override the stored rules score while keeping it current."""
    from datetime import date

    from suva.parse.run import parse_all
    from suva.scoring.rules import load_rules
    from suva.scoring.store import score_keys
    from suva.sources.base import SourceRecord

    kb = SQLiteRepository(path)
    kb.migrate()
    rec = SourceRecord(source="opencaselaw", source_id=docket, docket=docket,
                       decision_date=decided or date(2026, 10, 1), text=text)
    kb.upsert_judgement(rec)
    parse_all(kb)
    score_keys(kb, [rec.key], load_rules())
    if score is not None:
        row = kb.get_score(rec.key)
        kb.save_scores([row | {"score": score, "band": band or row["band"]}])
    kb.close()
    return rec.key


# --- review app: tests act as a reviewer who entered their name -----------------------------------
# The app asks who is reviewing before anything else and signs approvals with that name. App tests act as the
# fictional "Test Reviewer": every app test client puts it in its session before each request. Modules that
# test the anonymous start opt out.
TEST_REVIEWER = {"name": "Test Reviewer", "initials": "TRV", "function": "Rechtsanwalt"}
ANONYMOUS_MODULES = {"test_review_signature"}


def act_as_reviewer(client, reviewer=TEST_REVIEWER):
    import app as dashboard

    dashboard.ensure_secret_key()
    with client.session_transaction() as session:
        session.setdefault("reviewer", dict(reviewer))


@pytest.fixture(autouse=True)
def reviewer_session(request, monkeypatch):
    import sys

    if request.module.__name__ in ANONYMOUS_MODULES or "app" not in sys.modules:
        yield
        return
    from flask.testing import FlaskClient

    import app as dashboard

    class ReviewerClient(FlaskClient):
        def open(self, *args, **kwargs):
            act_as_reviewer(self)
            return super().open(*args, **kwargs)

    monkeypatch.setattr(dashboard.app, "test_client_class", ReviewerClient)
    yield
