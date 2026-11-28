"""Limit-case and stress tests for the core flows: input extremes, concurrency, external failures.

All offline and deterministic: networks, Apertus and ElevenLabs are test doubles.
"""

import json
import threading
from contextlib import closing
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import httpx
import pytest

import app as dashboard
import briefing
import bger
import summaries
from conftest import fake_client, make_settings, seed_kb
from suva.fetch import fetch
from suva.http import PoliteClient
from suva.llm import LLMClient
from suva.parse.citations import extract
from suva.parse.segment import segment
from suva.scoring.rules import parse_rules
from suva.scoring.score import score_record
from suva.sources import opencaselaw
from suva.sources.base import SourceRecord
from suva.storage import SQLiteRepository
from test_fetch import es_hit
from test_flow_pipeline import DEV, write_parquet
from test_review_app import SUVA_CASE

CONFIG = {"LLM_NAME": "m"}
JSON = {"Accept": "application/json"}


@pytest.fixture
def kb_path(tmp_path):
    return tmp_path / "kb.sqlite"


@pytest.fixture
def client(tmp_path, kb_path, monkeypatch):
    monkeypatch.setattr(dashboard, "current_config", lambda: CONFIG)
    with patch.dict(dashboard.app.config, DATABASE=tmp_path / "review.sqlite", KB=kb_path, TODAY=date(2026, 11, 27)):
        yield dashboard.app.test_client()


@pytest.fixture
def case(kb_path):
    return seed_kb(kb_path, "8C_1/2026", SUVA_CASE)


