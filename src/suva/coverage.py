"""Coverage ledger report: per week, which source listed which decisions.

Answers "is a dip a source gap or court publication lag?": if both independent
sources dip together, it is the court; if one has decisions the other lacks,
that source has a gap (only_<source> counts).
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from suva.sources.base import parse_iso_date
from suva.storage.base import Repository


def coverage_by_week(repo: Repository, start: str, end: str) -> list[dict]:
    """Per ISO week: decisions per source, and how many only one source has."""
    rows = repo.list_judgements(start=start, end=end)
    weeks: dict[str, Counter] = {}
    for r in rows:
        d = parse_iso_date(r["decision_date"])
        if d is None:
            continue
        wk = (d - timedelta(days=d.weekday())).isoformat()
        srcs = {s["source"] for s in repo.judgement_sources(r["key"])}
        c = weeks.setdefault(wk, Counter())
        c["total"] += 1
        for s in srcs:
            c[s] += 1
        if len(srcs) == 1:
            c[f"only_{next(iter(srcs))}"] += 1
        if r["text_length"] is None:
            c["no_text"] += 1
    return [{"week": wk, **dict(c)} for wk, c in sorted(weeks.items())]
