"""Polite HTTP client used by every source adapter.

Red lines from solution_planning.md §10, enforced here:
- an honest User-Agent naming this project (never a faked browser);
- at least MIN_INTERVAL seconds between requests to the same host
  (search.bger.ch / entscheidsuche ask for Crawl-delay: 2);
- one request at a time; retry with backoff on 429/5xx/timeouts only;
- a block (403) is reported, never worked around.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

USER_AGENT = "suva-bger-hackathon/0.1 (HackApertus research prototype; polite, 1 req/2s per host)"
MIN_INTERVAL = 2.0
RETRY_STATUS = {429, 500, 502, 503, 504}


class Blocked(RuntimeError):
    """The server refused us (403). We stop instead of trying to get around it."""


@dataclass
class PoliteClient:
    min_interval: float = MIN_INTERVAL
    retries: int = 3
    timeout: float = 60.0
    transport: httpx.BaseTransport | None = None  # tests inject a MockTransport
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._last: dict[str, float] = {}
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=self.timeout,
            transport=self.transport, follow_redirects=True,
        )

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        host = urlparse(url).netloc
        for attempt in range(self.retries + 1):
            self._wait_turn(host)
            try:
                resp = self._client.request(method, url, **kw)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.retries:
                    raise
                self.sleep(self.min_interval * 2 ** attempt)
                continue
            if resp.status_code == 403:
                raise Blocked(f"403 from {host} for {url}")
            if resp.status_code in RETRY_STATUS and attempt < self.retries:
                retry_after = resp.headers.get("Retry-After", "")
                delay = self.min_interval * 2 ** attempt
                if retry_after.isdigit():
                    delay = max(delay, float(retry_after))
                elif retry_after:
                    try:
                        retry_at = parsedate_to_datetime(retry_after)
                        if retry_at.tzinfo is not None:
                            delay = max(delay, (retry_at - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, OverflowError):
                        pass
                self.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        raise AssertionError("unreachable")

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post_json(self, url: str, body: Any) -> Any:
        return self.request("POST", url, json=body).json()

    def close(self) -> None:
        self._client.close()
