"""Integration: review app routes, job processing and record statuses (Flask test client, no network)."""

import json
from contextlib import closing
from datetime import date
from io import BytesIO
from unittest.mock import patch

import pytest

import app as dashboard
import briefing
from conftest import seed_kb
from suva.parse.run import parse_all
from suva.scoring.rules import load_rules
from suva.scoring.store import score_keys
from suva.sources.base import SourceRecord
from suva.storage import SQLiteRepository
from test_review_app import SUVA_CASE

CONFIG = {"LLM_NAME": "test-model"}
JSON = {"Accept": "application/json"}


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


def add_draft(key, lang="de", summary="Die Beschwerde wird abgewiesen.", approved=None):
    record = dashboard.load_record(key)
    version = dashboard.generation_version(record, CONFIG)
    with closing(dashboard.connect()) as db, db:
        db.execute("INSERT INTO results VALUES (?,?,?,?,NULL,?)",
                   (key, lang, version, json.dumps({"summary": summary, "quotes": []}), approved))
    return version


def result_row(key, kind, config=CONFIG):
    record = dashboard.load_record(key)
    with closing(dashboard.connect()) as db:
        return dashboard.get_result(db, record, kind, config)


def page(client, **query):
    return client.get("/api/dashboard", query_string=query).json


# --- /approve ----------------------------------------------------------------------------------

def approve(client, key, version, summary="Genehmigt.", lang="de", headers=JSON, previous=""):
    return client.post("/approve", headers=headers, data={"case": key, "lang": lang, "summary": summary,
                                                          "version": version, "previous_approved": previous})


def test_approve_accepts_exactly_16000_characters(client, case):
    version = add_draft(case)
    assert approve(client, case, version, "x" * 16000).status_code == 200
    assert result_row(case, "de")["approved"] == "x" * 16000


def test_approve_rejects_16001_characters_and_blank_text(client, case):
    version = add_draft(case)
    assert approve(client, case, version, "x" * 16001).status_code == 400
    assert approve(client, case, version, "   \n ").status_code == 400
    assert result_row(case, "de")["approved"] is None


def test_approve_strips_surrounding_whitespace(client, case):
    version = add_draft(case)
    approve(client, case, version, "  Text.  \n")
    assert result_row(case, "de")["approved"] == "Text."


@pytest.mark.parametrize("lang", ["it", "", "DE"])
def test_approve_unknown_language_is_400(client, case, lang):
    assert approve(client, case, "v", lang=lang).status_code == 400


def test_approve_unknown_case_is_404(client, case):
    assert approve(client, "8C_404/2026@2026-10-01", "v").status_code == 404


def test_approve_without_a_draft_or_with_old_version_is_409(client, case):
    assert approve(client, case, "v").status_code == 409
    add_draft(case)
    response = approve(client, case, "old-version")
    assert response.status_code == 409 and "draft changed" in response.json["message"]


def test_approve_from_a_plain_form_redirects(client, case):
    version = add_draft(case)
    response = approve(client, case, version, headers={})
    assert response.status_code == 303 and "Summary+approved" in response.location


def test_approve_missing_form_field_is_400(client, case):
    assert client.post("/approve", data={"case": case, "lang": "de"}, headers=JSON).status_code == 400


def test_re_approval_from_the_page_that_saw_the_approval_replaces_it(client, case):
    version = add_draft(case)
    assert approve(client, case, version, "Erste Fassung.").status_code == 200
    assert approve(client, case, version, "Zweite Fassung.", previous="Erste Fassung.").status_code == 200
    assert result_row(case, "de")["approved"] == "Zweite Fassung."


def test_second_tab_cannot_silently_overwrite_another_tabs_approval(client, case):
    version = add_draft(case)
    # Both tabs loaded the unapproved draft (previous_approved "").
    assert approve(client, case, version, "Fassung aus Tab A.").status_code == 200
    late = approve(client, case, version, "Fassung aus Tab B.")
    assert late.status_code == 409 and late.json["message"].startswith("Another approval was saved for this draft.")
    assert result_row(case, "de")["approved"] == "Fassung aus Tab A."


