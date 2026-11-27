from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


DOCKET_RE = re.compile(r"\b\d{1,2}[A-Z]_\d{1,4}/\d{4}\b")

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
