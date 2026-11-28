"""One judgment from source listing and HTML to the review endpoints."""

from contextlib import closing
from datetime import date
from html import escape
import json

import httpx

import app
import jobs
import summaries
from suva.fetch import fetch
from suva.http import PoliteClient
from suva.parse.run import parse_all
from suva.scoring.rules import load_rules
from suva.scoring.store import score_window
from suva.windows import Window
from test_fetch import es_hit, parquet
from test_review_app import SUVA_CASE


def test_one_judgment_from_scraper_to_approved_drafts(tmp_path, monkeypatch):
    day = date(2026, 10, 1)
    docket = "8C_9999/2026"
    key = f"{docket}@{day}"
    raw = tmp_path / "raw"
    parquet(raw, [("8C_9997/2025", "2025-01-01")])
    _, hit = es_hit(docket, str(day))
    text = f"{docket}\n\n{SUVA_CASE}"
    evidence = "Suva, Fluhmattstrasse 1, 6002 Luzern,"
    source_urls = []

    def source(request):
        source_urls.append(request.url.path)
        if request.url.path.endswith(".html"):
            html = "".join(f"<div class='para'>{escape(line)}</div>" for line in text.split("\n\n"))
            return httpx.Response(200, text=html)
        return httpx.Response(200, json={"hits": {"total": {"value": 1}, "hits": [hit]}})

    monkeypatch.setitem(app.app.config, "KB", tmp_path / "kb.sqlite")
    monkeypatch.setitem(app.app.config, "DATABASE", tmp_path / "review.sqlite")
    monkeypatch.setitem(app.app.config, "TODAY", date(2026, 11, 27))
    window = Window("fixture", day, day)
    with closing(app.kb()) as repo, closing(
        PoliteClient(transport=httpx.MockTransport(source), sleep=lambda _: None)
    ) as http:
        report = fetch(repo, raw, window, client=http, include_picks=False)
        assert report.new_judgements == 1 and report.text_fallback == [key] and not report.errors
        assert any(path.endswith(".html") for path in source_urls)
        assert repo.get_judgement(key)["text_source"] == "entscheidsuche"
        assert parse_all(repo).status == {"ok": 1}
        assert any(citation["law"] == "UVG" for citation in repo.list_citations(key))
        assert score_window(repo, window, load_rules()).scored == 1
        parse_all(repo, force=True)

    client = app.app.test_client()
    headers = {"Accept": "application/json"}

    def dashboard():
        return client.get("/api/dashboard", query_string={"case": key}).json

    assert dashboard()["selected"]["status"] == "Stale"
    with closing(app.kb()) as repo:
        score_window(repo, window, load_rules())
    assert dashboard()["rows"][0]["issue"]
    assert dashboard()["selected"]["score_current"]

    class InlineExecutor:
        def submit(self, task):
            task()

    config = {"LLM_NAME": "fixture-model", "LLM_BASE_URL": "https://fixture.example/v1"}
    monkeypatch.setattr(jobs, "pool", InlineExecutor())
    monkeypatch.setattr(app, "model_config", lambda: config)
    monkeypatch.setattr(jobs, "model_config", lambda: config)
    seen = []
    drafts = {
        "de": "Das Gericht weist die Beschwerde ab und bestätigt den Entscheid.",
        "fr": "Le Tribunal fédéral rejette le recours et confirme la décision.",
    }

    def chat(_config, messages, _max_tokens):
        data, _ = json.JSONDecoder().raw_decode(messages[-1]["content"])
        original = data["judgment_lines"]
        passage = next(key for key, value in original.items() if evidence in value)
        if "style_examples" not in data:
            return json.dumps({"facts": "facts", "issues": "issues", "reasoning": "reasoning",
                               "outcome": "outcome", "evidence_ids": [passage]})
        assert data["style_examples"] == []
        lang = "fr" if "Rédige le résumé" in messages[-1]["content"] else "de"
        seen.append(lang)
        return json.dumps({"summary": drafts[lang], "evidence_ids": [passage]})

    monkeypatch.setattr(summaries, "chat", chat)
    assert client.post("/run", data={"case": key, "action": "summarize"}, headers=headers).status_code == 200
    assert seen == ["de", "fr"]
    first = dashboard()["selected"]
    assert first["de"]["data"]["quotes"] == [evidence]

    config["LLM_BASE_URL"] = "https://fixture.example/replacement"
    assert client.post("/run", data={"case": key, "action": "summarize"}, headers=headers).status_code == 200
    second = dashboard()["selected"]
    assert second["de"]["version"] != first["de"]["version"]
    assert client.post("/approve", headers=headers, data={
        "case": key, "lang": "de", "version": first["de"]["version"], "summary": "Old draft",
    }).status_code == 409
    for lang in ("de", "fr"):
        assert client.post("/approve", headers=headers, data={
            "case": key, "lang": lang, "version": second[lang]["version"],
            "summary": second[lang]["data"]["summary"],
        }).status_code == 200
    assert [dashboard()["selected"][lang]["approved"] for lang in ("de", "fr")] == [
        drafts["de"], drafts["fr"],
    ]
