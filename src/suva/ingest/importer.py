"""Load Suva's supplied files into the KB: example summaries + known-positive label set."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from suva.ingest.exports import parse_export_dir
from suva.ingest.picks import read_picks
from suva.storage.base import Repository


@dataclass
class ImportReport:
    examples: int = 0
    xlsx_rows: int = 0
    known_positives: int = 0
    in_both: int = 0
    xlsx_only: int = 0
    exports_only: int = 0
    cantonal: int = 0
    unresolved: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def import_supplied(repo: Repository, judgements_dir: Path) -> ImportReport:
    examples = parse_export_dir(judgements_dir)
    xlsx = sorted(judgements_dir.glob("BG_Urteile_*.xlsx"))
    picks = [p for f in xlsx for p in read_picks(f, {e.docket for e in examples})]

    rep = ImportReport(examples=len(examples), xlsx_rows=len(picks))
    for e in examples:
        repo.upsert_example({
            "docket": e.docket,
            "decision_date": e.decision_date.isoformat() if e.decision_date else None,
            "title": e.title, "language": e.language, "text_de": e.text_de, "text_fr": e.text_fr,
            "author": e.author, "source_url": e.source_url, "bger_url": e.bger_url,
            "aza_docid": e.aza_docid, "source_file": e.source_file, "notes": e.notes,
        })
        rep.notes += [f"{e.source_file}: {n}" for n in e.notes]

    by_ref = {p.ref: p for p in picks}
    if len(by_ref) != len(picks):
        rep.notes.append(f"xlsx has {len(picks) - len(by_ref)} duplicate rows (merged)")
    export_dockets = {e.docket for e in examples}

    for ref in sorted(set(by_ref) | export_dockets):
        p = by_ref.get(ref)
        notes = list(p.notes) if p else []
        rep.notes += [f"xlsx {p.raw!r}: {n}" for n in notes] if p else []
        repo.upsert_known_positive({
            "ref": ref,
            "docket": p.docket if p else ref,
            "court": p.court if p else "bger",
            "in_xlsx": int(p is not None),
            "in_exports": int(ref in export_dockets),
            "quarter": p.quarter if p else None,
            "summary_created_at": p.summary_created_at.isoformat() if p and p.summary_created_at else None,
            "notes": notes,
        })

    rep.known_positives = len(set(by_ref) | export_dockets)
    rep.in_both = len(set(by_ref) & export_dockets)
    rep.xlsx_only = len(set(by_ref) - export_dockets)
    rep.exports_only = len(export_dockets - set(by_ref))
    rep.cantonal = sum(p.court == "cantonal" for p in by_ref.values())
    rep.unresolved = sum(p.court == "unresolved" for p in by_ref.values())
    return rep
