"""The two time windows (solution_planning.md): never mixed.

- dev: the period of Suva's xlsx (2025 Q4 - 2026 Q1); labelled, used for tuning.
- challenge: decisions published in the last 3 months before the run.

Sources give the decision date; publication date only sometimes. Measured on
OpenCaseLaw 2026-11-27: publication follows decision by median 22 days,
p90 43, p99 75. Use publication date for the confirmed challenge cohort. The widened decision
date range is for discovery and for separately reporting unknown publication dates.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

DEV_START, DEV_END = date(2025, 10, 1), date(2026, 3, 31)
WIDEN_DAYS = 75


@dataclass(frozen=True)
class Window:
    name: str
    start: date  # inclusive, by decision date
    end: date  # inclusive
    published_from: date | None = None  # the real "last 3 months" boundary

    def __str__(self) -> str:
        return f"{self.name}: decisions {self.start} .. {self.end}"


def months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def dev_window() -> Window:
    return Window("dev", DEV_START, DEV_END)


def challenge_window(today: date, months: int = 3, widen_days: int = WIDEN_DAYS) -> Window:
    published_from = months_back(today, months)
    return Window("challenge", published_from - timedelta(days=widen_days), today, published_from)
