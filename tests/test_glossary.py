from suva import glossary


def test_glossary_loads_and_every_pair_has_both_languages():
    terms = glossary.load_glossary()
    assert {"de": "UVG", "fr": "LAA"} in [{"de": t.de, "fr": t.fr} for t in terms]
    assert all(t.de and t.fr for t in terms)


def test_abbreviations_match_whole_words_case_sensitively():
    assert glossary.occurs("OR", "Art. 41 OR")
    assert not glossary.occurs("OR", "or le tribunal")
    assert not glossary.occurs("IV", "Die IV-Stelle verfügte")
    assert glossary.occurs("IV-Stelle", "Die IV-Stelle verfügte")
    assert not glossary.occurs("Beschwerdeführer", "Die Beschwerdeführerin rügt")


def test_relevant_terms_and_warnings_follow_the_target_language():
    pairs = glossary.relevant_terms("Gemäss Art. 6 UVG besteht ein adäquater Kausalzusammenhang.")
    assert {"de": "UVG", "fr": "LAA"} in pairs
    assert {"de": "adäquater Kausalzusammenhang", "fr": "lien de causalité adéquate"} in pairs
    assert glossary.warnings("Selon l’art. 6 LAA, le lien de causalité adéquate est donné.", "fr") == []
    assert {"found": "UVG", "expected": "LAA"} in glossary.warnings("Selon l'art. 6 UVG.", "fr")


def test_unflagged_pairs_are_hints_only():
    assert glossary.warnings("Die prime wurde erhöht.", "de") == []
