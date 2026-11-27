"""OpenCaseLaw (huggingface.co/datasets/voilaj/swiss-caselaw), data licence CC0-1.0.

All BGer decisions with full text are in one parquet file (~925 MB, refreshed
nightly). We download it once into data/raw/ and filter locally, so the
pipeline puts no load on any court server.

Measured 2026-11-27: has all 82 Suva-picked BGer dockets; newest decision is
more recent than entscheidsuche's; publication_date is filled for ~37% of rows
(recent scrapes only).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Iterator

import httpx
import pyarrow.parquet as pq

from suva.http import USER_AGENT
from suva.sources.base import SourceRecord, parse_iso_date

SOURCE = "opencaselaw"
BGER_URL = "https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/bger.parquet"
COLUMNS = [
    "decision_id", "docket_number", "decision_date", "publication_date", "language",
    "chamber", "source_url", "full_text", "has_full_text", "scraped_at",
]


def download_bger(dest: Path, refresh: bool = False, url: str = BGER_URL) -> Path:
    """Stream the parquet to dest (via a .part file so a broken download never looks complete)."""
    if dest.exists() and not refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    os.replace(part, dest)
    return dest


def _to_record(row: dict) -> SourceRecord:
    return SourceRecord(
        source=SOURCE,
        source_id=row["decision_id"],
        docket=row["docket_number"].strip(),
        decision_date=parse_iso_date(row["decision_date"]),
        language=row.get("language"),
        chamber_raw=row.get("chamber"),
        publication_date=parse_iso_date(row.get("publication_date")),
        url=row.get("source_url"),
        text=row.get("full_text") if row.get("has_full_text") else None,
        seen_at=row.get("scraped_at"),
    )


def read_bger(
    path: Path,
    start: date | None = None,
    end: date | None = None,
    dockets: set[str] | None = None,
    with_text: bool = True,
) -> Iterator[SourceRecord]:
    """Decisions with start <= decision_date <= end and/or docket in `dockets`.

    Duplicate rows (same docket + decision date) are collapsed, preferring the
    row that has full text, then the longer text.
    """
    filters = []
    if start:
        filters.append(("decision_date", ">=", start.isoformat()))
    if end:
        filters.append(("decision_date", "<=", end.isoformat()))
    if dockets is not None:
        filters.append(("docket_number", "in", sorted(dockets)))
    cols = COLUMNS if with_text else [c for c in COLUMNS if c != "full_text"]
    table = pq.read_table(path, columns=cols, filters=filters or None)

    best: dict[str, SourceRecord] = {}
    for row in table.to_pylist():
        row.setdefault("full_text", None)
        rec = _to_record(row)
        prev = best.get(rec.key)
        if prev is None or len(rec.text or "") > len(prev.text or ""):
            best[rec.key] = rec
    yield from best.values()
