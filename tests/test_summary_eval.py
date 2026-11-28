from suva.summary_eval import EvalReport, compare

REFERENCE = ("Die Unfallversicherung muss nach Art. 6 Abs. 1 UVG leisten (BGE 129 V 177).\n\n"
             "Der adäquate Kausalzusammenhang ist gegeben; der Einspracheentscheid wird aufgehoben.")


def test_identical_draft_scores_full_marks():
    m = compare(REFERENCE, REFERENCE, "de")
    assert m["paragraphs_draft"] == m["paragraphs_reference"] == 2
    assert m["length_ratio"] == 1.0
    assert m["opening_overlap"] == m["content_f1"] == 1.0
    assert m["statute_recall"] == m["bge_recall"] == m["glossary_recall"] == 1.0
    assert m["glossary_warnings"] == 0


def test_missing_citations_and_wrong_language_terms_are_measured():
    draft = "Die Unfallversicherung muss nach Art. 6 LAA leisten."
    m = compare(draft, REFERENCE, "de")
    assert m["bge_recall"] == 0.0
    assert m["statute_recall"] == 0.0  # canonical Art. 6 UVG ≠ Art. 6 Abs. 1 UVG
    assert m["glossary_warnings"] == 1
    assert 0 < m["content_f1"] < 1


def test_metrics_without_reference_citations_are_left_out_of_the_aggregate():
    report = EvalReport("eval", "m", cases=[
        {"key": "a", "de": {"metrics": compare("Kurz.", "Kein Zitat hier.", "de")}, "fr": {"error": "draft failed"}},
        {"key": "b", "de": {"metrics": compare(REFERENCE, REFERENCE, "de")}, "fr": {"error": "draft failed"}},
    ])
    aggregate = report.aggregate()
    assert aggregate["de"]["drafts"] == 2
    assert aggregate["de"]["bge_recall"] == 1.0
    assert aggregate["fr"]["drafts"] == 0
    assert report.failures == 2
