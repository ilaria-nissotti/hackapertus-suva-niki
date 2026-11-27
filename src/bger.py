"""Shared helpers for the review app: case IDs, config and Apertus chat.

Judgements come from the suva knowledge base (`python -m suva fetch`); `python -m suva
llm-ping` checks the Apertus endpoint.
"""

import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = re.compile(r"\b\d+[A-Z]+_\d+/\d{4}\b")


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
    names = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_NAME")
    config = local_config(*names)
    missing = [name for name in names if not config[name]]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    base_url = config["LLM_BASE_URL"].rstrip("/")
    if urlsplit(base_url).scheme != "https" or not urlsplit(base_url).hostname:
        raise ValueError("LLM_BASE_URL must be an HTTPS API base URL")
    return config


def chat(config, messages, max_tokens):
    base_url = config["LLM_BASE_URL"].rstrip("/")
    payload = dict(model=config["LLM_NAME"], messages=messages, max_tokens=max_tokens,
                   temperature=0)
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {config['LLM_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=90) as response:
        result = json.load(response)
    if not result.get("choices"):
        raise ValueError("Apertus returned no choices")
    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("Apertus response exceeded the output budget")
    return choice["message"]["content"]
