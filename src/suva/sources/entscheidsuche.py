"""entscheidsuche.ch: independent second listing of BGer decisions + HTML on demand.

robots.txt allows everything except /hidden and /archiv. We list via its search
API (metadata only, week by week) and fetch a decision's HTML only when needed,
through the PoliteClient (1 request / 2 s). Measured 2026-11-27: complete for the
dev window and all 82 Suva picks, but it lags OpenCaseLaw in the most recent weeks.
"""

from __future__ import annotations

import re
from tempfile import NamedTemporaryFile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup

from suva.http import PoliteClient
from suva.sources.base import SourceRecord, parse_iso_date

SOURCE = "entscheidsuche"
SEARCH_URL = "https://entscheidsuche.ch/_search.php"
PAGE_SIZE = 1000
SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


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
    for ref in src.get("reference") or []:
        yield SourceRecord(
            source=SOURCE,
            source_id=hit["_id"],
            docket=ref.strip(),
            decision_date=parse_iso_date(src.get("date")),
            language=src.get("language"),
            chamber_raw=title.get("de"),
            html_url=(src.get("attachment") or {}).get("content_url"),
        )


def _list_range(client: PoliteClient, start: date, end: date, size: int) -> Iterator[SourceRecord]:
    data = client.post_json(SEARCH_URL, _query(start, end, size))
    if "error" in data:
        raise RuntimeError(f"entscheidsuche search error: {str(data['error'])[:300]}")
    total = data["hits"]["total"]["value"]
    if total > size:
        if start == end:
            raise RuntimeError(f"{total} decisions on {start}: more than one page ({size})")
        mid = start + (end - start) // 2
        yield from _list_range(client, start, mid, size)
        yield from _list_range(client, mid + timedelta(days=1), end, size)
        return
    for hit in data["hits"]["hits"]:
        yield from _records(hit)


def list_bger(client: PoliteClient, start: date, end: date, size: int = PAGE_SIZE) -> Iterator[SourceRecord]:
    """All BGer decisions with start <= decision date <= end, queried one week at a time."""
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=6), end)
        yield from _list_range(client, cur, stop, size)
        cur = stop + timedelta(days=1)


def fetch_html(client: PoliteClient, rec: SourceRecord, cache_dir: Path) -> str:
    """Decision HTML, cached under cache_dir/<source_id>.html (fetched at most once)."""
    if not rec.html_url:
        raise ValueError(f"{rec.docket}: no HTML url")
    if not SAFE_ID.match(rec.source_id):
        raise ValueError(f"unsafe source id {rec.source_id!r}")
    path = cache_dir / f"{rec.source_id}.html"
    if path.exists():
        try:
            html = path.read_text(encoding="utf-8")
            _validate_html(html, rec)
            return html
        except (UnicodeError, ValueError):
            pass
    html = client.get(rec.html_url).text
    _validate_html(html, rec)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_dir, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(html)
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return html


def _validate_html(html: str, rec: SourceRecord) -> None:
    soup = BeautifulSoup(html, "lxml")
    content = " ".join(p.get_text(" ", strip=True) for p in soup.select("div.para"))
    docket = re.sub(r"[^a-z0-9]", "", rec.docket.lower())
    header = re.sub(r"[^a-z0-9]", "", soup.get_text(" ", strip=True).lower())
    if not content.strip() or not docket or docket not in header or not re.search(
        r"Erwägung|Considérant|Considérants|Diritto|Considerando", content, re.IGNORECASE
    ):
        raise ValueError(f"{rec.docket}: response lacks expected judgement content")
