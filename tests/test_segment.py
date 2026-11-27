from suva.parse.segment import _plausible_next, paragraphs, segment

DE = """Bundesgericht

8C_185/2025

Urteil vom 4. Dezember 2025

IV. öffentlich-rechtliche Abteilung

Besetzung

Bundesrichterin Viscione, Präsidentin.

Verfahrensbeteiligte

Suva, Fluhmattstrasse 1, 6002 Luzern,
Beschwerdeführerin,

gegen

A.________,
Beschwerdegegner.

Gegenstand

Unfallversicherung (Kausalzusammenhang),

Sachverhalt:

A.

Der Versicherte stürzte am 20. Dezember 2023.

B.

Das kantonale Gericht hiess die Beschwerde gut.

Erwägungen:

1.

Die Beschwerde ist zulässig (
Art. 82 BGG
).

2.

2.1. Streitig ist die natürliche Kausalität.

2.2. Nach Art. 6 Abs. 1 UVG gilt …

20. Dezember 2023 war ein Sonntag.

3.

Die Beschwerde ist begründet.

Demnach erkennt das Bundesgericht:

1.

Die Beschwerde wird gutgeheissen.

Luzern, 4. Dezember 2025
""" + ("Lorem ipsum dolor sit amet. " * 30)

ORDER = """Bundesgericht

9C_526/2025

Gegenstand

Krankenversicherung,

Nach Einsicht

in die Beschwerde vom 12. September 2025,

in Erwägung,

dass die Beschwerde offensichtlich unzulässig ist,

erkennt der Einzelrichter:

1.

Auf die Beschwerde wird nicht eingetreten.
""" + ("x " * 600)


def ids(seg):
    return [p.pid.split("#", 1)[1] for p in seg.passages]


def test_plausible_numbering():
    assert _plausible_next(None, (1,))
    assert _plausible_next((2,), (2, 1)) and _plausible_next((2, 1), (2, 2)) and _plausible_next((2, 2), (3,))
    assert not _plausible_next((2, 2), (20,)) and not _plausible_next((3,), (1,))


def test_full_decision_de():
    s = segment("8C_185/2025@2025-12-04", DE)
    assert s.status == "ok", s.issues
    assert ids(s) == ["header", "facts", "reasons", "E.1", "E.2", "E.2.1", "E.2.2", "E.3", "dispositiv"]
    e22 = next(p for p in s.passages if p.number == "2.2")
    assert "20. Dezember 2023 war ein Sonntag." in e22.text  # date not taken as E.20
    assert "Art. 82 BGG\n)" in next(p for p in s.passages if p.number == "1").text
    assert s.chamber == "IV. öffentlich-rechtliche Abteilung"
    assert s.parties.startswith("Suva, Fluhmattstrasse")
    assert s.subject == "Unfallversicherung (Kausalzusammenhang),"


def test_no_text_lost():
    s = segment("k", DE)
    previous_end = 0
    for passage in s.passages:
        assert passage.text == DE[passage.start:passage.end]
        assert not DE[previous_end:passage.start].strip()
        assert previous_end <= passage.start
        previous_end = passage.end
    assert not DE[previous_end:].strip()


def test_short_order_is_ok_without_numbers():
    s = segment("9C_526/2025@2025-10-03", ORDER)
    assert s.status == "ok", s.issues
    assert ids(s) == ["header", "reasons", "dispositiv"]
    assert "no_numbered_considerations" in s.issues


def test_italian_and_french_headings():
    it = DE.replace("Sachverhalt:", "Fatti:").replace("Erwägungen:", "Diritto:").replace(
        "Demnach erkennt das Bundesgericht:", "Per questi motivi, il Tribunale federale pronuncia:")
    fr = DE.replace("Sachverhalt:", "Faits :").replace("Erwägungen:", "Considérant en droit :").replace(
        "Demnach erkennt das Bundesgericht:", "Par ces motifs, le Tribunal fédéral prononce :")
    for t in (it, fr):
        s = segment("k", t)
        assert s.status == "ok" and "E.2.2" in ids(s) and ids(s)[-1] == "dispositiv"


def test_no_reasons_keeps_everything_as_full():
    s = segment("k", "Bundesgericht\n\nGegenstand\n\nUnfall\n\n" + "Freier Text ohne Struktur. " * 60)
    assert s.status == "failed" and "no_reasons_heading" in s.issues
    assert "full" in ids(s) and "Freier Text" in next(p for p in s.passages if p.kind == "full").text


def test_empty_and_short():
    assert segment("k", None).issues == ["no_text"]
    s = segment("k", "Erwägungen:\n\n1.\n\nkurz\n\nDemnach erkennt das Bundesgericht:\n\n1.\n\nx")
    assert "too_short" in s.issues and s.status == "partial"


def test_missing_dispositiv_is_partial():
    s = segment("k", DE.split("Demnach erkennt")[0] + "y " * 500)
    assert s.status == "partial" and "no_dispositiv" in s.issues


def test_combined_italian_heading_and_french_order():
    it = DE.replace("Sachverhalt:\n\nA.\n\nDer Versicherte stürzte am 20. Dezember 2023.\n\nB.\n\nDas kantonale Gericht hiess die Beschwerde gut.\n\nErwägungen:",
                    "Ritenuto in fatto e considerando in diritto:")
    s = segment("k", it)
    assert s.status == "ok" and "E.2.1" in ids(s), s.issues
    fr = ("Tribunal fédéral\n\n4D_217/2025\n\nObjet\n\ncontrat de travail\n\nLe Président :\n\n"
          "vu le recours,\n\nque les frais peuvent être réduits,\n\nOrdonne :\n\n1.\n\nLa cause est rayée du rôle.\n\n"
          "Le Président : Hurni") + " x" * 600
    s = segment("k", fr)
    assert "no_reasons_heading" not in s.issues and ids(s)[-1] == "dispositiv"
    assert "Le Président : Hurni" in s.passages[-1].text  # signature, not a heading