def test_approval_with_an_outdated_previous_text_is_refused(client, case):
    version = add_draft(case, approved="Aktuell.")
    assert approve(client, case, version, "Neu.", previous="Älter.").status_code == 409
    assert approve(client, case, version, "Neu.", previous="Aktuell.").status_code == 200


# --- cross-site protection --------------------------------------------------------------------

@pytest.mark.parametrize("headers, status", [
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Origin": "https://evil.example"}, 403),
    ({"Origin": "http://localhost"}, 409),  # same origin: passes the check, then no draft -> 409
    ({"Origin": "http://localhost/"}, 409),
    ({"Sec-Fetch-Site": "same-origin"}, 409),
])
def test_cross_site_posts_are_rejected(client, case, headers, status):
    response = client.post("/approve", headers=JSON | headers,
                           data={"case": case, "lang": "de", "summary": "x", "version": "v"})
    assert response.status_code == status


def test_cross_site_check_does_not_block_reads(client, case):
    assert client.get("/api/dashboard", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


# --- /add ----------------------------------------------------------------------------------------

def add(client, **data):
    return client.post("/add", headers=JSON, data=data)


@pytest.mark.parametrize("docket", ["", "8C-1/2026", "8C_1/26", "8c_1/2026", "hello"])
def test_add_rejects_malformed_case_ids(client, docket):
    response = add(client, docket=docket)
    assert response.status_code == 400 and "Enter a Swiss case ID" in response.json["message"]


@pytest.mark.parametrize("docket", ["123AB_5/2025", "8CC_1/2025", "123C_1/2025", "8C_12345/2025"])
def test_add_rejects_case_ids_the_rest_of_the_pipeline_never_recognises(client, tmp_path, docket):
    lookup = add(client, docket=docket)
    assert lookup.status_code == 400 and "Enter a Swiss case ID" in lookup.json["message"]

    upload = add(client, docket=docket, decision_date="2026-10-01",
                 text=(BytesIO(SUVA_CASE.encode()), "j.txt"))
    assert upload.status_code == 400 and "Enter a Swiss case ID" in upload.json["message"]
    repo = SQLiteRepository(tmp_path / "kb.sqlite")
    repo.migrate()
    assert repo.list_judgements() == []  # nothing stored, parsed or scored
    repo.close()


@pytest.mark.parametrize("docket", ["8C_1/2026", "12T_1/2023", "9C_1234/2025"])
def test_add_accepts_well_formed_case_ids(client, tmp_path, docket):
    response = add(client, docket=docket, decision_date="2026-10-01", text=(BytesIO(SUVA_CASE.encode()), "j.txt"))
    assert response.status_code == 200 and response.json["case"] == f"{docket}@2026-10-01"


@pytest.mark.parametrize("field, value", [("decision_date", "2026-13-01"), ("decision_date", "01.10.2026"),
                                          ("publication_date", "yesterday")])
def test_add_with_an_invalid_date_asks_for_iso_format(client, case, field, value):
    data = {"docket": "8C_1/2026", "decision_date": "2026-10-01", field: value,
            "text": (BytesIO(SUVA_CASE.encode()), "j.txt")}
    response = client.post("/add", headers=JSON, data=data)
    assert response.status_code == 400 and response.json["message"] == "Enter dates as YYYY-MM-DD."


def test_valid_date():
    assert dashboard.valid_date("2026-10-01") == date(2026, 10, 1)
    assert dashboard.valid_date("") is None and dashboard.valid_date(None) is None


def test_add_non_utf8_upload_asks_for_a_utf8_file(client, tmp_path):
    response = add(client, docket="8C_5/2026", decision_date="2026-10-01",
                   text=(BytesIO(b"\xff\xfe\xfa invalid"), "j.txt"))
    assert response.status_code == 400
    assert response.json["message"] == "Could not add that judgment. Upload a UTF-8 text file."
    repo = SQLiteRepository(tmp_path / "kb.sqlite")
    repo.migrate()
    assert repo.list_judgements() == []
    repo.close()


def test_add_other_value_errors_keep_their_own_message(client):
    response = add(client, docket="8C_5/2026", text=(BytesIO(SUVA_CASE.encode()), "j.txt"))  # no decision date
    assert response.status_code == 400 and "decision date" in response.json["message"]


def test_add_upload_over_4_mb_is_413(client):
    big = BytesIO(b"x" * (4 * 1024 * 1024 + 1))
    response = client.post("/add", headers=JSON, data={"docket": "8C_5/2026", "decision_date": "2026-10-01",
                                                        "text": (big, "j.txt")})
    assert response.status_code == 413


def test_add_upload_ignores_unsafe_source_urls(client, tmp_path):
    response = add(client, docket="8C_6/2026", decision_date="2026-10-01", source_url="javascript:alert(1)",
                   text=(BytesIO(SUVA_CASE.encode()), "j.txt"))
    key = response.json["case"]
    assert dashboard.load_record(key)["source_url"] is None


@pytest.mark.parametrize("value, expected", [
    ("https://www.bger.ch/x", "https://www.bger.ch/x"), ("http://a.b", "http://a.b"),
    ("javascript:alert(1)", None), ("ftp://a.b", None), ("https://", None), ("", None), (None, None),
])
def test_safe_url(value, expected):
    assert dashboard.safe_url(value) == expected


def test_add_same_case_twice_is_idempotent(client, case):
    assert add(client, docket="8C_1/2026").json["case"] == case
    assert add(client, docket="8C_1/2026").json["case"] == case
    with closing(dashboard.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM review_cases").fetchone()[0] == 1


# --- /api/dashboard ------------------------------------------------------------------------------

def seed_many(path, n):
    repo = SQLiteRepository(path)
    repo.migrate()
    keys = []
    for i in range(n):
        rec = SourceRecord("opencaselaw", f"s{i}", f"8C_{i + 1}/2026", date(2026, 10, 1 + i % 28), text=SUVA_CASE)
        repo.upsert_judgement(rec)
        keys.append(rec.key)
    parse_all(repo)
    score_keys(repo, keys, load_rules())
    repo.close()
    return keys


@pytest.mark.parametrize("requested, served", [(None, 1), ("0", 1), ("-3", 1), ("2", 2), ("99", 2), ("abc", 1)])
def test_dashboard_page_is_clamped(client, tmp_path, requested, served):
    seed_many(tmp_path / "kb.sqlite", 31)
    data = page(client, **({"page": requested} if requested else {}))
    assert data["page"] == served and data["total"] == 31
    assert len(data["rows"]) == (30 if served == 1 else 1)


def test_dashboard_on_an_empty_kb(client):
    data = page(client)
    assert data["rows"] == [] and data["count"] == 0 and data["dates"] is None and data["page"] == 1
    assert data["selected"] is None and data["active"] is False


def test_dashboard_status_filter_and_sort_by_date(client, tmp_path):
    seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE, decided=date(2026, 10, 1), score=90, band="high")
    seed_kb(tmp_path / "kb.sqlite", "8C_2/2026", SUVA_CASE, decided=date(2026, 10, 20), score=10, band="low")
    assert [r["docket"] for r in page(client)["rows"]] == ["8C_1/2026", "8C_2/2026"]  # by score
    assert [r["docket"] for r in page(client, sort="date")["rows"]] == ["8C_2/2026", "8C_1/2026"]
    low = page(client, status="Low relevance")
    assert [r["docket"] for r in low["rows"]] == ["8C_2/2026"] and low["count"] == 2


def test_dashboard_breaks_equal_scores_by_points(client, tmp_path):
    keys = [seed_kb(tmp_path / "kb.sqlite", f"8C_{i}/2026", SUVA_CASE, score=100.0, band="high") for i in (1, 2, 3)]
    repo = SQLiteRepository(tmp_path / "kb.sqlite")
    repo.save_scores([repo.get_score(k) | {"points": p} for k, p in zip(keys, (400, 600, 368))])
    repo.close()
    assert [r["docket"] for r in page(client)["rows"]] == ["8C_2/2026", "8C_1/2026", "8C_3/2026"]


def test_dashboard_unknown_language_falls_back_to_german(client, case):
    assert page(client, lang="it")["lang"] == "de"
    assert page(client, lang="fr")["lang"] == "fr"


def test_dashboard_unknown_selected_case_is_none(client, case):
    assert page(client, case="8C_404/2026@2026-10-01")["selected"] is None


def test_unscored_and_low_relevance_statuses(client, tmp_path):
    repo = SQLiteRepository(tmp_path / "kb.sqlite")
    repo.migrate()
    repo.upsert_judgement(SourceRecord("opencaselaw", "u", "8C_9/2026", date(2026, 10, 1), text=SUVA_CASE))
    repo.close()
    seed_kb(tmp_path / "kb.sqlite", "8C_8/2026", SUVA_CASE, score=1, band="low")
    statuses = {r["docket"]: r["status"] for r in page(client)["rows"]}
    assert statuses == {"8C_9/2026": "Unscored", "8C_8/2026": "Low relevance"}


def test_frontend_not_built_is_503(client, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "FRONTEND", tmp_path / "dist")
    response = client.get("/")
    assert response.status_code == 503 and b"npm --prefix src/frontend" in response.data


def test_missing_portrait_is_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    assert client.get("/conversation-portrait").status_code == 404


# --- briefing routes -------------------------------------------------------------------------

class RecordingPool:
    def __init__(self):
        self.tasks = []

    def submit(self, task):
        self.tasks.append(task)


def post_briefing(client, key, lang="de", headers=JSON):
    return client.post("/briefing", headers=headers, data={"case": key, "lang": lang})


def test_briefing_invalid_language_is_400(client, case):
    assert post_briefing(client, case, "it").status_code == 400


def test_briefing_needs_an_approved_summary(client, case, monkeypatch):
    monkeypatch.setattr(briefing, "voice_config", lambda lang: ("key", "voice"))
    add_draft(case)
    response = post_briefing(client, case)
    assert response.status_code == 400 and "Approve" in response.json["message"]


def test_briefing_needs_elevenlabs_configuration(client, case, monkeypatch):
    monkeypatch.setattr(briefing, "voice_config", lambda lang: (None, None))
    add_draft(case, approved="Genehmigt.")
    response = post_briefing(client, case)
    assert response.status_code == 400 and "ElevenLabs" in response.json["message"]


def test_briefing_unknown_case(client, case):
    response = post_briefing(client, "8C_404/2026@2026-10-01")
    assert response.status_code == 400 and response.json["message"] == "Judgment not found."


def test_briefing_is_queued_once(client, case, monkeypatch):
    monkeypatch.setattr(briefing, "voice_config", lambda lang: ("key", "voice"))
    pool = RecordingPool()
    monkeypatch.setattr(dashboard, "pool", pool)
    monkeypatch.setattr(dashboard, "pending", set())
    add_draft(case, approved="Genehmigt.")
    assert post_briefing(client, case).status_code == 202
    assert post_briefing(client, case).status_code == 202
    assert len(pool.tasks) == 1  # second request joins the pending job
    assert page(client, case=case)["audio"]["active"] is True


def test_briefing_form_post_redirects_with_message(client, case):
    response = post_briefing(client, case, headers={})
    assert response.status_code == 303 and "case-briefing" in response.location


def briefing_key(case, lang="de"):
    record = dashboard.load_record(case)
    with closing(dashboard.connect()) as db:
        ctx, _, _ = dashboard.briefing_context(db, record, lang, CONFIG)
    return ctx[0]


def test_play_briefing_edge_cases(client, case, monkeypatch):
    monkeypatch.setattr(briefing, "voice_config", lambda lang: ("key", "voice"))
    add_draft(case, approved="Genehmigt.")
    key = briefing_key(case)
    url = f"/briefing/{key}.mp3"
    assert client.get(url, query_string={"case": case, "lang": "it"}).status_code == 404
    assert client.get(url, query_string={"case": "nope", "lang": "de"}).status_code == 404
    assert client.get("/briefing/wrong.mp3", query_string={"case": case, "lang": "de"}).status_code == 404
    assert client.get(url, query_string={"case": case, "lang": "de"}).status_code == 404  # no row yet

    with closing(dashboard.connect()) as db, db:
        db.execute("INSERT INTO briefings (cache_key, script) VALUES (?, 'Skript')", (key,))
    assert client.get(url, query_string={"case": case, "lang": "de"}).status_code == 404  # script, no mp3

    with closing(dashboard.connect()) as db, db:
        db.execute("UPDATE briefings SET mp3=? WHERE cache_key=?", (b"ID3audio", key))
    response = client.get(url, query_string={"case": case, "lang": "de"})
    assert response.status_code == 200 and response.data == b"ID3audio" and response.mimetype == "audio/mpeg"


def test_briefing_key_changes_when_the_approved_text_changes(client, case, monkeypatch):
    monkeypatch.setattr(briefing, "voice_config", lambda lang: ("key", "voice"))
    version = add_draft(case, approved="Erste.")
    first = briefing_key(case)
    assert approve(client, case, version, "Zweite.", previous="Erste.").status_code == 200
    assert briefing_key(case) != first


# --- process(): summary jobs -----------------------------------------------------------------

FACTS = {"facts": "f", "issues": "i", "reasoning": "r", "outcome": "o", "quotes": ["q"]}


def test_process_drafts_after_facts_and_caches_both(client, case, monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard, "make_facts", lambda config, text: calls.append("facts") or dict(FACTS))
    monkeypatch.setattr(dashboard, "draft", lambda config, record, facts, lang, ex: calls.append(lang)
                        or {"summary": "Zusammenfassung", "quotes": ["q"]})
    assert dashboard.process(case, "de", config=CONFIG)
    assert dashboard.process(case, "de", config=CONFIG)  # cached: no new calls
    assert dashboard.process(case, "fr", config=CONFIG)  # reuses the stored facts
    assert calls == ["facts", "de", "fr"]


def test_concurrent_jobs_for_the_same_draft_call_the_model_once(client, case, monkeypatch):
    import threading
    import time

    calls = []

    def slow_facts(config, text):
        calls.append(1)
        time.sleep(0.2)
        return dict(FACTS)

    monkeypatch.setattr(dashboard, "make_facts", slow_facts)
    results = []
    app_config = dict(dashboard.app.config)

    def job():
        with patch.dict(dashboard.app.config, app_config):
            results.append(dashboard.process(case, "facts", config=CONFIG))

    threads = [threading.Thread(target=job) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True, True, True] and len(calls) == 1  # the others reuse the stored brief


def test_different_drafts_do_not_share_a_lock(client, case, monkeypatch):
    monkeypatch.setattr(dashboard, "make_facts", lambda config, text: dict(FACTS))
    monkeypatch.setattr(dashboard, "draft", lambda config, record, facts, lang, ex: {"summary": lang, "quotes": ["q"]})
    assert dashboard.process(case, "de", config=CONFIG)  # holds the de lock while it builds the facts
    locks = list((dashboard.app.config["DATABASE"].parent / "summary-locks").iterdir())
    assert len(locks) == 2  # one lock file per (case, kind, version): de and facts


def test_process_unknown_case_returns_false(client, case):
    assert dashboard.process("8C_404/2026@2026-10-01", "de", config=CONFIG) is False


def test_process_shows_the_provider_status_from_bger_chat(client, case, monkeypatch):
    import httpx
    from openai import APIStatusError

    import bger

    class FakeLLM:
        def __init__(self, settings):
            self.client = httpx.Client()

        def chat(self, *a, **k):
            request = httpx.Request("POST", "https://x.test")
            raise APIStatusError("busy", response=httpx.Response(503, request=request), body=None)

    monkeypatch.setattr(bger, "LLMClient", FakeLLM)
    monkeypatch.setattr(dashboard, "make_facts", lambda config, text: bger.chat(config, [], 10))
    assert not dashboard.process(case, "facts", config=CONFIG | {"settings": None})
    assert result_row(case, "facts")["error"] == "Provider HTTP 503"


def test_process_plain_oserror_is_a_generic_retry_message(client, case, monkeypatch):
    monkeypatch.setattr(dashboard, "make_facts", lambda c, t: (_ for _ in ()).throw(OSError("Apertus HTTP 429")))
    assert not dashboard.process(case, "facts", config=CONFIG)
    assert result_row(case, "facts")["error"] == "Apertus request failed. Try again."


def test_failed_facts_message_is_replaced_by_the_generic_draft_error(client, case, monkeypatch):
    monkeypatch.setattr(dashboard, "make_facts",
                        lambda c, t: (_ for _ in ()).throw(ValueError("Apertus factual brief could not be verified")))
    assert not dashboard.process(case, "de", config=CONFIG)
    # process raises "The factual brief failed. Retry summary generation." but the stored message
    # is the generic one; the reviewer cannot tell that the factual brief step failed.
    assert result_row(case, "de")["error"] == "Could not generate a draft with verified source evidence. Try again."


@pytest.mark.parametrize("url, name, message", [
    ("http://remote.example/v1", "m", "Use HTTPS for a remote LLM_BASE_URL"),
    ("", "m", "Configure LLM_BASE_URL and LLM_NAME for Apertus"),
])
def test_invalid_model_config_shows_the_real_reason(client, case, monkeypatch, url, name, message):
    monkeypatch.undo()  # restore the real current_config / model_config
    with patch("bger.load_settings") as settings:
        settings.return_value.llm_base_url = url
        settings.return_value.llm_name = name
        assert dashboard.current_config() == {"LLM_NAME": "unconfigured", "error": message}
        assert not dashboard.process(case, "de")  # config=None -> current_config()
    unconfigured = {"LLM_NAME": "unconfigured"}  # same generation version as with the error text
    assert result_row(case, "de", unconfigured)["error"] == message


def test_unconfigured_without_a_reason_keeps_the_generic_message(client, case):
    assert not dashboard.process(case, "de", config={"LLM_NAME": "unconfigured"})
    assert result_row(case, "de", {"LLM_NAME": "unconfigured"})["error"] == "Missing model configuration"


def test_process_without_source_text_needs_no_model(client, tmp_path):
    repo = SQLiteRepository(tmp_path / "kb.sqlite")
    repo.migrate()
    repo.upsert_judgement(SourceRecord("opencaselaw", "e", "8C_7/2026", date(2026, 10, 1), text=None))
    repo.close()
    key = "8C_7/2026@2026-10-01"
    assert not dashboard.process(key, "facts", config=CONFIG)
    assert result_row(key, "facts")["error"] == "The judgment has no usable source text."


# --- summarize_top ---------------------------------------------------------------------------

def test_summarize_top_only_drafts_relevant_and_review_cases(client, tmp_path, monkeypatch):
    high = seed_kb(tmp_path / "kb.sqlite", "8C_1/2026", SUVA_CASE, score=90, band="high")
    seed_kb(tmp_path / "kb.sqlite", "8C_2/2026", SUVA_CASE, score=5, band="low")
    calls = []
    monkeypatch.setattr(dashboard, "run_summary_job", lambda key, lang: calls.append((key, lang)) or True)
    assert dashboard.summarize_top(10, 1) == 0
    assert sorted(calls) == [(high, "de"), (high, "fr")]


def test_summarize_top_reports_failures(client, case, monkeypatch, capsys):
    monkeypatch.setattr(dashboard, "run_summary_job", lambda key, lang: lang == "de")
    assert dashboard.summarize_top(10, 1) == 1
    assert "1 tasks need retry" in capsys.readouterr().out


def test_summarize_top_with_limit_zero_does_nothing(client, case, monkeypatch):
    monkeypatch.setattr(dashboard, "run_summary_job", lambda *a: pytest.fail("should not run"))
    assert dashboard.summarize_top(0, 1) == 0
