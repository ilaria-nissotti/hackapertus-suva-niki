"""Offline acceptance contracts for feat/pipeline-extensions.

Endpoint and response names below are proposed where the feature brief leaves
them open. Update those adapters if the implementation chooses another API.
Run with --runxfail to see missing entry points as failures.
"""

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

import summaries
from conftest import seed_kb
from test_flow_review_app import add_draft, approve, case, client, page
from test_review_app import SUVA_CASE

ROOT = Path(__file__).resolve().parents[1]
DE = "Das Gericht bestätigt die Leistungspflicht nach Art. 6 UVG und BGE 129 V 177."
FR = "Le tribunal confirme la prise en charge selon l’art. 6 LAA et l’ATF 129 V 177."


class MissingExtension(Exception):
    """The public entry point for a pending feature is absent."""


pending = pytest.mark.xfail(strict=True, raises=MissingExtension, reason="Extension entry point pending")


def feature_response(response):
    if response.status_code == 404 and not response.is_json:
        raise MissingExtension(response.request.path)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


@pytest.mark.parametrize(("lang", "wrong", "reply"), [("de", "LAA", DE), ("fr", "UVG", FR)])
def test_draft_injects_relevant_term_pair_and_flags_wrong_language_token(monkeypatch, lang, wrong, reply):
    if not (ROOT / "rules" / "glossary.yaml").exists():
        raise MissingExtension("rules/glossary.yaml")
    prompts = []

    def fake_chat(config, messages, tokens):
        prompts.extend(message["content"] for message in messages)
        correct = {"de": "UVG", "fr": "LAA"}[lang]
        return json.dumps({"summary": reply.replace(correct, wrong), "evidence_ids": [1]})

    monkeypatch.setattr(summaries, "chat", fake_chat)
    result = summaries.draft({}, {"docket": "8C_71/2025", "full_text": DE}, {}, lang, [])
    assert any("UVG" in prompt and "LAA" in prompt for prompt in prompts)
    assert wrong in json.dumps(result["glossary_warnings"], ensure_ascii=False)


def test_library_indexes_approvals_by_language_and_replaces_old_text(client, case):
    de_version = add_draft(case, "de", "Entwurfskennung")
    fr_version = add_draft(case, "fr", "Brouillon")
    assert approve(client, case, de_version, "Kausalitätskennung nach Art. 6 UVG.").status_code == 200

    def search(word, **facets):
        response = feature_response(client.get("/api/library", query_string={"q": word, **facets}))
        return [(hit["key"], hit["language"]) for hit in response.json["hits"]]

    assert search("Kausalitätskennung", language="de") == [(case, "de")]
    assert search("Kausalitätskennung", language="fr") == []
    assert search("Entwurfskennung") == []
    assert search("Brouillon") == []
    assert approve(client, case, fr_version, FR, lang="fr").status_code == 200
    assert search("prise", language="fr") == [(case, "fr")]
    response = approve(client, case, de_version, "Ersatzkennung.",
                       previous="Kausalitätskennung nach Art. 6 UVG.")
    assert response.status_code == 200
    assert search("Kausalitätskennung") == []
    assert search("Ersatzkennung") == [(case, "de")]


def test_case_search_boosts_citations_and_returns_explanations(client, case, tmp_path):
    cited = seed_kb(tmp_path / "kb.sqlite", "8C_72/2026", f"{SUVA_CASE}\n\nE. 2. {DE}")
    response = feature_response(client.post("/api/case-search", json={
        "description": "Unfallversicherung und Leistungspflicht nach Art. 6 UVG, BGE 129 V 177",
    }))
    hits = response.json["hits"]
    assert hits[0]["key"] == cited
    assert hits[0]["matches"]
    assert hits[0]["highlights"]
    assert any(hit["key"] == case for hit in hits[1:])


def test_triage_records_reviewer_reason_without_replacing_rule_score(client, case):
    before = page(client, case=case)["selected"]["score"]
    feedback = {"case": case, "status": "Relevant", "reason": "Die Erwägung präzisiert den Kausalzusammenhang."}
    feature_response(client.post("/api/triage-feedback", json=feedback))

    selected = page(client, case=case)["selected"]
    assert selected["score"] == before
    assert selected["triage_feedback"]["status"] == feedback["status"]
    assert selected["triage_feedback"]["reason"] == feedback["reason"]


@pytest.mark.parametrize("format", ["html", "docx", "pdf"])
def test_export_uses_only_approved_bilingual_text(client, case, format):
    de_version = add_draft(case, "de", "Entwurfskennung")
    fr_version = add_draft(case, "fr", "Brouillon")
    assert approve(client, case, de_version, DE).status_code == 200
    assert approve(client, case, fr_version, FR, lang="fr").status_code == 200
    response = feature_response(client.get("/api/export", query_string={"case": case, "format": format}))
    if format == "pdf":
        assert response.mimetype == "application/pdf"
        assert response.data.startswith(b"%PDF-")
        return
    if format == "docx":
        with ZipFile(BytesIO(response.data)) as archive:
            body = archive.read("word/document.xml").decode("utf-8")
    else:
        assert response.mimetype == "text/html"
        body = response.get_data(as_text=True)
    assert DE in body and FR in body
    assert "Entwurfskennung" not in body and "Brouillon" not in body
