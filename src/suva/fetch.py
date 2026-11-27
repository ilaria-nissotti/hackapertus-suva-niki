"""Step 3: fill the KB with judgements for a window (+ Suva's picks), from both sources.

1. OpenCaseLaw: full text for every decision in the window (local parquet).
2. Suva's picks: always fetched, whatever their date (they are the labels).
3. entscheidsuche: independent listing of the same window -> coverage ledger.
   A decision only entscheidsuche has is stored without text: that is a gap.
4. Optional: structured HTML from entscheidsuche for the picks (step 4 input).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from suva.http import Blocked, PoliteClient
from suva.sources import entscheidsuche, opencaselaw
from suva.sources.base import SourceRecord, parse_iso_date
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
    new_judgements: int = 0
    html_fetched: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _pick_dockets(repo: Repository) -> set[str]:
    return {r["docket"] for r in repo.list_known_positives(court="bger") if r["docket"]}


def fetch(
    repo: Repository,
    raw_dir: Path,
    window: Window,
    *,
    use_entscheidsuche: bool = True,
    include_picks: bool = True,
    html_for_picks: bool = True,
    refresh_opencaselaw: bool = False,
    client: PoliteClient | None = None,
) -> FetchReport:
    rep = FetchReport(window.name, window.start.isoformat(), window.end.isoformat())
    parquet = opencaselaw.download_bger(raw_dir / "opencaselaw" / "bger.parquet", refresh=refresh_opencaselaw)

    def store(rec) -> None:
        rep.new_judgements += repo.upsert_judgement(rec)

    for rec in opencaselaw.read_bger(parquet, window.start, window.end):
        store(rec)
        rep.opencaselaw += 1

    picks = _pick_dockets(repo) if include_picks else set()
    rep.picks_requested = len(picks)
    if picks:
        for rec in opencaselaw.read_bger(parquet, dockets=picks):
            store(rec)

    client = client or PoliteClient()
    if use_entscheidsuche:
        try:
            for rec in entscheidsuche.list_bger(client, window.start, window.end):
                store(rec)
                rep.entscheidsuche += 1
            # picks outside the window: look them up on their own decision dates
            pick_rows = [r for r in repo.list_judgements(dockets=picks)
                         if not (rep.start <= (r["decision_date"] or "") <= rep.end)]
            for d in sorted({r["decision_date"] for r in pick_rows if r["decision_date"]}):
                day = parse_iso_date(d)
                for rec in entscheidsuche.list_bger(client, day, day):
                    if rec.docket in picks:
                        store(rec)
        except Blocked as exc:
            rep.errors.append(f"entscheidsuche blocked us, stopped: {exc}")

    found = {r["docket"] for r in repo.list_judgements(dockets=picks)}
    rep.picks_found = len(found)
    rep.picks_missing = sorted(picks - found)

    if use_entscheidsuche and html_for_picks:
        cache = raw_dir / "entscheidsuche"
        for row in repo.list_judgements(dockets=picks):
            if row["html_path"]:
                continue
            src = {s["source"]: s for s in repo.judgement_sources(row["key"])}.get("entscheidsuche")
            if not src or not src["html_url"]:
                continue
            rec = SourceRecord(
                source="entscheidsuche", source_id=src["source_id"], docket=row["docket"],
                decision_date=parse_iso_date(row["decision_date"]), html_url=src["html_url"])
            try:
                entscheidsuche.fetch_html(client, rec, cache)
                repo.set_html_path(row["key"], str(cache / f"{rec.source_id}.html"))
                rep.html_fetched += 1
            except Blocked as exc:
                rep.errors.append(f"html blocked, stopped: {exc}")
                break
            except Exception as exc:  # one bad page must not stop the run
                rep.errors.append(f"html {row['docket']}: {type(exc).__name__}: {exc}")

    repo.append_audit(AuditEntry(kind="fetch_run", actor="pipeline", payload=rep.as_dict()))
    return rep
