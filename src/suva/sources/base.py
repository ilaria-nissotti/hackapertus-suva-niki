from __future__ import annotations

from dataclasses import dataclass
from datetime import date


def judgement_key(docket: str, decision_date: date | None) -> str:
    """One docket can carry several decisions, so the key is docket + decision date.

    Same format as suva.ingest.exports.decision_id ('8C_129/2025@2025-11-11'), so
    example summaries and judgements join directly. '#' stays reserved for
    passage ids ('8C_129/2025@2025-11-11#E.3.2').
    """
    from suva.ingest.exports import decision_id

    return decision_id(docket, decision_date.isoformat() if decision_date else None)


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
        return judgement_key(self.docket, self.decision_date)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
