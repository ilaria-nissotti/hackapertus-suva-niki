"""Load Suva's supplied files into the KB: example summaries + known-positive label set."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from suva.ingest.exports import parse_export_dir, title_date
from suva.ingest.picks import read_picks
from suva.sources.base import decision_id
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
    if not judgements_dir.is_dir():
        raise ValueError(f"Supplied-data directory does not exist: {judgements_dir}")
    examples = parse_export_dir(judgements_dir)
    xlsx = sorted(judgements_dir.glob("BG_Urteile_*.xlsx"))
    if not examples or not xlsx:
        raise ValueError("Expected both summary exports and a picked-judgements workbook")
    by_docket = defaultdict(set)
    for example in examples:
        by_docket[example.docket].add(example.decision_id)
    picks = [p for f in xlsx for p in read_picks(f, set(by_docket))]
    if not picks:
        raise ValueError("The picked-judgements workbooks contain no labels")

    rep = ImportReport(examples=len(examples), xlsx_rows=len(picks))
    rows = {}
    for example in examples:
        row = asdict(example)
        row.update(
            decision_id=example.decision_id,
            decision_date=example.decision_date.isoformat() if example.decision_date else None,
            bger_url=example.bger_url,
        )
        if example.decision_id in rows:
            raise ValueError(f"Duplicate summary export for {example.decision_id}")
        rows[example.decision_id] = row
        rep.notes.extend(f"{example.source_file}: {note}" for note in example.notes)

    positives = {ref: {
        "ref": ref, "docket": row["docket"], "court": "bger", "in_exports": 1, "in_xlsx": 0,
    } for ref, row in rows.items()}
    for pick in picks:
        ref = pick.ref
        court = pick.court
        notes = list(pick.notes)
        candidates = by_docket.get(pick.docket, set())
        dated_ref = decision_id(pick.docket, title_date(pick.raw)) if pick.docket else None
        if dated_ref in candidates:
            ref = dated_ref
        elif len(candidates) == 1 and (dated_ref == pick.docket or pick.docket in candidates):
            ref = next(iter(candidates))
        elif candidates:
            ref = f"unresolved:{pick.raw}"
            court = "unresolved"
            notes.append("spreadsheet reference does not identify a unique matching decision")
        elif dated_ref:
            ref = dated_ref

        row = positives.setdefault(ref, {
            "ref": ref, "docket": pick.docket, "court": court, "in_exports": 0, "in_xlsx": 0,
        })
        if row["in_xlsx"]:
            rep.notes.append(f"Duplicate spreadsheet reference merged: {ref}")
            row["notes"] = sorted(set(row["notes"] + notes))
            continue
        row.update(
            in_xlsx=1, quarter=pick.quarter, notes=notes,
            summary_created_at=pick.summary_created_at.isoformat() if pick.summary_created_at else None,
        )
        rep.notes.extend(f"xlsx {pick.raw!r}: {note}" for note in notes)

    rep.known_positives = len(positives)
    rep.in_both = sum(row["in_xlsx"] and row["in_exports"] for row in positives.values())
    rep.xlsx_only = sum(row["in_xlsx"] and not row["in_exports"] for row in positives.values())
    rep.exports_only = sum(row["in_exports"] and not row["in_xlsx"] for row in positives.values())
    rep.cantonal = sum(row["court"] == "cantonal" for row in positives.values())
    rep.unresolved = sum(row["court"] == "unresolved" for row in positives.values())
    repo.replace_supplied(list(rows.values()), list(positives.values()))
    return rep
