"""Read the cached OpenCaseLaw BGer Parquet snapshot."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator

import httpx
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from suva.http import USER_AGENT
from suva.sources.base import SourceRecord, parse_iso_date
from suva.sources.quality import usable_text

SOURCE = "opencaselaw"
BGER_URL = "https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/bger.parquet"
COLUMNS = [
    "decision_id", "docket_number", "decision_date", "publication_date", "language",
    "chamber", "source_url", "full_text", "has_full_text", "scraped_at",
]


def download_bger(dest: Path, refresh: bool = False) -> Path:
    """Stream the parquet to dest (via a .part file so a broken download never looks complete)."""
    if dest.exists() and not refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=dest.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            with httpx.stream("GET", BGER_URL, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(1 << 20):
                    output.write(chunk)
            output.close()
            schema = pq.read_schema(temporary)
            missing = set(COLUMNS) - set(schema.names)
            if missing:
                raise pa.ArrowInvalid(f"OpenCaseLaw snapshot missing columns: {', '.join(sorted(missing))}")
            temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)
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
        text=usable_text(row.get("full_text")) if row.get("has_full_text") else None,
        seen_at=row.get("scraped_at"),
    )


def read_bger(
    path: Path,
    start: date | None = None,
    end: date | None = None,
    dockets: set[str] | None = None,
    with_text: bool = True,
    published_from: date | None = None,
) -> Iterator[SourceRecord]:
    """Select decisions and keep the best available text for each dated docket."""
    filters = []
    if start:
        filters.append(("decision_date", ">=", start.isoformat()))
    if end:
        filters.append(("decision_date", "<=", end.isoformat()))
    if dockets is not None:
        filters.append(("docket_number", "in", sorted(dockets)))
    cols = COLUMNS if with_text else [c for c in COLUMNS if c != "full_text"]
    selection = pq.filters_to_expression(filters) if filters else ds.scalar(True)
    if published_from is not None:
        published = ds.field("publication_date").cast(pa.string())
        in_window = published >= published_from.isoformat()
        if end:
            in_window = in_window & (published < (end + timedelta(days=1)).isoformat())
        selection = in_window | (published.is_null() & selection)
    best: dict[str, SourceRecord] = {}
    dataset = ds.dataset(path, format="parquet")
    # One row group at a time, checking the filter columns first, so full_text is decoded
    # only where something matches (pyarrow cannot prune on docket or the publication OR).
    groups = (g for f in dataset.get_fragments() for g in f.split_by_row_group(selection))
    batches = (b for g in groups if g.count_rows(filter=selection)
               for b in g.to_batches(columns=cols, filter=selection, batch_size=1024))
    for batch in batches:
        for row in batch.to_pylist():
            if not (row.get("docket_number") or "").strip():
                continue  # no docket, no identity: skip instead of crashing the whole fetch
            rec = _to_record(row)
            prev = best.get(rec.key)
            if prev is None:
                best[rec.key] = rec
            elif len(rec.text or "") > len(prev.text or ""):
                rec.publication_date = rec.publication_date or prev.publication_date
                best[rec.key] = rec
            elif not prev.publication_date:
                prev.publication_date = rec.publication_date
    yield from best.values()


def publication_dates(path: Path, start: date, end: date) -> dict[str, date]:
    return {rec.key: rec.publication_date for rec in read_bger(path, start, end, with_text=False)
            if rec.publication_date}
