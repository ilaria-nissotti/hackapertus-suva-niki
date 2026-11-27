"""Recognize source error pages and extract paragraph text from judgement HTML."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

ERROR_MARKERS = re.compile(
    r"Dokument nicht gefunden|in elektronischer Form nicht verfügbar|Document (?:non trouvé|introuvable)|"
    r"n'est pas disponible sous forme électronique|non disponibile in forma elettronica|"
    r"Request unsuccessful\. Incapsula",
    re.I,
)
PORTAL_CHROME = re.compile(r"Liste der Neuheiten|Expertensuche für Abonnenten|Eurospider Information Technology", re.I)
JUDGEMENT_MARKERS = re.compile(
    r"^\s*(?:Erwägungen?\s*:|in Erwägung\s*[:,]|Considérant|Considerando|Diritto\s*:|"
    r"ritiene\s*:|Demnach (?:erkennt|verfügt)|Par ces motifs|Per questi motivi)", re.I | re.M
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


def html_to_text(html: str | BeautifulSoup) -> str:
    """Keep one paragraph per div.para so the parser can find section boundaries."""
    soup = BeautifulSoup(html, "lxml") if isinstance(html, str) else html
    paras = [p.get_text(" ", strip=True) for p in soup.select("div.para")]
    if not any(paras):
        body = soup.body or soup
        paras = [line.strip() for line in body.get_text("\n").splitlines()]
    return "\n\n".join(p for p in paras if p)
