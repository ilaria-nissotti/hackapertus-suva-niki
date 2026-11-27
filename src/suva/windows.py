"""Decision-date discovery ranges and the challenge publication window."""

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

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("Window start must be on or before its end")

    def includes_publication(self, published: date | None) -> bool:
        return self.published_from is None or published is None or self.published_from <= published <= self.end

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
