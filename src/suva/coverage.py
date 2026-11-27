"""Compare per-run source sightings and usable text across decision weeks."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from suva.sources.base import parse_iso_date
from suva.storage.base import Repository


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _count(week: Counter, sources: set[str], has_text: bool) -> None:
    week["total"] += 1
    week.update(sources)
    if len(sources) == 1:
        week[f"only_{next(iter(sources))}"] += 1
    if not has_text:
        week["no_text"] += 1


def coverage_by_week(repo: Repository, start: str, end: str, run: dict | None = None) -> list[dict]:
    """Use the latest run ledger when available, with a legacy database fallback."""
    if run is None:
        audits = repo.list_audit(kind="fetch_run", limit=1)
        run = audits[0]["payload"] if audits else {}
    weeks: dict[str, Counter] = {}
    if "sightings" not in run:
        for row in repo.list_judgements(start=start, end=end):
            day = parse_iso_date(row["decision_date"])
            if day:
                sources = {src["source"] for src in repo.judgement_sources(row["key"])}
                _count(weeks.setdefault(_monday(day).isoformat(), Counter()), sources, bool(row["text_length"]))
        return [{"week": week, **counts} for week, counts in sorted(weeks.items())]

    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ValueError("Coverage end must be on or after start")
    covered_days = {
        source: {
            date.fromisoformat(a) + timedelta(days=i)
            for a, b in details["ranges"]
            for i in range((date.fromisoformat(b) - date.fromisoformat(a)).days + 1)
        }
        for source, details in run["source_runs"].items()
    }
    day = _monday(first)
    statuses = {}
    while day <= last:
        week = day.isoformat()
        weeks[week] = Counter()
        start_day = max(day, first)
        end_day = min(day + timedelta(days=6), last)
        week_days = {start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)}
        statuses[week] = {
            source: "queried" if week_days <= covered else "partial" if week_days & covered else "unqueried"
            for source, covered in covered_days.items()
        }
        day += timedelta(days=7)

    for row in run["sightings"].values():
        day = parse_iso_date(row["decision_date"])
        if day and first <= day <= last:
            _count(weeks[_monday(day).isoformat()], set(row["sources"]), row["has_text"])
    return [{"week": week, **counts, "queries": statuses[week]} for week, counts in weeks.items()]


def publication_coverage(run: dict) -> dict:
    """Separate dated challenge publications from sightings with unknown dates."""
    published = Counter()
    unknown = Counter()
    for row in run.get("sightings", {}).values():
        target = published if row["publication_date"] else unknown
        _count(target, set(row["sources"]), row["has_text"])
    return {"known_publication_date": dict(published), "unknown_publication_date": dict(unknown)}
