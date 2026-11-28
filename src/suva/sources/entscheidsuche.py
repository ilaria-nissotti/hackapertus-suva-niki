"""List BGer decisions and cache validated HTML from entscheidsuche."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from suva.http import PoliteClient
from suva.sources.base import SourceRecord, parse_iso_date
from suva.sources.quality import JUDGEMENT_MARKERS, html_to_text, usable_text

SOURCE = "entscheidsuche"
SEARCH_URL = "https://entscheidsuche.ch/_search.php"
PAGE_SIZE = 1000
SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
log = logging.getLogger(__name__)


class SourceResponseError(RuntimeError):
    """The listing did not contain the requested result set."""


@dataclass(frozen=True)
class JudgementDocument:
    path: Path
    text: str


def _query(start: date, end: date, size: int) -> dict:
    return {
        "size": size,
        "track_total_hits": True,
        "_source": ["date", "reference", "language", "title", "attachment.content_url"],
        "query": {"bool": {"filter": [
            {"term": {"hierarchy": "CH_BGer"}},
            {"range": {"date": {"gte": start.isoformat(), "lte": end.isoformat()}}},
        ]}},
        "sort": [{"date": {"order": "asc", "unmapped_type": "date"}}],
    }


def _records(hit: dict) -> Iterator[SourceRecord]:
    src = hit.get("_source", {})
    title = src.get("title") or {}
    references = src.get("reference")
    decision_date = parse_iso_date(src.get("date"))
    if not isinstance(references, list) or not references or not decision_date:
        raise ValueError("Missing docket references or decision date")
    for ref in references:
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("Invalid docket reference")
        yield SourceRecord(
            source=SOURCE,
            source_id=hit["_id"],
            docket=ref.strip(),
            decision_date=decision_date,
            language=src.get("language"),
            chamber_raw=title.get("de"),
            html_url=(src.get("attachment") or {}).get("content_url"),
        )


def _list_range(client: PoliteClient, start: date, end: date, size: int) -> Iterator[SourceRecord]:
    try:
        data = client.post_json(SEARCH_URL, _query(start, end, size))
        if data.get("error"):
            raise SourceResponseError(f"entscheidsuche search error: {str(data['error'])[:300]}")
        results = data["hits"]
        total = results["total"]["value"]
        hits = results["hits"]
        if not isinstance(total, int) or total < 0 or not isinstance(hits, list):
            raise ValueError("Unexpected search result fields")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SourceResponseError(f"Invalid listing for {start} through {end}") from exc
    if total > size or results["total"].get("relation") == "gte":
        if start == end:
            raise SourceResponseError(f"Listing for {start} exceeds one page ({size})")
        mid = start + (end - start) // 2
        yield from _list_range(client, start, mid, size)
        yield from _list_range(client, mid + timedelta(days=1), end, size)
        return
    if len(hits) != total:
        raise SourceResponseError(f"Incomplete listing for {start} through {end}")
    try:
        for hit in hits:
            yield from _records(hit)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SourceResponseError(f"Invalid decision in listing for {start} through {end}") from exc


def week_ranges(start: date, end: date) -> Iterator[tuple[date, date]]:
    while start <= end:
        stop = min(start + timedelta(days=6), end)
        yield start, stop
        start = stop + timedelta(days=1)


def list_bger(client: PoliteClient, start: date, end: date, size: int = PAGE_SIZE) -> Iterator[SourceRecord]:
    for first, last in week_ranges(start, end):
        yield from _list_range(client, first, last, size)


def load_judgement(client: PoliteClient, rec: SourceRecord, cache_dir: Path) -> JudgementDocument:
    """Load the text and local HTML path, refreshing a missing or invalid cache entry."""
    if not isinstance(rec.source_id, str) or not SAFE_ID.fullmatch(rec.source_id):
        raise ValueError(f"Unsafe source ID: {rec.source_id!r}")
    if not isinstance(rec.html_url, str):
        raise ValueError(f"{rec.docket}: expected an HTTPS entscheidsuche attachment URL")
    url = urlsplit(rec.html_url)
    if (url.scheme, url.netloc) != ("https", "entscheidsuche.ch"):
        raise ValueError(f"{rec.docket}: expected an HTTPS entscheidsuche attachment URL")
    path = cache_dir / f"{rec.source_id}.html"
    try:
        return JudgementDocument(path, _judgement_text(path.read_text(encoding="utf-8"), rec))
    except FileNotFoundError:
        log.debug("html_cache_miss source_id=%s", rec.source_id)
    except ValueError as exc:
        log.info("html_cache_invalid source_id=%s reason=%s", rec.source_id, exc)

    html = client.get(rec.html_url, follow_redirects=False).text
    text = _judgement_text(html, rec)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_dir, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(html)
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return JudgementDocument(path, text)


def _judgement_text(html: str, rec: SourceRecord) -> str:
    soup = BeautifulSoup(html, "lxml")
    text = usable_text(html_to_text(soup))
    docket = re.escape(rec.docket).replace("_", r"[_\s]+")
    header = " ".join(islice(soup.stripped_strings, 30))
    if (not text or not JUDGEMENT_MARKERS.search(text)
            or not re.search(rf"(?<!\w){docket}(?!\w)", header, re.I)):
        raise ValueError(f"{rec.docket}: response lacks expected judgement content")
    return text