def in_threads(n, target):
    """Start n threads at once (behind a barrier) and wait for all of them."""
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        target(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def with_app_config(fn):
    """Flask config patches are thread-local to the test; copy them into worker threads."""
    config = dict(dashboard.app.config)

    def run(*args):
        with patch.dict(dashboard.app.config, config):
            return fn(*args)
    return run


# ============================== 1. Input extremes ===============================================

def test_segment_keeps_verbatim_offsets_on_a_4_mb_judgement():
    text = SUVA_CASE + ("\n\n" + "Erwägung mit Text. " * 50) * 4000
    seg = segment("k", text)
    assert len(text) > 3_500_000 and seg.status == "ok"
    assert all(text[p.start:p.end] == p.text for p in seg.passages)


@pytest.mark.parametrize("insert", ["\x00", "😀 Emoji", "עברית ‮ RTL", "​ zero width", "𝔘𝔫𝔦𝔠𝔬𝔡𝔢"])
def test_segment_handles_control_and_unicode_characters(insert):
    text = SUVA_CASE.replace("Der Versicherte stürzte.", f"Der Versicherte {insert} stürzte.")
    seg = segment("k", text)
    assert seg.status == "ok" and all(text[p.start:p.end] == p.text for p in seg.passages)


def test_segment_of_whitespace_only_text_fails_cleanly():
    assert segment("k", " \n\t\n\n ").status == "failed"


def test_citation_extraction_on_20000_repeated_articles():
    passage = "Art. 6 UVG, " * 20_000
    cits = extract(passage, self_docket="8C_1/2025")
    assert len(cits) == 20_000
    assert all(passage[c.start:c.end].startswith("Art. 6") for c in cits[:50])


def test_article_list_without_a_law_name_yields_no_citation():
    assert extract("Art. " + "1, " * 20_000, self_docket="x") == []


def test_graded_rule_caps_10000_matching_articles():
    rs = parse_rules({"rules": [{"id": "g", "field": "articles", "match": {"regex": "UVG$"}, "per_match": 3,
                                 "max": 15, "explanation": "x"}]})
    s = score_record({"articles": [f"Art. {i} UVG" for i in range(10_000)]}, rs)
    assert s.points == 15 and "(10000×)" in s.explanation


def test_text_rule_on_an_empty_field_list_does_not_fire():
    rs = parse_rules({"rules": [{"id": "l", "field": "laws", "match": {"any": ["UVG"]}, "weight": 5,
                                 "explanation": "x"}]})
    assert score_record({"laws": []}, rs).fired == [] and score_record({"laws": None}, rs).fired == []


def test_docket_with_sql_metacharacters_is_stored_verbatim(repo):
    docket = "8C_1/2025'; DROP TABLE judgements;--"
    repo.upsert_judgement(SourceRecord("opencaselaw", "x", docket, date(2025, 1, 1), text="t"))
    assert [r["docket"] for r in repo.list_judgements()] == [docket]
    assert repo.list_judgements(dockets={docket})[0]["key"] == f"{docket}@2025-01-01"


@pytest.mark.parametrize("query", ["((((", "[a-z]+*", "%' OR 1=1 --", "😀", "\x00", "ä" * 5000, "x" * 100_000])
def test_dashboard_search_with_regex_sql_and_unicode_queries(client, case, query):
    response = client.get("/api/dashboard", query_string={"q": query})
    assert response.status_code == 200 and response.json["total"] == 0 and response.json["count"] == 1


@pytest.mark.parametrize("page", ["99999999999999999999999", "-99999999999999999999", "1.5", "0x10", " 2", ""])
def test_dashboard_page_with_huge_or_non_integer_values(client, case, page):
    response = client.get("/api/dashboard", query_string={"page": page})
    assert response.status_code == 200 and response.json["page"] == 1


def test_dashboard_with_a_100k_character_case_key(client, case):
    response = client.get("/api/dashboard", query_string={"case": "x" * 100_000})
    assert response.status_code == 200 and response.json["selected"] is None


@pytest.mark.parametrize("body", [b"", b"\xef\xbb\xbf", b"   \n\n\t  "])
def test_add_upload_without_usable_text_is_rejected(client, body):
    response = client.post("/add", headers=JSON, data={"docket": "8C_45/2026", "decision_date": "2026-10-01",
                                                        "text": (BytesIO(body), "j.txt")})
    assert response.status_code == 400 and "usable judgment text" in response.json["message"]


def test_add_upload_of_a_3_mb_judgement_is_accepted(client):
    body = (SUVA_CASE + "\n\n" + "a" * (3 * 1024 * 1024)).encode()  # 3 MB text, under the 4 MB body limit
    response = client.post("/add", headers=JSON, data={"docket": "8C_46/2026", "decision_date": "2026-10-01",
                                                        "text": (BytesIO(body), "j.txt")})
    assert response.status_code == 200


@pytest.mark.parametrize("path, data", [
    ("/run", {}), ("/run", {"case": "x"}), ("/approve", {}), ("/approve", {"case": "x", "lang": "de"}),
    ("/briefing", {}), ("/briefing", {"case": "x"}),
])
def test_posts_with_missing_form_fields_are_400(client, case, path, data):
    assert client.post(path, data=data, headers=JSON).status_code == 400


def test_briefing_audio_without_query_arguments_is_404(client, case):
    assert client.get("/briefing/abc.mp3").status_code == 404


def test_conversation_payload_over_the_4_mb_body_limit_is_413(client, case):
    history = [{"question": "q", "answer": "a", "evidence_ids": []}] * 100_000
    response = client.post("/conversation/ask", json={"case": case, "lang": "de", "question": "Q?", "history": history})
    assert response.status_code == 413


@pytest.mark.parametrize("payload", [
    {"question": "Q?", "history": None}, {"question": 5}, {"question": None}, {"question": ["Q?"]},
    {"question": "Q?", "history": [None]}, {"question": "Q?", "history": {"question": "q"}},
])
def test_conversation_ask_with_wrong_json_types_is_400(client, case, payload):
    response = client.post("/conversation/ask", json={"case": case, "lang": "de"} | payload)
    assert response.status_code == 400


@pytest.mark.parametrize("payload", [{"case": [1], "lang": "de"}, {"case": None, "lang": "de"},
                                     {"case": "k", "lang": None}, {"case": "k", "lang": ["de"]}])
def test_conversation_with_wrong_case_or_language_types_is_400(client, case, payload):
    assert client.post("/conversation/ask", json=payload | {"question": "Q?"}).status_code == 400


@pytest.mark.parametrize("reply", [
    {"kind": "court"},  # no answer
    {"kind": "court", "answer": "A", "evidence_ids": "1"},  # ids not a list
    {"kind": "court", "answer": "A", "evidence_ids": [{"id": 1}]},  # ids are objects
    {"kind": "court", "answer": 42, "evidence_ids": [1]},  # answer not text
    {"kind": "verdict", "answer": "A", "evidence_ids": [1]},  # unknown kind
    {},
])
def test_malformed_model_answers_fall_back_to_uncertain(client, case, reply):
    with patch("conversation.model_config", return_value=CONFIG), patch("conversation.ask", return_value=reply):
        response = client.post("/conversation/ask", json={"case": case, "lang": "de", "question": "Q?"})
    assert response.status_code == 200
    assert response.json == {"answer": "Das lässt sich anhand dieses Urteils nicht verlässlich beantworten.",
                             "kind": "uncertain", "citations": []}


@pytest.mark.parametrize("reply", [{"points": None}, {"points": ["a"], "takeaways": [1], "open_questions": []},
                                   {"points": ["x" * 401], "takeaways": [], "open_questions": []}])
def test_malformed_recaps_are_502(client, case, reply):
    history = [{"question": "q", "answer": "a", "evidence_ids": []}]
    with patch("conversation.model_config", return_value=CONFIG), patch("conversation.ask", return_value=reply):
        response = client.post("/conversation/recap", json={"case": case, "lang": "de", "history": history})
    assert response.status_code == 502


@pytest.mark.parametrize("reply, expected", [
    ('{"a": 1} {"b": 2}', {"a": 1}),  # two objects: the first wins
    ('[{"a": 1}]', {"a": 1}),  # wrapped in a list
    ('{"a": "}"}', {"a": "}"}),  # brace inside a string
    ('```json\n{"a": 1}\n```', {"a": 1}),  # markdown fence
    ('{"a":' * 300 + "1" + "}" * 300, None),  # deep nesting still parses
])
def test_apertus_json_is_recovered_from_noisy_replies(reply, expected):
    with patch("summaries.chat", return_value=reply):
        result = summaries.ask({}, "i", {})
    assert result == expected if expected is not None else isinstance(result, dict)


@pytest.mark.parametrize("reply", ["", "null", "{" * 5000, "}{", "I cannot help with that."])
def test_apertus_replies_without_a_json_object_raise_value_error(reply):
    with patch("summaries.chat", return_value=reply), pytest.raises(ValueError, match="valid JSON object"):
        summaries.ask({}, "i", {})


def test_llm_reply_with_null_content_is_empty_text(tmp_path):
    client, completions = fake_client()
    completions.reply = None
    llm = LLMClient(make_settings(tmp_path), client=client)
    assert llm.chat([{"role": "user", "content": "x"}], prompt_version="t").text == ""


# ============================== 2. Concurrency and duplicates ==================================

def add_draft(key):
    record = dashboard.load_record(key)
    version = dashboard.generation_version(record, CONFIG)
    with closing(dashboard.connect()) as db, db:
        db.execute("INSERT INTO results VALUES (?,?,?,?,NULL,NULL)", (key, "de", version, json.dumps({"summary": "s"})))
    return version


def test_20_simultaneous_approvals_save_exactly_one_and_refuse_the_rest(client, case):
    version = add_draft(case)
    codes = []

    @with_app_config
    def approve(i):
        response = dashboard.app.test_client().post("/approve", headers=JSON, data={
            "case": case, "lang": "de", "summary": f"Fassung {i}", "version": version})
        codes.append(response.status_code)

    in_threads(20, approve)
    assert sorted(codes) == [200] + [409] * 19  # every tab saw no approval; only the first save may land
    with closing(dashboard.connect()) as db:
        approved = db.execute("SELECT approved FROM results WHERE case_key=?", (case,)).fetchone()[0]
    assert approved in {f"Fassung {i}" for i in range(20)}


def test_20_simultaneous_adds_of_the_same_docket_create_one_review_case(client, case):
    codes = []

    @with_app_config
    def add(_):
        codes.append(dashboard.app.test_client().post("/add", headers=JSON, data={"docket": "8C_1/2026"}).status_code)

    in_threads(20, add)
    assert codes == [200] * 20
    with closing(dashboard.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM review_cases").fetchone()[0] == 1


def test_simultaneous_uploads_of_different_texts_store_exactly_one(client, kb_path):
    seed_kb(kb_path, "8C_1/2026", SUVA_CASE)  # creates the KB schema
    results = []

    @with_app_config
    def upload(i):
        r = dashboard.app.test_client().post("/add", headers=JSON, data={
            "docket": "8C_99/2026", "decision_date": "2026-10-01",
            "text": (BytesIO(f"{SUVA_CASE} Version {i}".encode()), "j.txt")})
        results.append((r.status_code, r.json["message"]))

    in_threads(8, upload)
    assert sorted(code for code, _ in results) == [200] + [400] * 7
    assert all("already has source text" in m for code, m in results if code == 400)
    repo = SQLiteRepository(kb_path)
    assert repo.get_judgement("8C_99/2026@2026-10-01")["text"].startswith(SUVA_CASE)
    repo.close()


def test_duplicate_summarize_requests_queue_one_job_per_language(client, case, monkeypatch):
    submitted = []
    monkeypatch.setattr(dashboard, "pool", SimpleNamespace(submit=submitted.append))
    monkeypatch.setattr(dashboard, "pending", set())
    for _ in range(5):
        assert client.post("/run", headers=JSON, data={"case": case, "action": "summarize"}).status_code == 200
    assert len(submitted) == 1 and dashboard.pending == {(case, "de"), (case, "fr")}


def test_summary_jobs_for_different_cases_run_side_by_side(monkeypatch):
    both_running = threading.Barrier(2, timeout=5)
    done = []

    def job(key, kind):
        both_running.wait()  # breaks (and the job fails) if the second case waits behind the first
        done.append(key)
        return True

    monkeypatch.setattr(dashboard, "run_summary_job", job)
    monkeypatch.setattr(dashboard, "pending", set())
    dashboard.enqueue("8C_1/2026@2026-10-01", ["de"])
    dashboard.enqueue("8C_2/2026@2026-10-02", ["de"])
    for _ in range(100):
        if len(done) == 2:
            break
        threading.Event().wait(0.05)
    assert sorted(done) == ["8C_1/2026@2026-10-01", "8C_2/2026@2026-10-02"] and not both_running.broken


def test_parallel_writers_on_one_knowledge_base_do_not_lock_each_other_out(tmp_path):
    path = tmp_path / "kb.sqlite"
    SQLiteRepository(path).migrate()
    errors = []

    def write(i):
        try:
            repo = SQLiteRepository(path)
            for j in range(50):
                repo.upsert_judgement(SourceRecord("opencaselaw", f"s{i}", f"8C_{j}/2025", date(2025, 10, 2),
                                                   text=f"{SUVA_CASE} {i}"))
            repo.close()
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(repr(exc))

    in_threads(8, write)
    repo = SQLiteRepository(path)
    assert errors == [] and len(repo.list_judgements()) == 50
    repo.close()


def test_parallel_llm_clients_share_one_cache_file(tmp_path):
    errors, served = [], []

    def call(i):
        try:
            client, _ = fake_client(reply=f"reply {i}")
            llm = LLMClient(make_settings(tmp_path), client=client)
            served.append(llm.chat([{"role": "user", "content": f"q{i % 3}"}], prompt_version="t").text)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    in_threads(30, call)
    assert errors == [] and len(served) == 30
    cached = LLMClient(make_settings(tmp_path), client=fake_client(fail=AssertionError("not cached"))[0])
    assert all(cached.chat([{"role": "user", "content": f"q{k}"}], prompt_version="t").cached for k in range(3))


# ============================== 3. External failures ===========================================

def scripted(*steps):
    """A MockTransport handler that raises or returns each step in turn."""
    steps = iter(steps)

    def handler(request):
        step = next(steps)
        if isinstance(step, Exception):
            raise step
        return step
    return handler


def test_dropped_connection_is_retried_then_succeeds():
    c = PoliteClient(transport=httpx.MockTransport(scripted(httpx.RemoteProtocolError("peer closed"),
                                                            httpx.Response(200, json={"ok": 1}))),
                     sleep=lambda s: None)
    assert c.get("https://e.test/x").json() == {"ok": 1}


def test_endless_read_timeouts_raise_after_the_retries():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    c = PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, retries=3)
    with pytest.raises(httpx.ReadTimeout):
        c.get("https://e.test/x")
    assert len(calls) == 4


def offline_kb(tmp_path):
    repo = SQLiteRepository(tmp_path / "kb.sqlite")
    repo.migrate()
    write_parquet(tmp_path / "raw", [("8C_1/2025", "2025-10-02", None, SUVA_CASE)])
    return repo


@pytest.mark.parametrize("response", [httpx.Response(200, text="<html>Wartungsarbeiten</html>"),
                                      httpx.Response(500), httpx.Response(200, json={"hits": None})])
def test_entscheidsuche_outage_marks_the_source_failed_and_keeps_opencaselaw(tmp_path, response):
    repo = offline_kb(tmp_path)
    client = PoliteClient(transport=httpx.MockTransport(lambda r: response), sleep=lambda s: None, retries=1)
    rep = fetch(repo, tmp_path / "raw", DEV, client=client)
    assert rep.source_runs["entscheidsuche"]["status"] == "failed" and rep.errors
    assert list(rep.sightings) == ["8C_1/2025@2025-10-02"] and rep.sightings["8C_1/2025@2025-10-02"]["has_text"]
    assert repo.list_audit(kind="fetch_run")


def test_attachment_timeout_is_reported_and_the_decision_stays_without_text(tmp_path):
    repo = offline_kb(tmp_path)
    day, hit = es_hit("8C_3/2025", "2025-10-04")

    def handler(request):
        if request.url.path.endswith(".html"):
            raise httpx.ReadTimeout("slow")
        rng = json.loads(request.content)["query"]["bool"]["filter"][1]["range"]["date"]
        hits = [hit] if rng["gte"] <= day <= rng["lte"] else []
        return httpx.Response(200, json={"hits": {"total": {"value": len(hits)}, "hits": hits}})

    rep = fetch(repo, tmp_path / "raw", DEV,
                client=PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, retries=1))
    assert rep.errors == ["entscheidsuche HTML 8C_3/2025@2025-10-04: slow"]
    assert rep.still_without_text == ["8C_3/2025@2025-10-04"]
    assert repo.get_judgement("8C_1/2025@2025-10-02")["text"]  # the other decision is unaffected


