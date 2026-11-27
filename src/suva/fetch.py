"""Collect source sightings, challenge cohorts, and usable text for Suva picks."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing, nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path

import httpx
from pyarrow import ArrowException

from suva.http import Blocked, PoliteClient
from suva.sources import entscheidsuche, opencaselaw
from suva.sources.base import SourceRecord, parse_iso_date
from suva.sources.quality import usable_text
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


@dataclass
class _Decision:
    docket: str
    date: date | None
    publication: date | None
    in_window: set[str] = field(default_factory=set)
    attachment: SourceRecord | None = None
    has_text: bool = False


class _FetchRun:
    """Track only metadata for newly sighted decisions; full text lives in the KB."""

    def __init__(self, repo: Repository, window: Window, include_picks: bool):
        self.repo = repo
        self.window = window
        self.labels = repo.list_known_positives(court="bger") if include_picks else []
        self.dockets = {label["docket"] for label in self.labels if label["docket"]}
        self.decisions: dict[str, _Decision] = {}
        self.report = FetchReport(
            window.name, window.start.isoformat(), window.end.isoformat(),
            published_from=window.published_from.isoformat() if window.published_from else None,
            picks_requested=len(self.labels),
        )

    def record(self, rec: SourceRecord, *, in_window: bool) -> None:
        self.report.new_judgements += self.repo.upsert_judgement(rec)
        seen = self.decisions.setdefault(rec.key, _Decision(rec.docket, rec.decision_date, rec.publication_date))
        seen.publication = rec.publication_date or seen.publication
        if in_window:
            seen.in_window.add(rec.source)
        if rec.html_url and rec.source == entscheidsuche.SOURCE:
            seen.attachment = rec
        seen.has_text |= bool(usable_text(rec.text))

    def collect_opencaselaw(self, raw_dir: Path, refresh: bool) -> dict[str, date]:
        window = self.window
        progress = {"status": "complete", "ranges": []}
        self.report.source_runs[opencaselaw.SOURCE] = progress
        publications = {}
        try:
            path = opencaselaw.download_bger(raw_dir / "opencaselaw" / "bger.parquet", refresh=refresh)
            if window.published_from:
                publications = opencaselaw.publication_dates(path, window.start, window.end)
            for rec in opencaselaw.read_bger(path, window.start, window.end, published_from=window.published_from):
                published = rec.publication_date or publications.get(rec.key)
                if window.includes_publication(published):
                    self.record(replace(rec, publication_date=published), in_window=True)
            progress["ranges"].append([self.report.start, self.report.end])
            if self.dockets:
                for rec in opencaselaw.read_bger(path, dockets=self.dockets):
                    if rec.key not in self.decisions:
                        self.record(rec, in_window=False)
        except (OSError, httpx.HTTPError, ArrowException) as exc:
            progress["status"] = "failed"
            self.report.errors.append(f"opencaselaw: {exc}")
        return publications

    def collect_entscheidsuche(self, client: PoliteClient, publications: dict[str, date]) -> None:
        window = self.window
        progress = self.report.source_runs[entscheidsuche.SOURCE]
        progress["status"] = "complete"
        try:
            for start, end in entscheidsuche.week_ranges(window.start, window.end):
                for rec in entscheidsuche.list_bger(client, start, end):
                    published = rec.publication_date or publications.get(rec.key)
                    in_window = window.includes_publication(published)
                    if in_window or rec.docket in self.dockets:
                        self.record(rec, in_window=in_window)
                        if published:
                            self.decisions[rec.key].publication = published
                progress["ranges"].append([start.isoformat(), end.isoformat()])

            dates = {parse_iso_date(label["ref"].partition("@")[2]) for label in self.labels}
            dates.update(seen.date for seen in self.decisions.values() if seen.docket in self.dockets)
            for day in sorted(d for d in dates if d and not window.start <= d <= window.end):
                for rec in entscheidsuche.list_bger(client, day, day):
                    if rec.docket in self.dockets:
                        self.record(rec, in_window=False)
        except (Blocked, httpx.HTTPError, entscheidsuche.SourceResponseError) as exc:
            progress["status"] = "blocked" if isinstance(exc, Blocked) else "failed"
            self.report.errors.append(f"entscheidsuche listing: {exc}")

    def match_picks(self) -> list[tuple[str, str]]:
        by_docket = defaultdict(list)
        for key, seen in self.decisions.items():
            by_docket[seen.docket].append(key)
        matched = []
        for label in self.labels:
            ref = label["ref"]
            if parse_iso_date(ref.partition("@")[2]):
                candidates = [ref] if ref in self.decisions else []
            else:
                candidates = by_docket[label["docket"]]
            if len(candidates) == 1:
                matched.append((ref, candidates[0]))
            elif candidates:
                self.report.picks_ambiguous.append(ref)
            else:
                self.report.picks_missing.append(ref)
        return matched

    def collect_text(
        self, client: PoliteClient | None, cache: Path, *, pick_keys: set[str],
        html_for_picks: bool, text_fallback: bool,
    ) -> None:
        progress = self.report.source_runs[entscheidsuche.SOURCE]
        for key, seen in self.decisions.items():
            if not seen.has_text:
                stored = self.repo.get_judgement(key)
                cached_text = stored["text"] if stored else None
                seen.has_text = bool(usable_text(cached_text))
                if cached_text is not None and not seen.has_text and self.repo.clear_unusable_text(key):
                    self.report.unusable_text_cleared.append(key)

            save_pick_html = html_for_picks and key in pick_keys
            needs_text = text_fallback and not seen.has_text
            attachment = seen.attachment
            if not (client and attachment and progress["status"] != "blocked" and (save_pick_html or needs_text)):
                continue
            try:
                document = entscheidsuche.load_judgement(client, attachment, cache)
            except Blocked as exc:
                progress["status"] = "blocked"
                self.report.errors.append(f"entscheidsuche HTML: {exc}")
                continue
            except (httpx.HTTPError, OSError, ValueError) as exc:
                self.report.errors.append(f"entscheidsuche HTML {key}: {exc}")
                continue
            if needs_text:
                self.record(replace(attachment, text=document.text), in_window=False)
                self.report.text_fallback.append(key)
            self.repo.set_html_path(key, str(document.path))
            if save_pick_html:
                self.report.html_fetched += 1

    def finish(self, picks: list[tuple[str, str]]) -> FetchReport:
        report = self.report
        for key, seen in self.decisions.items():
            if not seen.has_text:
                report.still_without_text.append(key)
            if seen.in_window:
                report.sightings[key] = {
                    "decision_date": seen.date.isoformat() if seen.date else None,
                    "publication_date": seen.publication.isoformat() if seen.publication else None,
                    "sources": sorted(seen.in_window),
                    "has_text": seen.has_text,
                }
        report.opencaselaw = sum(opencaselaw.SOURCE in row["sources"] for row in report.sightings.values())
        report.entscheidsuche = sum(entscheidsuche.SOURCE in row["sources"] for row in report.sightings.values())
        if self.window.published_from:
            report.publication_unknown = sum(not row["publication_date"] for row in report.sightings.values())
        report.picks_listed = len(picks)
        report.picks_without_text = [ref for ref, key in picks if not self.decisions[key].has_text]
        report.picks_found = len(picks) - len(report.picks_without_text)
        self.repo.append_audit(AuditEntry(kind="fetch_run", actor="pipeline", payload=report.as_dict()))
        return report


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
    run = _FetchRun(repo, window, include_picks)
    publications = run.collect_opencaselaw(raw_dir, refresh_opencaselaw)
    run.report.source_runs[entscheidsuche.SOURCE] = {"status": "disabled", "ranges": []}
    with closing(PoliteClient()) if use_entscheidsuche and client is None else nullcontext(client) as http:
        if use_entscheidsuche:
            run.collect_entscheidsuche(http, publications)
        picks = run.match_picks()
        run.collect_text(
            http if use_entscheidsuche else None, raw_dir / "entscheidsuche",
            pick_keys={key for _, key in picks}, html_for_picks=html_for_picks, text_fallback=text_fallback,
        )
    return run.finish(picks)
