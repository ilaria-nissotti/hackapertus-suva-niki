import case_search

TEXT_A = "Die Unfallversicherung lehnte Leistungen ab. Nach Art. 6 Abs. 1 UVG und BGE 129 V 177 fehlt die Kausalität."
TEXT_B = "Die Invalidenversicherung prüfte den Rentenanspruch nach Art. 28 IVG."


def records():
    return [{"key": k, "docket": k, "title": k, "decision_date": "2026-10-01", "status": "Relevant", "full_text": t}
            for k, t in (("a", TEXT_A), ("b", TEXT_B))]


def citations_of(key):
    return {"a": [{"kind": "statute", "canonical": "Art. 6 Abs. 1 UVG", "law": "UVG", "article": "6"},
                  {"kind": "bge", "canonical": "BGE 129 V 177", "law": None, "article": None}],
            "b": [{"kind": "statute", "canonical": "Art. 28 IVG", "law": "IVG", "article": "28"}]}[key]


def passages_of(key):
    return [{"text": t} for r in records() if r["key"] == key for t in r["full_text"].split(". ")]


def test_citations_in_the_description_lift_the_judgement_that_cites_them():
    hits = case_search.search(records(), citations_of, passages_of, "Sturz; Art. 6 UVG, BGE 129 V 177")
    assert [h["key"] for h in hits] == ["a"]
    kinds = {(m["kind"], m["value"]) for m in hits[0]["matches"]}
    assert ("bge", "BGE 129 V 177") in kinds and ("article", "Art. 6 UVG") in kinds


def test_terms_rank_and_highlight():
    hits = case_search.search(records(), citations_of, passages_of, "Rentenanspruch der Invalidenversicherung")
    assert hits[0]["key"] == "b"
    assert any("⟦Rentenanspruch⟧" in h for h in hits[0]["highlights"])


def test_empty_description_finds_nothing():
    assert case_search.search(records(), citations_of, passages_of, "und der die") == []
