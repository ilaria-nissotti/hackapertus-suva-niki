"""Collect source sightings, challenge cohorts, and usable text for Suva picks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from datetime import timedelta

from suva.http import Blocked, PoliteClient
from suva.sources import entscheidsuche, opencaselaw
from suva.sources.base import SourceRecord, parse_iso_date
from suva.sources.quality import html_to_text, usable_text
from suva.storage.base import AuditEntry, Repository
from suva.windows import Window


@dataclass
class FetchReport:
    window: str
    start: str
    end: str
    opencaselaw: int = 0
    entscheidsuche: int = 0
    picks_requested: int = 0
    picks_found: int = 0
    picks_missing: list[str] = field(default_factory=list)
    picks_listed: int = 0
    picks_ambiguous: list[str] = field(default_factory=list)
    picks_without_text: list[str] = field(default_factory=list)
    published_from: str | None = None
    publication_unknown: int = 0
    source_runs: dict = field(default_factory=dict)
    sightings: dict = field(default_factory=dict)
    new_judgements: int = 0
    html_fetched: int = 0
    unusable_text_cleared: list[str] = field(default_factory=list)
    text_fallback: list[str] = field(default_factory=list)
    still_without_text: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def fetch(
    repo: Repository,
    raw_dir: Path,
    window: Window,
    *,
    use_entscheidsuche: bool = True,
    include_picks: bool = True,
    html_for_picks: bool = True,
    text_fallback: bool = True,
    refresh_opencaselaw: bool = False,
    client: PoliteClient | None = None,
) -> FetchReport:
    rep = FetchReport(window.name, window.start.isoformat(), window.end.isoformat())
    if window.published_from:
        rep.published_from = window.published_from.isoformat()
    rep.source_runs = {
        "opencaselaw": {"status": "pending", "ranges": []},
        "entscheidsuche": {"status": "pending" if use_entscheidsuche else "disabled", "ranges": []},
    }
    records = {}
    publication_dates = {}
    pick_labels = repo.list_known_positives(court="bger") if include_picks else []
    picks = {r["docket"] for r in pick_labels if r["docket"]}
    rep.picks_requested = len(pick_labels)

    def store(rec: SourceRecord, *, cohort: bool = False) -> None:
        rep.new_judgements += repo.upsert_judgement(rec)
        row = records.setdefault(rec.key, {})
        row[rec.source] = rec
        if rec.publication_date:
            publication_dates[rec.key] = rec.publication_date.isoformat()
        if cohort:
            sighting = rep.sightings.setdefault(rec.key, {
                "decision_date": rec.decision_date.isoformat() if rec.decision_date else None,
                "publication_date": None, "sources": [], "has_text": False,
            })
            if rec.source not in sighting["sources"]:
                sighting["sources"].append(rec.source)
            if rec.publication_date:
                sighting["publication_date"] = rec.publication_date.isoformat()
            sighting["has_text"] |= bool(rec.text and rec.text.strip())

    ocl = rep.source_runs["opencaselaw"]
    try:
        parquet = opencaselaw.download_bger(raw_dir / "opencaselaw" / "bger.parquet", refresh=refresh_opencaselaw)
        if window.published_from:
            for rec in opencaselaw.read_bger(parquet, window.start, window.end, with_text=False):
                if rec.publication_date:
                    publication_dates[rec.key] = rec.publication_date.isoformat()
        for rec in opencaselaw.read_bger(parquet, window.start, window.end, published_from=window.published_from):
            store(rec, cohort=True)
            rep.opencaselaw += 1
        ocl["ranges"].append([rep.start, rep.end])
        if picks:
            for rec in opencaselaw.read_bger(parquet, dockets=picks):
                if rec.key not in records:
                    store(rec)
        ocl["status"] = "complete"
    except Exception as exc:
        ocl["status"] = "failed"
        rep.errors.append(f"opencaselaw: {type(exc).__name__}: {exc}")

    owns_client = use_entscheidsuche and client is None
    if owns_client:
        client = PoliteClient()
    try:
        es = rep.source_runs["entscheidsuche"]
        if use_entscheidsuche:
            try:
                cur = window.start
                while cur <= window.end:
                    stop = min(cur + timedelta(days=6), window.end)
                    for rec in entscheidsuche.list_bger(client, cur, stop):
                        published = publication_dates.get(rec.key)
                        in_window = not rep.published_from or not published or rep.published_from <= published <= rep.end
                        if in_window or rec.docket in picks:
                            store(rec, cohort=in_window)
                        if in_window:
                            rep.entscheidsuche += 1
                    es["ranges"].append([cur.isoformat(), stop.isoformat()])
                    cur = stop + timedelta(days=1)
                dates = {
                    parse_iso_date(p["ref"].partition("@")[2]) for p in pick_labels
                }
                dates.update(
                    rec.decision_date for sources in records.values() for rec in sources.values()
                    if rec.docket in picks
                )
                for day in sorted(d for d in dates if d and not window.start <= d <= window.end):
                    for rec in entscheidsuche.list_bger(client, day, day):
                        if rec.docket in picks:
                            store(rec)
                es["status"] = "complete"
            except Exception as exc:
                es["status"] = "blocked" if isinstance(exc, Blocked) else "failed"
                rep.errors.append(f"entscheidsuche: {type(exc).__name__}: {exc}")

        # text fallback: OpenCaseLaw had no usable text (missing or an error page)
        for key, sources in records.items():
            if repo.clear_unusable_text(key):
                rep.unusable_text_cleared.append(key)
            stored = repo.get_judgement(key)
            if stored and usable_text(stored["text"]):
                continue
            html_record = sources.get("entscheidsuche")
            if not (text_fallback and html_record and html_record.html_url and es["status"] == "complete"):
                rep.still_without_text.append(key)
                continue
            try:
                html = entscheidsuche.fetch_html(client, html_record, raw_dir / "entscheidsuche")
            except Blocked as exc:
                es["status"] = "blocked"
                rep.errors.append(f"text fallback blocked, stopped: {exc}")
                rep.still_without_text.append(key)
                continue
            except Exception as exc:
                rep.errors.append(f"text fallback {key}: {type(exc).__name__}: {exc}")
                rep.still_without_text.append(key)
                continue
            text = html_to_text(html)
            store(replace(html_record, text=text), cohort=key in rep.sightings)
            repo.set_html_path(key, str(raw_dir / "entscheidsuche" / f"{html_record.source_id}.html"))
            rep.text_fallback.append(key)

        by_docket = {}
        for key, sources in records.items():
            rec = next(iter(sources.values()))
            by_docket.setdefault(rec.docket, []).append(key)

        for label in pick_labels:
            ref = label["ref"]
            if parse_iso_date(ref.partition("@")[2]):
                matches = [ref] if ref in records else []
            else:
                matches = by_docket.get(label["docket"], [])
            if not matches:
                rep.picks_missing.append(ref)
                continue
            if len(matches) != 1:
                rep.picks_ambiguous.append(ref)
                continue
            key = matches[0]
            rep.picks_listed += 1
            sources = records[key]
            has_text = any(rec.text and rec.text.strip() for rec in sources.values())
            html_record = sources.get("entscheidsuche")
            if html_for_picks and es["status"] == "complete" and html_record and html_record.html_url:
                cache = raw_dir / "entscheidsuche"
                try:
                    entscheidsuche.fetch_html(client, html_record, cache)
                    repo.set_html_path(key, str(cache / f"{html_record.source_id}.html"))
                    rep.html_fetched += 1
                    has_text = True
                except Blocked as exc:
                    es["status"] = "blocked"
                    rep.errors.append(f"html blocked, stopped: {exc}")
                except Exception as exc:
                    rep.errors.append(f"html {ref}: {type(exc).__name__}: {exc}")
            if has_text:
                rep.picks_found += 1
                if key in rep.sightings:
                    rep.sightings[key]["has_text"] = True
            else:
                rep.picks_without_text.append(ref)
    finally:
        if owns_client:
            client.close()

    if window.published_from:
        rep.publication_unknown = sum(not row["publication_date"] for row in rep.sightings.values())
    repo.append_audit(AuditEntry(kind="fetch_run", actor="pipeline", payload=rep.as_dict()))
    return rep
