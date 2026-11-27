from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


DOCKET_RE = re.compile(r"\b\d{1,2}[A-Z]_\d{1,4}/\d{4}\b")
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

def decision_id(docket: str, decision_date: date | str | None) -> str:
    """A docket can have several dated decisions."""
    return f"{docket}@{decision_date}" if decision_date else docket


@dataclass
class SourceRecord:
    """One sighting of one decision in one source."""

    source: str  # "opencaselaw" | "entscheidsuche"
    source_id: str  # id inside that source
    docket: str  # e.g. "8C_129/2025"
    decision_date: date | None
    language: str | None = None
    chamber_raw: str | None = None  # as the source labels it (may be wrong, see §2)
    publication_date: date | None = None  # only when the source provides it
    url: str | None = None  # bger.ch link as given by the source
    text: str | None = None  # plain full text
    html_url: str | None = None  # where structured HTML can be fetched
    seen_at: str | None = None  # source's own scrape timestamp

    @property
    def key(self) -> str:
        return decision_id(self.docket, self.decision_date)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
