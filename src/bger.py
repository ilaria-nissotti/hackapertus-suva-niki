"""Shared helpers for the review app: config and Apertus chat.

Judgements come from the suva knowledge base (`python -m suva fetch`); `python -m suva
llm-ping` checks the Apertus endpoint.
"""

from dataclasses import replace
import os
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from openai import APIError

from suva.config import load_settings
from suva.llm import LLMClient

ROOT = Path(__file__).resolve().parents[1]


def local_config(*names):
    config = {}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in names:
                config[name.strip()] = value.strip().strip("'\"")
    return {name: os.environ.get(name, config.get(name)) for name in names}


def model_config():
    settings = load_settings()
    base_url = settings.llm_base_url.rstrip("/")
    if not base_url or not settings.llm_name:
        raise ValueError("Configure LLM_BASE_URL and LLM_NAME for Apertus")
    try:
        parsed = urlsplit(base_url)
        parsed.port  # raises ValueError for a port such as "bogus"
        httpx.URL(base_url)  # the client library's own parser, so a URL accepted here cannot fail later
    except (ValueError, httpx.InvalidURL):
        raise ValueError("LLM_BASE_URL is not a valid URL") from None
    if not parsed.hostname or (parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )):
        raise ValueError("Use HTTPS for a remote LLM_BASE_URL")
    return {"LLM_NAME": settings.llm_name, "LLM_BASE_URL": base_url,
            "settings": replace(settings, llm_base_url=base_url, llm_cache_mode="off")}


def chat(config, messages, max_tokens):
    # Review conversations can contain private questions. Keep them out of the replay cache.
    llm = LLMClient(config["settings"])
    try:
        with llm.client:
            return llm.chat(messages, prompt_version="review-json", max_tokens=max_tokens).text
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        error = OSError(f"Apertus HTTP {status}" if status else "Apertus request failed")
        if status:
            error.code = status  # app.process shows "Provider HTTP <code>"
        raise error from None
