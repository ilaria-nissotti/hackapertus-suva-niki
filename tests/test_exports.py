from datetime import date
from pathlib import Path

import pytest

from suva.ingest.exports import ExportParseError, parse_export, parse_export_dir

JUDGEMENTS = Path(__file__).resolve().parents[1] / "judgements"

HTML = """<!DOCTYPE html><html><body><hr><h1>{title}</h1>
<h2>Text DE</h2><p>Erster Absatz.</p><p>Zweiter Absatz.</p>
<h2>Text FR</h2><p>Premier paragraphe.</p>
<h2>Link zum Urteil</h2><p>{link}</p>
<h2>Autor</h2><p>Muster Max (MM), Rechtsanwalt</p><hr></body></html>"""
URL = "https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php?highlight_docid=aza://{aza}&lang=de&zoom=&type=show_document"


def test_basic_fields_and_paragraphs():
    html = HTML.format(title="Arrêt du TF 8C_129/2025 du 11 novembre 2025",
                       link=f'<a href="{URL.format(aza="11-11-2025-8C_129-2025")}">x</a>')
    e = parse_export(html, "f.html")
    assert (e.docket, e.decision_date, e.language) == ("8C_129/2025", date(2025, 11, 11), "fr")
    assert e.text_de == "Erster Absatz.\n\nZweiter Absatz."
    assert e.text_fr == "Premier paragraphe."
    assert e.author == "Muster Max (MM), Rechtsanwalt"
    assert e.notes == []


def test_plain_text_link_and_missing_prefix_in_title():
    # mirrors Export_8C_458_2025.html: title lacks "8C_", link is the authority
    html = HTML.format(title="BGer-Urteil 458/2025 vom 18. November 2025",
                       link=URL.format(aza="18-11-2025-8C_458-2025"))
    e = parse_export(html, "f.html")
    assert e.docket == "8C_458/2025"
    assert e.decision_date == date(2025, 11, 18)
    assert any("taken from link" in n for n in e.notes)


def test_search_hit_link_is_canonicalised():
    search = ("https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php?lang=de"
              "&type=highlight_simple_query&highlight_docid=aza://04-08-2025-4A_106-2025&rank=5")
    html = HTML.format(title="BGer-Urteil 4A_106/2025 vom 4. August 2025", link=f'<a href="{search}">x</a>')
    e = parse_export(html, "f.html")
    assert e.source_url == search
    assert e.bger_url == URL.format(aza="04-08-2025-4A_106-2025")


def test_title_link_conflict_is_noted_not_hidden():
    html = HTML.format(title="Sentenza del TF 8C_135/2025 del 21 agosto 2025",
                       link=URL.format(aza="22-08-2025-8C_135-2025"))
    e = parse_export(html, "f.html")
    assert e.language == "it"
    assert e.decision_date == date(2025, 8, 22)
    assert any("!= link date" in n for n in e.notes)


def test_missing_text_section_raises():
    with pytest.raises(ExportParseError):
        parse_export("<html><body><h1>BGer-Urteil 8C_1/2025</h1><h2>Text DE</h2><p>x</p></body></html>", "f.html")


@pytest.mark.skipif(not JUDGEMENTS.exists(), reason="supplied data not present")
def test_all_supplied_exports_parse():
    exs = parse_export_dir(JUDGEMENTS)
    assert len(exs) == 55
    assert len({e.docket for e in exs}) == 55
    assert all(e.text_de and e.text_fr and e.author and e.aza_docid for e in exs)
    assert {e.language for e in exs} == {"de", "fr", "it"}
    by = {e.docket: e for e in exs}
    assert by["8C_458/2025"].decision_date == date(2025, 11, 18)
