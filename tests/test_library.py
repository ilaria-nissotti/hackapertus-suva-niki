import json

import summaries
from test_flow_review_app import CONFIG, add_draft, approve, case, client  # noqa: F401
from test_pipeline_extensions import DE, FR


def test_library_facets_and_filters(client, case):
    assert approve(client, case, add_draft(case, "de"), DE).status_code == 200
    assert approve(client, case, add_draft(case, "fr"), FR, lang="fr").status_code == 200
    body = client.get("/api/library").json
    assert body["total"] == 2
    assert {"value": "UVG", "count": 2} in body["facets"]["statute"]
    assert {f["value"] for f in body["facets"]["language"]} == {"de", "fr"}
    assert body["hits"][0]["decision_date"] == "2026-10-01"
    assert client.get("/api/library", query_string={"statute": "IVG"}).json["hits"] == []
    assert client.get("/api/library", query_string={"date_from": "2026-10-02"}).json["hits"] == []
    assert client.get("/api/library", query_string={"date_from": "nope"}).status_code == 400
    hit = client.get("/api/library", query_string={"q": "Leistungspflicht"}).json["hits"][0]
    assert "⟦Leistungspflicht⟧" in hit["snippet"]


def test_library_query_operators_are_plain_text(client, case):
    assert approve(client, case, add_draft(case, "de"), DE).status_code == 200
    assert client.get("/api/library", query_string={"q": 'UVG OR "NEAR('}).status_code == 200


def test_ask_cites_only_retrieved_cases(client, case, monkeypatch):
    import app as dashboard

    assert approve(client, case, add_draft(case, "de"), DE).status_code == 200
    seen = []

    def fake_chat(config, messages, tokens):
        data = json.loads(messages[-1]["content"])
        seen.append([s["case"] for s in data["summaries"]])
        return json.dumps({"answer": "Ja, nach Art. 6 UVG.", "cited": [case, "9C_9/2020@2020-01-01"]})

    monkeypatch.setattr(summaries, "chat", fake_chat)
    monkeypatch.setattr(dashboard, "model_config", lambda: CONFIG)
    body = client.post("/api/library/ask", json={"question": "Wann besteht Leistungspflicht nach UVG?"}).json
    assert seen == [[case]]
    assert body["answer"] == "Ja, nach Art. 6 UVG."
    assert [c["key"] for c in body["cited"]] == [case]
    none = client.post("/api/library/ask", json={"question": "Mutterschaftsentschädigung?"}).json
    assert none["answer"] is None
    assert client.post("/api/library/ask", json={"question": " "}).status_code == 400


def test_statute_facet_includes_laws_the_judgement_cites_and_topics_have_labels(client, case):
    # The approved text cites no statute; the judgement cites Art. 6 Abs. 1 UVG.
    assert approve(client, case, add_draft(case, "de"), "Die Beschwerde wird abgewiesen.").status_code == 200
    body = client.get("/api/library").json
    assert {"value": "UVG", "count": 1} in body["facets"]["statute"]
    assert body["hits"][0]["statutes"] == ["UVG"]
    assert all(topic["label"] and topic["label"] != topic["value"] for topic in body["facets"]["topic"])
    assert client.get("/api/library", query_string={"statute": "UVG"}).json["total"] == 1
