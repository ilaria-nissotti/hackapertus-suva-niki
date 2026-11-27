"""HTTP pacing and retries for the listing and attachment API."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from itertools import count
from typing import Any, Callable

import httpx

USER_AGENT = "suva-bger-hackathon/0.1 (HackApertus research prototype; polite, 1 req/2s per host)"
MIN_INTERVAL = 2.0
RETRY_STATUS = {429, 500, 502, 503, 504}
log = logging.getLogger(__name__)


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
            transport=self.transport, follow_redirects=True, event_hooks={"request": [self._pace_request]},
        )

    def _pace_request(self, request: httpx.Request) -> None:
        host = request.url.host
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        for attempt in count():
            try:
                resp = self._client.request(method, url, **kw)
            except httpx.TransportError as exc:
                host = httpx.URL(url).host
                if attempt >= self.retries:
                    log.error("http_gave_up method=%s host=%s attempts=%d error=%s", method, host, attempt + 1,
                              type(exc).__name__)
                    raise
                log.warning("http_retry method=%s host=%s attempt=%d error=%s", method, host, attempt + 1,
                            type(exc).__name__)
                self.sleep(self.min_interval * 2 ** attempt)
                continue
            if resp.status_code == 403:
                log.error("http_blocked method=%s host=%s status=403", method, resp.request.url.host)
                raise Blocked(f"403 from {resp.request.url.host}")
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
                        log.debug("http_retry_after_unparsed host=%s", resp.request.url.host)
                log.warning("http_retry method=%s host=%s attempt=%d status=%d delay_s=%.1f", method,
                            resp.request.url.host, attempt + 1, resp.status_code, delay)
                self.sleep(delay)
                continue
            if resp.is_error:
                log.error("http_failed method=%s host=%s status=%d attempts=%d", method, resp.request.url.host,
                          resp.status_code, attempt + 1)
            resp.raise_for_status()
            return resp

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post_json(self, url: str, body: Any) -> Any:
        return self.request("POST", url, json=body).json()

    def close(self) -> None:
        self._client.close()
