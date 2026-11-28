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
    published_from: date | None = None
    include_unknown_publication: bool = True

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("Window start must be on or before its end")

    def includes_publication(self, published: date | None) -> bool:
        if self.published_from is None:
            return True
        if published is None:
            return self.include_unknown_publication
        return self.published_from <= published <= self.end

    def __str__(self) -> str:
        return f"{self.name}: decisions {self.start} .. {self.end}"


def months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def dev_window() -> Window:
    return Window("dev", DEV_START, DEV_END)


def publication_window(first: date, last: date) -> Window:
    """An inclusive publication range, with a wider discovery range for source queries."""
    if first > last:
        raise ValueError("The first publication date must be on or before the last.")
    start = date.fromordinal(max(1, first.toordinal() - WIDEN_DAYS))
    return Window("publication", start, last, first, include_unknown_publication=False)


def challenge_window(today: date, months: int = 3, widen_days: int = WIDEN_DAYS) -> Window:
    published_from = months_back(today, months)
    return Window("challenge", published_from - timedelta(days=widen_days), today, published_from)
