"""Approved DE/FR summaries as files for Suva's workflow: HTML in the shape of the supplied
`Export_*.html`, Word (.docx) and PDF. Built with the standard library only, so it works air-gapped.

Only approved text is exported; drafts never are.
"""

from datetime import date
from html import escape
from io import BytesIO
import textwrap
from zipfile import ZIP_DEFLATED, ZipFile

MONTHS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre")
FORMATS = {"html": "text/html",
           "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           "pdf": "application/pdf"}


def title(docket, decision_date):
    """'Arrêt du TF 8C_129/2025 du 11 novembre 2025', as in Suva's exports."""
    if not decision_date:
        return f"Arrêt du TF {docket}"
    d = date.fromisoformat(decision_date)
    return f"Arrêt du TF {docket} du {'1er' if d.day == 1 else d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def bger_url(docket, decision_date):
    """The court's own page for a decision, in the form Suva's exports link to."""
    if not decision_date:
        return None
    d = date.fromisoformat(decision_date)
    docid = f"aza://{d:%d-%m-%Y}-{docket.replace('/', '-')}"
    return ("https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php?highlight_docid="
            f"{docid}&lang=de&zoom=&type=show_document")


def document(record, de, fr):
    return {"title": title(record["docket"], record["decision_date"]), "de": de, "fr": fr,
            "url": record.get("source_url") or bger_url(record["docket"], record["decision_date"])}


def paragraphs(text):
    return [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]


def sections(doc):
    out = [("Text DE", paragraphs(doc["de"])), ("Text FR", paragraphs(doc["fr"]))]
    if doc["url"]:
        out.append(("Link zum Urteil", [doc["url"]]))
    return out


def to_html(doc):
    body = [f"<hr><h1>{escape(doc['title'])}</h1>"]
    for heading, paras in sections(doc):
        body.append(f"<h2>{escape(heading)}</h2>")
        if heading.startswith("Link"):
            url = escape(paras[0])
            body.append(f'<p><a href="{url}">{url}</a></p>')
        else:
            body += [f"<p>{escape(p)}</p>" for p in paras]
    return (f'<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><title>{escape(doc["title"])}</title>'
            f"</head><body>{''.join(body)}<hr></body></html>").encode("utf-8")


def to_docx(doc):
    def para(text, style=None):
        props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f'<w:p>{props}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'

    body = [para(doc["title"], "Heading1")]
    for heading, paras in sections(doc):
        body.append(para(heading, "Heading2"))
        body += [para(p) for p in paras]
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(body)}</w:body></w:document>")
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
                         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/word/document.xml" ContentType="application/'
                         'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels",
                         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                         'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


def to_pdf(doc):
    """A4 pages, Helvetica (WinAnsi covers German and French), text wrapped by character count."""
    lines = [("F2", 14, line) for line in textwrap.wrap(doc["title"], 70)] + [("F1", 10, "")]
    for heading, paras in sections(doc):
        lines.append(("F2", 11, heading))
        for p in paras:
            lines += [("F1", 10, line) for line in textwrap.wrap(p, 95, break_long_words=True)]
            lines.append(("F1", 10, ""))
    per_page = 52
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]

    def pdf_text(value):
        raw = value.encode("cp1252", errors="replace")
        return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for page in pages:
        stream = [b"BT", b"56 786 Td"]
        for font, size, text in page:
            stream.append(b"/%s %d Tf (%s) Tj 0 -14 Td" % (font.encode(), size, pdf_text(text)))
        stream.append(b"ET")
        content = b"\n".join(stream)
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                       b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % len(objects))
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    out.write(b"".join(b"%010d 00000 n \n" % offset for offset in offsets))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


RENDER = {"html": to_html, "docx": to_docx, "pdf": to_pdf}


def filename(doc_docket, fmt):
    return f"Export_{doc_docket.replace('/', '_')}.{fmt}"
