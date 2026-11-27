from suva.parse.citations import extract


def canon(text, **kw):
    return [c.canonical for c in extract(text, **kw)]


def test_statutes_de_fr_it_map_to_one_name():
    assert canon("Nach Art. 6 Abs. 2 UVG gilt") == ["Art. 6 Abs. 2 UVG"]
    assert canon("selon l'art. 6 al. 2 LAA") == ["Art. 6 Abs. 2 UVG"]
    assert canon("giusta l'art. 6 cpv. 2 LAINF") == ["Art. 6 Abs. 2 UVG"]
    assert canon("Art. 4 ATSG und art. 4 LPGA") == ["Art. 4 ATSG", "Art. 4 ATSG"]


def test_qualifiers_and_suffixes():
    assert canon("Art. 37 Abs. 2 lit. b und Abs. 3 lit. b IVV") == ["Art. 37 Abs. 2 IVV"]
    assert canon("Gemäss Art. 88bis Abs. 2 IVV") == ["Art. 88bis Abs. 2 IVV"]
    assert canon("art. 73 let. b OLAA") == ["Art. 73 UVV"]
    assert canon("les art. 82 ss LTF") == ["Art. 82 BGG"]
    assert canon("in Art. 90 bis 93 BGG") == ["Art. 90 BGG"]
    assert canon("Art. 29 Abs. 2 BV und Art. 6 Ziff. 1 EMRK") == ["Art. 29 Abs. 2 BV", "Art. 6 EMRK"]


def test_no_false_law_match():
    assert canon("Art. 5 des Reglements") == []
    assert canon("Art. 12 COVID-Verordnung") == []  # 'CO' must not match inside COVID


def test_bge_atf_dtf_and_consideration():
    cs = extract("vgl. BGE 134 V 109 E. 2.1; ATF 146 V 51 consid. 5.1 et DTF 140 V 356")
    assert [c.canonical for c in cs] == ["BGE 134 V 109", "BGE 146 V 51", "BGE 140 V 356"]
    assert [c.consideration for c in cs] == ["2.1", "5.1", None]


def test_judgement_refs_skip_self():
    t = "Urteil 9C_547/2022 vom 5. Dezember 2022; im Verfahren 8C_129/2025"
    assert canon(t, self_docket="8C_129/2025") == ["9C_547/2022"]


def test_offsets_point_at_raw():
    t = "x Art. 6 Abs. 2 UVG y"
    c = extract(t)[0]
    assert t[c.start:c.end] == c.raw and c.law == "UVG" and c.article == "6"
