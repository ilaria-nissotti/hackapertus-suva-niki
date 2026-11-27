"""Build the labelled dataset: one record per judgement in the Excel's period.

Label: relevant if the docket is a row of the Excel (known_positives with in_xlsx=1),
otherwise not relevant. The example summaries (Export_*.html) are NOT labels; they
are style examples for summarising.

Split: decisions on or after `test_from` form the test set (time-based, closest to
real use: rules built on the past, applied to new judgements).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from suva.storage.base import Repository
from suva.windows import DEV_END, DEV_START

TEST_FROM = date(2026, 3, 1)
FACT_KINDS = ("facts",)
REASON_KINDS = ("reasons", "consideration")


@dataclass
class DatasetReport:
    start: str
    end: str
    test_from: str
    records: int = 0
    relevant: int = 0
    by_split: dict = field(default_factory=dict)
    without_text: int = 0
    parse_status: dict = field(default_factory=dict)
    picks_not_in_dataset: list[str] = field(default_factory=list)  # Excel dockets with no judgement in the period

    def as_dict(self) -> dict:
        return asdict(self)


def relevant_dockets(repo: Repository) -> set[str]:
    return {p["docket"] for p in repo.list_known_positives(court="bger") if p["in_xlsx"] and p["docket"]}


def build_record(repo: Repository, row: dict[str, Any], positives: set[str], test_from: date) -> dict[str, Any]:
    j = repo.get_judgement(row["key"]) or {}
    parse = repo.get_parse(row["key"]) or {}
    passages = repo.list_passages(row["key"])
    citations = repo.list_citations(row["key"])

    def joined(kinds: tuple[str, ...]) -> str:
        return "\n\n".join(p["text"] for p in passages if p["kind"] in kinds)

    decision = row["decision_date"]
    return {
        "key": row["key"],
        "docket": row["docket"],
        "decision_date": decision,
        "chamber": row["chamber_prefix"],
        "language": row["language"],
        "relevant": row["docket"] in positives,
        "split": "test" if decision and date.fromisoformat(decision) >= test_from else "train",
        "parse_status": parse.get("status"),
        "parties": parse.get("parties") or "",
        "subject": parse.get("subject") or "",
        "header": joined(("header",)),
        "facts": joined(FACT_KINDS),
        "considerations": joined(REASON_KINDS),
        "dispositiv": joined(("dispositiv",)),
        "text": j.get("text") or "",
        "laws": sorted({c["law"] for c in citations if c["kind"] == "statute" and c["law"]}),
        "articles": sorted({c["canonical"] for c in citations if c["kind"] == "statute"}),
        "bge": sorted({c["canonical"] for c in citations if c["kind"] == "bge"}),
    }


def build_dataset(
    repo: Repository, out_path: Path, start: date = DEV_START, end: date = DEV_END, test_from: date = TEST_FROM,
) -> DatasetReport:
    rep = DatasetReport(start.isoformat(), end.isoformat(), test_from.isoformat())
    positives = relevant_dockets(repo)
    rows = repo.list_judgements(start=start.isoformat(), end=end.isoformat())
    splits, status, found = Counter(), Counter(), set()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            rec = build_record(repo, row, positives, test_from)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rep.records += 1
            rep.relevant += rec["relevant"]
            splits[f"{rec['split']}_{'relevant' if rec['relevant'] else 'not_relevant'}"] += 1
            status[rec["parse_status"] or "not_parsed"] += 1
            rep.without_text += not rec["text"]
            if rec["relevant"]:
                found.add(rec["docket"])
    rep.by_split, rep.parse_status = dict(sorted(splits.items())), dict(status)
    rep.picks_not_in_dataset = sorted(positives - found)
    return rep


def load_dataset(path: Path, split: str | None = None) -> list[dict[str, Any]]:
    return [r for r in iter_dataset(path) if split in (None, "all") or r["split"] == split]


def iter_dataset(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `python -m suva dataset` first")
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)