class DroppedStream:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, size):
        yield b"PAR1 partial snapshot"
        raise httpx.RemoteProtocolError("peer closed")


def test_snapshot_download_dropped_midstream_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(opencaselaw.httpx, "stream", lambda *a, **k: DroppedStream())
    with pytest.raises(httpx.RemoteProtocolError):
        opencaselaw.download_bger(tmp_path / "bger.parquet")
    assert list(tmp_path.iterdir()) == []


def test_snapshot_download_dropped_midstream_keeps_the_previous_snapshot(tmp_path, monkeypatch):
    write_parquet(tmp_path, [("8C_1/2025", "2025-10-02", None, SUVA_CASE)])
    path = tmp_path / "opencaselaw" / "bger.parquet"
    before = path.read_bytes()
    monkeypatch.setattr(opencaselaw.httpx, "stream", lambda *a, **k: DroppedStream())
    with pytest.raises(httpx.RemoteProtocolError):
        opencaselaw.download_bger(path, refresh=True)
    assert path.read_bytes() == before and sorted(p.name for p in path.parent.iterdir()) == ["bger.parquet"]


@pytest.mark.parametrize("failure, error", [
    (TimeoutError("timed out"), "ElevenLabs could not generate the briefing"),
    (URLError("connection refused"), "ElevenLabs could not generate the briefing"),
    (ConnectionResetError("reset by peer"), "ElevenLabs could not generate the briefing"),
    (HTTPError("https://api.elevenlabs.io", 500, "Server Error", {}, None), "ElevenLabs returned HTTP 500"),
])
def test_briefing_network_failure_is_stored_and_the_retry_succeeds(client, case, failure, error):
    record = dashboard.load_record(case)
    args = (dashboard.connect, dashboard.app.config["DATABASE"], "cache-key", record, "Genehmigt.", "de",
            CONFIG, "api-key", "voice")
    with patch("briefing.write_script", return_value=("Skript", ["Zeile"])) as script, \
            patch("briefing.narrate", side_effect=[failure, b"ID3"]):
        assert briefing.generate(*args) is False
        with closing(dashboard.connect()) as db:
            row = db.execute("SELECT script, mp3, error FROM briefings WHERE cache_key='cache-key'").fetchone()
        assert (row["script"], row["mp3"], row["error"]) == ("Skript", None, error)
        assert briefing.generate(*args) is True
        assert script.call_count == 1  # the stored script is reused; only the audio is retried


