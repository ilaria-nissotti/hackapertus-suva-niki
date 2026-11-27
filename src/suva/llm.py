"""Apertus requests, endpoint-scoped replay, and inference auditing."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from suva.config import Settings
from suva.hashing import sha256
from suva.storage.base import AuditEntry, Repository

log = logging.getLogger(__name__)


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
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
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

        cache = self.cache if mode != "off" else None
        latency_ms = 0
        try:
            response = cache.get(key) if cache else None
            cached = response is not None
            if not cached:
                if mode == "readonly":
                    raise CacheMiss(f"{prompt_version}: not in LLM cache and LLM_CACHE_MODE=readonly")
                started = time.monotonic()
                completion = self.client.chat.completions.create(**request)
                latency_ms = int((time.monotonic() - started) * 1000)
                if not completion.choices:
                    raise ValueError("Endpoint response has no choices")
                usage = getattr(completion, "usage", None)
                response = {
                    "text": completion.choices[0].message.content or "",
                    "served_model": getattr(completion, "model", None) or model,
                    "host": self.settings.llm_host,
                    "usage": {name: getattr(usage, name, None) for name in ("prompt_tokens", "completion_tokens")}
                    if usage is not None else {},
                }
                if cache is not None:
                    cache.put(key, model, prompt_version, response)
            result = LLMResult(
                text=response["text"], model=response["served_model"], host=response["host"],
                prompt_version=prompt_version, input_hash=input_hash, output_hash=sha256(response["text"]),
                cached=cached, latency_ms=latency_ms, usage=response.get("usage", {}),
            )
        except Exception as exc:
            # Never log messages, prompts or provider error text: only what identifies the call.
            log.warning("llm_call_failed prompt_version=%s host=%s model=%s input_hash=%s error=%s",
                        prompt_version, self.settings.llm_host, model, input_hash[:12], type(exc).__name__)
            self._audit(model, host=self.settings.llm_host, prompt_version=prompt_version, input_hash=input_hash,
                        error="cache_miss_readonly" if isinstance(exc, CacheMiss) else type(exc).__name__)
            raise
        self._audit(
            result.model, host=result.host, prompt_version=prompt_version, input_hash=input_hash,
            output_hash=result.output_hash, cached=cached, latency_ms=latency_ms, usage=result.usage,
        )
        return result

    def _audit(self, model: str, **payload: Any) -> None:
        if self.repo is not None:
            self.repo.append_audit(AuditEntry(kind="llm_call", actor=model, payload=payload))