def test_error_page_is_flagged_not_split():
    s = segment("k", "menu\n\nListe der Neuheiten\n\nDokument nicht gefunden Dieser AZA-Entscheid ist in elektronischer Form nicht verfügbar.")
    assert s.status == "failed" and s.issues == ["source_error_page"] and s.passages == []


def test_single_judge_heading_variants():
    for heading in ("Die Präsidentin zieht in Erwägung:", "La Juge présidant:", "Il Presidente:",
                    "Das Bundesgericht zieht in Erwägung:", "Das präsidierende Mitglied zieht in Erwägung:"):
        t = ("Bundesgericht\n\nGegenstand\n\nRevision\n\n" + heading +
             "\n\n1.\n\nDie Beschwerde ist unzulässig.\n\n2.\n\nKosten.\n\nDemnach erkennt die Präsidentin:\n\n1.\n\nNichteintreten.") + " x" * 600
        s = segment("k", t)
        assert s.status == "ok" and ids(s)[:4] == ["header", "reasons", "E.1", "E.2"], (heading, s.issues)


def test_das_bundesgericht_erkennt():
    s = segment("k", DE.replace("Demnach erkennt das Bundesgericht:", "Das Bundesgericht erkennt:"))
    assert s.status == "ok" and ids(s)[-1] == "dispositiv"


def test_dispositiv_does_not_overlap_header_without_reasons():
    text = "Bundesgericht\n\nGegenstand\n\nDemnach erkennt das Bundesgericht:\n\nAbgewiesen."
    result = segment("key", text)
    header, outcome = result.passages
    assert header.end <= outcome.start
    assert outcome.text.startswith("Demnach erkennt")


def test_singular_german_reason_heading():
    result = segment("k", DE.replace("Erwägungen:", "Erwägung:"))
    assert result.status == "ok"
    assert "E.2.2" in ids(result)


BGE_DE = """Urteilskopf

151 V 1

1. Auszug aus dem Urteil der III. öffentlich-rechtlichen Abteilung i.S. A. gegen X. Versicherung (Beschwerde)

9C_480/2022 vom 29. August 2024

Regeste

Art. 69 Abs. 1 ATSG; Kongruenz der Leistungen.

Die Hilflosenentschädigung ist nicht anzurechnen (E. 6).

Sachverhalt ab Seite 3

    BGE 151 V 1 S. 3

    A. Die Versicherte leidet an einer Erkrankung.

Erwägungen

Aus den Erwägungen:

6.

6.1 Nach Art. 69 Abs. 1 ATSG darf das Zusammentreffen von Leistungen nicht zu einer Überentschädigung führen.

6.2 An dieser Sichtweise ist nicht festzuhalten. BGE 151 V 1 S. 4

7. Die Beschwerde ist begründet.
""" + ("Weitere Erwägung. " * 60)


def test_bge_extract_layout():
    s = segment("9C_480/2022@2024-08-29", BGE_DE)
    assert s.status == "ok", s.issues
    assert ids(s) == ["header", "regeste", "facts", "reasons", "E.6", "E.6.1", "E.6.2", "E.7"]
    assert "bge_extract" in s.issues and "no_dispositiv" not in s.issues  # extracts omit the Dispositiv
    assert s.passages[1].text.startswith("Regeste\n\nArt. 69 Abs. 1 ATSG")


def test_bge_extract_reasons_with_page_and_no_facts():
    t = BGE_DE.replace(BGE_DE[BGE_DE.index("Sachverhalt ab Seite 3"):BGE_DE.index("Erwägungen\n")], "")
    t = t.replace("Erwägungen\n\nAus den Erwägungen:", "Erwägungen ab Seite 343\n\n    BGE 151 V 1 S. 343\n\nAus den Erwägungen:")
    s = segment("k", t)
    assert s.status == "ok" and ids(s)[:4] == ["header", "regeste", "reasons", "E.6"]


def test_extract_start_rule_only_for_bge():
    # an ordinary judgement must still start at E. 1: "6." right after the heading is not trusted
    t = DE.replace("Erwägungen:\n\n1.\n\n", "Erwägungen:\n\n6.\n\n")
    assert "E.6" not in ids(segment("k", t))


def test_bge_excerpt_can_start_mid_subsection_and_skip_numbers():
    text = BGE_DE.replace("Erwägungen\n\nAus den Erwägungen:", "Aus den Erwägungen:")
    text = text[:text.index("\n\n6.\n\n")] + (
        "\n\n5. Januar 2024 war ein Datum."
        "\n\n29 août 2024."
        "\n\n30 agosto 2024."
        "\n\n6.2.3 Ein Erwägungsabschnitt."
        "\n\n6.2.3 Wiederholte Nummer im Text."
        "\n\n8.2 Eine weitere Erwägung."
    ) + (" Weitere Erwägung." * 60)
    result = segment("k", text)
    assert result.status == "ok"
    assert ids(result)[-3:] == ["reasons", "E.6.2.3", "E.8.2"]
    assert "5. Januar" in result.passages[-3].text
    assert "Wiederholte Nummer" in result.passages[-2].text
    assert all(p.text == text[p.start:p.end] for p in result.passages)
