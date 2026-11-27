"""Is a source's "full text" really a judgement?

Measured 2026-11-27: 27 OpenCaseLaw rows (12 of them 8C, all Sep-Nov 2026)
hold the bger.ch "Dokument nicht gefunden" page instead of the decision.
Such text must never count as the judgement: it would make relevant decisions
look empty (false negatives). It is treated as "no text", and fetch falls back
to entscheidsuche's HTML.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

ERROR_MARKERS = re.compile(
    r"Dokument nicht gefunden|in elektronischer Form nicht verfügbar|Document (non )?trouvé|"
    r"n'est pas disponible sous forme électronique|non disponibile in forma elettronica|"
    r"Request unsuccessful\. Incapsula",
    re.I,
)
PORTAL_CHROME = re.compile(r"Liste der Neuheiten|Expertensuche für Abonnenten|Eurospider Information Technology", re.I)
JUDGEMENT_MARKERS = re.compile(
    r"Erwägung|Considérant|considérant|Considerando|Diritto:|Demnach erkennt|Par ces motifs|Per questi motivi"
)


def is_error_page(text: str | None) -> bool:
    """True for portal/error pages that do not contain a judgement."""
    if not text:
        return False
    if JUDGEMENT_MARKERS.search(text):
        return False
    return bool(ERROR_MARKERS.search(text) or PORTAL_CHROME.search(text))


def usable_text(text: str | None) -> str | None:
    return None if not text or not text.strip() or is_error_page(text) else text


def html_to_text(html: str) -> str:
    """entscheidsuche/bger HTML -> plain text with one paragraph per div.para.

    Same shape as OpenCaseLaw text (blank line between paragraphs), so the
    step-4 splitter treats both sources identically.
    """
    soup = BeautifulSoup(html, "lxml")
    paras = [p.get_text(" ", strip=True) for p in soup.select("div.para")]
    if not any(paras):
        body = soup.body or soup
        paras = [line.strip() for line in body.get_text("\n").splitlines()]
    return "\n\n".join(p for p in paras if p)
