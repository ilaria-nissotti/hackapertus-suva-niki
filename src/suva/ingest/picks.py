"""Reader for Suva's list of summarised judgements (BG_Urteile_*.xlsx).

Columns: "Jahr / Quartal", "Urteilsnummer / Titel", "Erstellt", "SharePoint-Link".
"Erstellt" is when Suva wrote its summary, NOT the court's publication date.

Normalisation (decided in step 2):
- "BGer-Urteil 458/2025 vom …": chamber prefix missing. It is completed only if
  exactly one known docket (from the exports) has that number/year; otherwise the
  row stays unresolved. The fix is recorded in `notes`.
- Cantonal decisions ("Entscheid des Kantonsgerichts …") are kept but marked
  court="cantonal" with no docket: they are not on bger.ch, so they are excluded
  from bger.ch recall checks rather than counted as misses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from suva.ingest.exports import DOCKET_RE

BARE_NUMBER_RE = re.compile(r"(?<![\w_])(\d{1,4})/(\d{4})\b")
CANTONAL_RE = re.compile(
    r"Kantonsgericht|Obergericht|Verwaltungsgericht|Sozialversicherungsgericht|"
    r"tribunal cantonal|cour cantonale|tribunale cantonale",
    re.IGNORECASE,
)


@dataclass
class Pick:
    raw: str  # cell text exactly as in the xlsx
    docket: str | None  # normalised BGer docket, None for cantonal/unresolved
    court: str  # "bger" | "cantonal" | "unresolved"
    quarter: str | None  # e.g. "2025 Q4"
    summary_created_at: datetime | None  # "Erstellt" (not publication date)
    notes: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        """Stable key: the docket, or the raw title for non-BGer rows."""
        return self.docket or self.raw


def normalise_pick(raw: str, known_dockets: set[str]) -> tuple[str | None, str, list[str]]:
    raw = " ".join(str(raw).split())
    m = DOCKET_RE.search(raw)
    if m:
        return f"{m[1]}_{m[2]}/{m[3]}", "bger", []
    if CANTONAL_RE.search(raw):
        return None, "cantonal", ["cantonal decision: not on bger.ch, excluded from bger recall"]
    b = BARE_NUMBER_RE.search(raw)
    if b:
        candidates = sorted(d for d in known_dockets if d.split("_", 1)[-1] == f"{b[1]}/{b[2]}")
        if len(candidates) == 1:
            return candidates[0], "bger", [f"prefix missing; completed to {candidates[0]} from matching export"]
        return None, "unresolved", [f"prefix missing; {len(candidates)} matching exports, left unresolved"]
    return None, "unresolved", ["no docket found"]


def read_picks(xlsx: Path, known_dockets: set[str] | None = None) -> list[Pick]:
    df = pd.read_excel(xlsx)
    known = known_dockets or set()
    picks = []
    for _, row in df.iterrows():
        raw = " ".join(str(row["Urteilsnummer / Titel"]).split())
        docket, court, notes = normalise_pick(raw, known)
        created = row.get("Erstellt")
        picks.append(Pick(
            raw=raw, docket=docket, court=court,
            quarter=str(row["Jahr / Quartal"]).strip() if pd.notna(row["Jahr / Quartal"]) else None,
            summary_created_at=created.to_pydatetime() if pd.notna(created) else None,
            notes=notes,
        ))
    return picks
