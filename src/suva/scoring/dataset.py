"""Build the labelled dataset: one record per judgement in the Excel's period.

Label: relevant if the decision is a row of the Excel (known_positives with in_xlsx=1),
otherwise not relevant. Dated refs (docket@YYYY-MM-DD) match that decision exactly; a
docket-only ref matches only when the docket has a single decision in the period, and is
reported as ambiguous otherwise (same rule as fetch and parse). The example summaries (Export_*.html) are NOT labels; they
are style examples for summarising.

Split: dockets whose first decision in the period is on or after `test_from` form the
test set (time-based, closest to real use: rules built on the past, applied to new
judgements). All decisions of one docket stay in the same split.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from suva.hashing import sha256
from suva.scoring.derive import with_derived_fields
from suva.parse.segment import PARSER_VERSION
from suva.sources.base import parse_iso_date
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
    stale_parses: int = 0  # parsed with an older parser or an older text: run `parse`
    parse_status: dict = field(default_factory=dict)
    picks_not_in_dataset: list[str] = field(default_factory=list)  # Excel refs with no judgement in the period
    picks_ambiguous: list[str] = field(default_factory=list)  # docket-only refs with several decisions

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LabelMatch:
    keys: set[str] = field(default_factory=set)  # judgement keys labelled relevant
    missing: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)


def match_labels(repo: Repository, rows: list[dict[str, Any]]) -> LabelMatch:
    """Match the Excel picks to the judgements in `rows` (dated refs exactly, docket-only if unique)."""
    keys = {row["key"] for row in rows}
    by_docket = defaultdict(list)
    for row in rows:
        by_docket[row["docket"]].append(row["key"])
    result = LabelMatch()
    for label in repo.list_known_positives(court="bger"):
        if not (label["in_xlsx"] and label["docket"]):
            continue
        ref = label["ref"]
        if parse_iso_date(ref.partition("@")[2]):
            candidates = [ref] if ref in keys else []
        else:
            candidates = by_docket[label["docket"]]
        if len(candidates) == 1:
            result.keys.add(candidates[0])
        elif candidates:
            result.ambiguous.append(ref)
        else:
            result.missing.append(ref)
    result.missing.sort()
    result.ambiguous.sort()
    return result


def docket_splits(rows: list[dict[str, Any]], test_from: date) -> dict[str, str]:
    """train/test per docket, from the docket's earliest decision date (undated decisions: train)."""
    first: dict[str, str] = {}
    for row in rows:
        d = row["decision_date"]
        if d and (row["docket"] not in first or d < first[row["docket"]]):
            first[row["docket"]] = d
    return {row["docket"]: "test" if row["docket"] in first and date.fromisoformat(first[row["docket"]]) >= test_from
            else "train" for row in rows}


def build_record(repo: Repository, row: dict[str, Any], positives: set[str], split: str) -> dict[str, Any]:
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
        "relevant": row["key"] in positives,
        "split": split,
        "parse_status": parse.get("status"),
        "parse_stale": bool(parse) and (parse.get("parser_version") != PARSER_VERSION
                                        or parse.get("text_hash") != (sha256(j["text"]) if j.get("text") else None)),
        "parties": parse.get("parties") or "",
        "subject": parse.get("subject") or "",
        "header": joined(("header",)),
        "regeste": joined(("regeste",)),
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
    rows = repo.list_judgements(start=start.isoformat(), end=end.isoformat())
    labels = match_labels(repo, rows)
    positives = labels.keys
    splits_by_docket = docket_splits(rows, test_from)
    splits, status = Counter(), Counter()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            rec = build_record(repo, row, positives, splits_by_docket[row["docket"]])
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rep.records += 1
            rep.relevant += rec["relevant"]
            splits[f"{rec['split']}_{'relevant' if rec['relevant'] else 'not_relevant'}"] += 1
            status[rec["parse_status"] or "not_parsed"] += 1
            rep.without_text += not rec["text"]
            rep.stale_parses += rec["parse_stale"]
    rep.by_split, rep.parse_status = dict(sorted(splits.items())), dict(status)
    rep.picks_not_in_dataset, rep.picks_ambiguous = labels.missing, labels.ambiguous
    return rep


def load_dataset(path: Path, split: str | None = None) -> list[dict[str, Any]]:
    return [r for r in iter_dataset(path) if split in (None, "all") or r["split"] == split]


def iter_dataset(path: Path) -> Iterator[dict[str, Any]]:
    """Records of the dataset file, with the derived fields of derive.py added."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `python -m suva dataset` first")
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield with_derived_fields(json.loads(line))
