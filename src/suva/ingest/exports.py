"""Parser for Suva's summary exports (judgements/Export_*.html).

These files are Suva's human-written summaries, NOT judgement texts. Layout:
  <h1>title in the decision's language</h1>
  <h2>Text DE</h2><p>…</p>…  <h2>Text FR</h2><p>…</p>…
  <h2>Link zum Urteil</h2><p>bger.ch URL (as <a href> or plain text)</p>
  <h2>Autor</h2><p>name</p>

Docket and decision date are taken from the bger.ch link's aza docid
(aza://DD-MM-YYYY-8C_458-2025), which is the most reliable source; the title
is the fallback. Disagreements are recorded in `notes`, never silently fixed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup, Tag

DOCKET_RE = re.compile(r"\b(\d{1,2}[A-Z])_(\d{1,4})/(\d{4})\b")
AZA_RE = re.compile(r"aza://(\d{2})-(\d{2})-(\d{4})-(\d{1,2}[A-Z])_(\d{1,4})-(\d{4})")
URL_RE = re.compile(r"https?://\S+")

MONTHS = {
    # de
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
    "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
    # fr
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7,
    "août": 8, "septembre": 9, "octobre": 10, "décembre": 12,
    # it
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "dicembre": 12,
}
TITLE_DATE_RE = re.compile(r"(\d{1,2})(?:\.|er)?\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})")
TITLE_LANG = {"BGer-Urteil": "de", "Arrêt": "fr", "Sentenza": "it"}

SHOW_DOCUMENT_URL = (
    "https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php"
    "?highlight_docid=aza://{aza}&lang=de&zoom=&type=show_document"
)


@dataclass
class ExampleSummary:
    docket: str
    decision_date: date | None
    title: str
    language: str | None  # language of the decision (from the title wording): de | fr | it
    text_de: str
    text_fr: str
    author: str | None
    source_url: str | None  # link exactly as in the export
    aza_docid: str | None  # e.g. 18-11-2025-8C_458-2025
    source_file: str
    notes: list[str] = field(default_factory=list)

    @property
    def bger_url(self) -> str | None:
        """Canonical bger.ch show_document URL (some exports link to a search hit instead)."""
        return SHOW_DOCUMENT_URL.format(aza=self.aza_docid) if self.aza_docid else self.source_url


class ExportParseError(ValueError):
    pass


def _sections(soup: BeautifulSoup) -> dict[str, list[Tag]]:
    """Map each <h2> heading to the elements that follow it, up to the next <h2>."""
    out: dict[str, list[Tag]] = {}
    for h2 in soup.find_all("h2"):
        items = []
        for sib in h2.find_next_siblings():
            if sib.name == "h2":
                break
            if sib.name != "hr":
                items.append(sib)
        out[h2.get_text(strip=True)] = items
    return out


def _text(items: list[Tag]) -> str:
    paras = [p.get_text(" ", strip=True) for p in items]
    return "\n\n".join(p for p in paras if p)


def _link(items: list[Tag]) -> str | None:
    for el in items:
        a = el.find("a", href=True)
        if a:
            return a["href"].strip()
        m = URL_RE.search(el.get_text(" ", strip=True))
        if m:
            return m.group(0)
    return None


def _title_date(title: str) -> date | None:
    m = TITLE_DATE_RE.search(title)
    if not m:
        return None
    month = MONTHS.get(m.group(2).lower())
    return date(int(m.group(3)), month, int(m.group(1))) if month else None


def parse_export(html: str, source_file: str) -> ExampleSummary:
    soup = BeautifulSoup(html, "lxml")
    if soup.h1 is None:
        raise ExportParseError(f"{source_file}: no <h1> title")
    title = soup.h1.get_text(" ", strip=True)
    sec = _sections(soup)
    missing = [k for k in ("Text DE", "Text FR") if k not in sec]
    if missing:
        raise ExportParseError(f"{source_file}: missing sections {missing}")

    notes: list[str] = []
    url = _link(sec.get("Link zum Urteil", []))
    aza = AZA_RE.search(url or "")

    title_docket = DOCKET_RE.search(title)
    title_docket = f"{title_docket[1]}_{title_docket[2]}/{title_docket[3]}" if title_docket else None
    t_date = _title_date(title)

    if aza:
        docket = f"{aza[4]}_{aza[5]}/{aza[6]}"
        decision_date = date(int(aza[3]), int(aza[2]), int(aza[1]))
        aza_docid = aza.group(0).removeprefix("aza://")
        if title_docket is None:
            notes.append(f"title has no full docket; taken from link: {docket}")
        elif title_docket != docket:
            notes.append(f"title docket {title_docket} != link docket {docket}; using link")
        if t_date and t_date != decision_date:
            notes.append(f"title date {t_date} != link date {decision_date}; using link")
    elif title_docket:
        docket, decision_date, aza_docid = title_docket, t_date, None
        notes.append("no aza docid in link; docket/date from title")
    else:
        raise ExportParseError(f"{source_file}: no docket in link or title")

    first_word = title.split()[0] if title else ""
    language = TITLE_LANG.get(first_word)
    if language is None:
        notes.append(f"unknown title wording {first_word!r}; language unknown")

    author = _text(sec.get("Autor", [])) or None
    text_de, text_fr = _text(sec["Text DE"]), _text(sec["Text FR"])
    for lang, txt in (("DE", text_de), ("FR", text_fr)):
        if not txt:
            notes.append(f"Text {lang} is empty")

    return ExampleSummary(
        docket=docket, decision_date=decision_date, title=title, language=language,
        text_de=text_de, text_fr=text_fr, author=author, source_url=url,
        aza_docid=aza_docid, source_file=source_file, notes=notes,
    )


def parse_export_dir(directory: Path) -> list[ExampleSummary]:
    return [
        parse_export(p.read_text(encoding="utf-8"), p.name)
        for p in sorted(directory.glob("Export_*.html"))
    ]
