from summaries import similar_examples


def test_style_examples_exclude_the_same_docket_and_use_the_requested_language():
    examples = [{"docket": "8C_1/2025", "text_de": "Unfallbegriff Kausalität", "text_fr": "causalité"}]
    assert similar_examples("Kausalität", "de", "8C_9/2026", examples) == ["Unfallbegriff Kausalität"]
    assert similar_examples("causalité", "fr", "8C_9/2026", examples) == ["causalité"]
    assert similar_examples("Kausalität", "de", "8C_1/2025", examples) == []


def test_french_draft_retries_a_german_model_response(monkeypatch):
    import summaries

    responses = iter([
        {"summary": "Das Gericht weist die Beschwerde ab und bestätigt den Entscheid.", "evidence_ids": [1]},
        {"summary": "Le Tribunal fédéral rejette le recours et confirme la décision.", "evidence_ids": [1]},
    ])
    monkeypatch.setattr(summaries, "ask", lambda *args, **kwargs: next(responses))
    record = {"docket": "8C_9/2026", "full_text": "Das Gericht weist die Beschwerde ab und bestätigt den Entscheid."}
    assert summaries.draft({}, record, {}, "fr", [])["summary"].startswith("Le Tribunal fédéral")


def test_factual_brief_retries_an_invalid_citation(monkeypatch):
    import summaries

    fields = {"facts": "facts", "issues": "issues", "reasoning": "reasoning", "outcome": "outcome"}
    responses = iter([fields | {"evidence_ids": [99]}, fields | {"evidence_ids": [1]}])
    monkeypatch.setattr(summaries, "ask", lambda *args, **kwargs: next(responses))
    text = "Das Gericht weist die Beschwerde ab und bestätigt den Entscheid."
    assert summaries.make_facts({}, text)["quotes"] == [text]
