"""Offline acceptance contracts for feat/pipeline-extensions.

Endpoint and response names below are proposed where the feature brief leaves
them open. Update those adapters if the implementation chooses another API.
Run with --runxfail to see missing entry points as failures.
"""

import json
from contextlib import closing
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

import summaries
from conftest import seed_kb
from suva import cli
from suva.scoring.rules import load_rules
from suva.sources.base import SourceRecord
from suva.storage import SQLiteRepository
from test_flow_review_app import add_draft, approve, case, client, page
from test_flow_cli import kb, settings
from test_review_app import SUVA_CASE

ROOT = Path(__file__).resolve().parents[1]
DE = "Das Gericht bestätigt die Leistungspflicht nach Art. 6 UVG und BGE 129 V 177."
FR = "Le tribunal confirme la prise en charge selon l’art. 6 LAA et l’ATF 129 V 177."


class MissingExtension(Exception):
    """The public entry point for a pending feature is absent."""


pending = pytest.mark.xfail(strict=True, raises=MissingExtension, reason="Extension entry point pending")


def require_command(command, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    if command not in capsys.readouterr().out:
        raise MissingExtension(command)


def feature_response(response):
    if response.status_code == 404 and not response.is_json:
        raise MissingExtension(response.request.path)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def test_eval_keeps_all_held_out_references_out_of_model_requests(settings, monkeypatch, capsys):
    decided = date(2025, 10, 2)
    held_out = {
        "8C_71/2025": ("ReferenzsperreAlpha", "RéférenceSecrèteAlpha"),
        "8C_72/2025": ("ReferenzsperreBeta", "RéférenceSecrèteBeta"),
    }
    with closing(kb(settings)) as repo:
        for docket, (de_marker, fr_marker) in held_out.items():
            record = SourceRecord("opencaselaw", docket, docket, decided, text=DE)
            repo.upsert_judgement(record)
            repo.upsert_example({
                "docket": docket, "decision_date": decided.isoformat(), "title": docket, "source_file": "Export_fixture.html",
                "text_de": f"{DE}\n\n{de_marker}", "text_fr": f"{FR}\n\n{fr_marker}",
            })
            repo.set_example_split(record.key, "eval")
        repo.upsert_example({
            "docket": "8C_73/2025", "title": "Style example", "source_file": "Export_fixture.html",
            "text_de": "NurStilbeispiel UVG", "text_fr": "SeulementLeStyle LAA",
        })
        repo.set_example_split("8C_73/2025", "fewshot")

    prompts = []

    def fake_chat(config, messages, tokens):
        prompt = json.dumps(messages, ensure_ascii=False)
        prompts.append(prompt)
        for markers in held_out.values():
            assert all(marker not in prompt for marker in markers)
        result = {"evidence_ids": [1]}
        if "factual brief from the judgment" in prompt:
            result.update(facts="Leistungspflicht", issues="UVG", reasoning="Kausalität", outcome="Bestätigt")
        else:
            result["summary"] = FR if "French (Swiss legal usage)" in prompt else DE
        return json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr(summaries, "chat", fake_chat)
    require_command("eval-summaries", capsys)
    assert cli.main(["eval-summaries", "--split", "evaluation"]) == 0
    assert sum("German (Swiss legal usage)" in prompt for prompt in prompts) == 2
    assert sum("French (Swiss legal usage)" in prompt for prompt in prompts) == 2

    output = capsys.readouterr().out
    scoring_dir = settings.data_dir / "scoring"
    assert list(scoring_dir.glob("*")), "Expected an evaluation artifact under data/scoring"
    artifact = "\n".join(path.read_text(encoding="utf-8") for path in scoring_dir.iterdir() if path.is_file())
    for docket in held_out:
        assert f"{docket}@{decided}" in output
        assert f"{docket}@{decided}" in artifact
    assert "8C_73/2025" not in artifact
    for metric in ("paragraph", "length", "opening", "statute", "bge", "glossary"):
        assert metric in artifact.casefold()
    assert "aggregate" in output.casefold()


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


@pending
def test_triage_records_reviewer_reason_without_replacing_rule_score(client, case):
    before = page(client, case=case)["selected"]["score"]
    feedback = {"case": case, "status": "Relevant", "reason": "Die Erwägung präzisiert den Kausalzusammenhang."}
    feature_response(client.post("/api/triage-feedback", json=feedback))

    selected = page(client, case=case)["selected"]
    assert selected["score"] == before
    assert selected["triage_feedback"]["status"] == feedback["status"]
    assert selected["triage_feedback"]["reason"] == feedback["reason"]


@pending
def test_near_miss_explains_gap_using_rule_points_without_display_base(client, tmp_path):
    path = tmp_path / "kb.sqlite"
    key = seed_kb(path, "8C_74/2026", SUVA_CASE)
    rules = load_rules()
    floor = rules.selection.floor_rule_points
    points = floor - 1
    with closing(SQLiteRepository(path)) as repo:
        row = repo.get_score(key)
        repo.save_scores([row | {"score": rules.base + points, "points": points, "band": "low"}])
    response = feature_response(client.get("/api/near-misses"))
    assert response.json["hits"] == sorted(response.json["hits"], key=lambda hit: hit["gap_to_floor"])
    match = next(hit for hit in response.json["hits"] if hit["key"] == key)
    assert match["floor_rule_points"] == floor
    assert match["rule_points"] == points
    assert match["gap_to_floor"] == 1
    assert match["missing_signals"]


@pending
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


@pending
def test_container_configuration_keeps_secrets_and_local_databases_out_of_image():
    dockerfile = ROOT / "Dockerfile"
    compose = next((ROOT / name for name in ("compose.yaml", "compose.yml", "docker-compose.yml")
                    if (ROOT / name).is_file()), None)
    if not dockerfile.is_file() or compose is None:
        raise MissingExtension("Dockerfile and Compose configuration")
    import yaml

    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    assert len(services) >= 2, "The review app and scheduled pipeline need separate services"
    instructions = dockerfile.read_text(encoding="utf-8")
    assert ".env" not in "\n".join(line for line in instructions.splitlines() if line.upper().startswith("COPY "))
    ignores = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignores
    assert any(pattern in ignores for pattern in ("data", "data/", "data/*.sqlite", "**/*.sqlite"))
    for service in services.values():
        environment = service.get("environment") or {}
        if isinstance(environment, dict) and environment.get("LLM_API_KEY") is not None:
            assert str(environment["LLM_API_KEY"]).startswith("${")
