from io import BytesIO
import re
from zipfile import ZipFile

import export
from test_flow_review_app import add_draft, approve, case, client  # noqa: F401
from test_pipeline_extensions import DE, FR

RECORD = {"docket": "8C_129/2025", "decision_date": "2025-11-11", "source_url": None}


def test_html_export_follows_suva_export_layout():
    html = export.to_html(export.document(RECORD, "Erster Absatz.\n\nZweiter <Absatz>.", FR)).decode()
    assert "<title>Arrêt du TF 8C_129/2025 du 11 novembre 2025</title>" in html
    assert html.index("<h2>Text DE</h2>") < html.index("<h2>Text FR</h2>") < html.index("<h2>Link zum Urteil</h2>")
    assert "<p>Erster Absatz.</p><p>Zweiter &lt;Absatz&gt;.</p>" in html
    assert "aza://11-11-2025-8C_129-2025" in html


def test_pdf_has_a_valid_cross_reference_table_and_keeps_accents():
    data = export.to_pdf(export.document(RECORD, DE * 40, FR * 40))
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
    assert data[xref:xref + 4] == b"xref"
    offsets = [int(o) for o in re.findall(rb"(\d{10}) 00000 n", data)]
    assert all(re.match(rb"\d+ 0 obj", data[o:o + 12]) for o in offsets)
    assert "bestätigt".encode("cp1252") in data
    assert data.count(b"/Type /Page ") > 1  # long text spans pages


def test_docx_is_a_word_package():
    with ZipFile(BytesIO(export.to_docx(export.document(RECORD, DE, FR)))) as archive:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(archive.namelist())


def test_export_needs_both_approved_languages(client, case):
    assert approve(client, case, add_draft(case, "de"), DE).status_code == 200
    response = client.get("/api/export", query_string={"case": case, "format": "html"})
    assert response.status_code == 409 and "FR" in response.json["message"]
    assert client.get("/api/export", query_string={"case": case, "format": "txt"}).status_code == 400