@pytest.mark.parametrize("failure", [TimeoutError("t"), ConnectionResetError("reset"), URLError("refused"),
                                     HTTPError("https://api.elevenlabs.io", 503, "Busy", {}, None)])
def test_transcription_network_failures_are_502(client, case, failure):
    with patch("conversation.voice_config", return_value=("key", "voice")), \
            patch("conversation.urlopen", side_effect=failure):
        response = client.post("/conversation/transcribe", data={
            "case": case, "lang": "de", "audio": (BytesIO(b"rec"), "clip", "audio/webm")})
    assert response.status_code == 502 and "key" not in response.get_data(as_text=True)


@pytest.mark.parametrize("body", [b'{"text": 5}', b'{"text": null}', b'{"text": ["a"]}', b"", b"\xff"])
def test_malformed_transcription_responses_are_502(client, case, body):
    with patch("conversation.voice_config", return_value=("key", "voice")), \
            patch("conversation.urlopen", return_value=BytesIO(body)):
        response = client.post("/conversation/transcribe", data={
            "case": case, "lang": "de", "audio": (BytesIO(b"rec"), "clip", "audio/webm")})
    assert response.status_code == 502


@pytest.mark.parametrize("error", ["timeout", "connection"])
def test_apertus_timeouts_and_dropped_connections_become_a_generic_oserror(monkeypatch, error):
    from openai import APIConnectionError, APITimeoutError

    request = httpx.Request("POST", "https://apertus.test")
    exc = APITimeoutError(request=request) if error == "timeout" else APIConnectionError(request=request)

    class FakeLLM:
        def __init__(self, settings):
            self.client = httpx.Client()

        def chat(self, *a, **k):
            raise exc

    monkeypatch.setattr(bger, "LLMClient", FakeLLM)
    with pytest.raises(OSError, match="^Apertus request failed$"):
        bger.chat({"settings": None}, [], 10)


def test_apertus_timeout_during_a_summary_job_is_stored_as_retryable(client, case, monkeypatch):
    monkeypatch.setattr(dashboard, "make_facts", lambda c, t: (_ for _ in ()).throw(TimeoutError("slow")))
    assert dashboard.process(case, "facts", config=CONFIG) is False
    record = dashboard.load_record(case)
    with closing(dashboard.connect()) as db:
        assert dashboard.get_result(db, record, "facts", CONFIG)["error"] == "Apertus request failed. Try again."
    monkeypatch.setattr(dashboard, "make_facts", lambda c, t: {"facts": "f"})
    assert dashboard.process(case, "facts", config=CONFIG) is True  # a failed row does not block the retry
