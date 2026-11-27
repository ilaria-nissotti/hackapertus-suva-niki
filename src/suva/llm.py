"""Apertus via any OpenAI-compatible endpoint, with call cache and audit log.

- Endpoint/model come only from LLM_NAME / LLM_BASE_URL / LLM_API_KEY.
- Cache key = sha256(endpoint + model + prompt_version + full request input).
- LLM_CACHE_MODE=readonly serves the frozen snapshot: a cache miss raises CacheMiss
  instead of calling the network, so the demo cannot break on a gateway outage.
- Every call (hit, miss, error) is appended to audit_log with model, host,
  prompt version and input/output hashes — never prompt text or the API key.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from suva.config import Settings
from suva.hashing import sha256
from suva.storage.base import AuditEntry, Repository


class CacheMiss(RuntimeError):
    """Raised in readonly cache mode when a call is not in the snapshot."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    host: str
    prompt_version: str
    input_hash: str
    output_hash: str
    cached: bool
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)


class LLMCache:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache ("
            " key TEXT PRIMARY KEY, model TEXT NOT NULL, prompt_version TEXT NOT NULL,"
            " response TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        self.conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, model: str, prompt_version: str, response: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?, ?)",
                (key, model, prompt_version, json.dumps(response, ensure_ascii=False), time.time()),
            )

    def close(self) -> None:
        self.conn.close()


def _build_openai_client(settings: Settings):
    from openai import OpenAI

    if not settings.llm_base_url:
        raise RuntimeError("LLM_BASE_URL is not set (see .env.example)")
    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "EMPTY",
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,  # SDK retries 429/5xx/timeouts with backoff
    )


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        repo: Repository | None = None,
        cache: LLMCache | None = None,
        client: Any | None = None,
    ):
        self.settings = settings
        self.repo = repo
        self.cache = cache if cache is not None else (
            None if settings.llm_cache_mode == "off" else LLMCache(settings.llm_cache_path)
        )
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _build_openai_client(self.settings)
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        prompt_version: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> LLMResult:
        model = model or self.settings.llm_name
        request: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if response_format is not None:
            request["response_format"] = response_format
        input_hash = sha256({"prompt_version": prompt_version, **request})
        key = sha256({"endpoint": self.settings.llm_base_url, "input_hash": input_hash})
        mode = self.settings.llm_cache_mode

        if self.cache is not None and mode in ("readwrite", "readonly"):
            hit = self.cache.get(key)
            if hit is not None:
                result = LLMResult(
                    text=hit["text"], model=hit["served_model"], host=hit["host"],
                    prompt_version=prompt_version, input_hash=input_hash,
                    output_hash=sha256(hit["text"]), cached=True, latency_ms=0, usage=hit.get("usage", {}),
                )
                self._audit(result)
                return result
            if mode == "readonly":
                self._audit_error(model, prompt_version, input_hash, "cache_miss_readonly")
                raise CacheMiss(f"{prompt_version}: not in LLM cache and LLM_CACHE_MODE=readonly")

        t0 = time.monotonic()
        try:
            resp = self.client.chat.completions.create(**request)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if not resp.choices:
                raise ValueError("Endpoint response has no choices")
            text = resp.choices[0].message.content or ""
            usage = {}
            if getattr(resp, "usage", None) is not None:
                usage = {
                    "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                    "completion_tokens": getattr(resp.usage, "completion_tokens", None),
                }
            served_model = getattr(resp, "model", None) or model
            host = self.settings.llm_host

            if self.cache is not None and mode == "readwrite":
                self.cache.put(key, model, prompt_version, {
                    "text": text, "usage": usage, "served_model": served_model, "host": host,
                })

            result = LLMResult(
                text=text, model=served_model, host=host, prompt_version=prompt_version,
                input_hash=input_hash, output_hash=sha256(text), cached=False,
                latency_ms=latency_ms, usage=usage,
            )
        except Exception as exc:
            self._audit_error(model, prompt_version, input_hash, type(exc).__name__)
            raise
        self._audit(result)
        return result

    def _audit(self, r: LLMResult) -> None:
        if self.repo is None:
            return
        self.repo.append_audit(AuditEntry(
            kind="llm_call", actor=r.model,
            payload={
                "host": r.host, "prompt_version": r.prompt_version,
                "input_hash": r.input_hash, "output_hash": r.output_hash, "cached": r.cached,
                "latency_ms": r.latency_ms, "usage": r.usage,
            },
        ))

    def _audit_error(self, model: str, prompt_version: str, key: str, error: str) -> None:
        if self.repo is None:
            return
        self.repo.append_audit(AuditEntry(
            kind="llm_call", actor=model,
            payload={"host": self.settings.llm_host, "prompt_version": prompt_version,
                     "input_hash": key, "error": error[:500]},
        ))
