"""Settings from environment variables only (gateway → local vLLM → Azure by env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

CACHE_MODES = ("readwrite", "readonly", "off")


@dataclass(frozen=True)
class Settings:
    llm_name: str
    llm_base_url: str
    llm_api_key: str
    data_dir: Path
    database_url: str
    llm_cache_mode: str
    llm_timeout: float
    llm_max_retries: int

    @property
    def llm_host(self) -> str:
        """Host part of the endpoint, recorded per call (never the key)."""
        return urlparse(self.llm_base_url).hostname or "unset"

    @property
    def sqlite_path(self) -> Path:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError(f"Only sqlite:/// DATABASE_URL is supported so far, got {self.database_url!r}")
        p = Path(self.database_url.removeprefix("sqlite:///"))
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def llm_cache_path(self) -> Path:
        return self.data_dir / "llm_cache.sqlite"


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def load_settings(env_file: Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    """Read settings. `environ` overrides os.environ (used by tests)."""
    if environ is None:
        load_dotenv(env_file or REPO_ROOT / ".env", override=False)
        environ = dict(os.environ)

    mode = environ.get("LLM_CACHE_MODE", "readwrite").strip().lower()
    if mode not in CACHE_MODES:
        raise ValueError(f"LLM_CACHE_MODE must be one of {CACHE_MODES}, got {mode!r}")

    return Settings(
        llm_name=environ.get("LLM_NAME", "swiss-ai/Apertus-v1.5-70B").strip(),
        llm_base_url=environ.get("LLM_BASE_URL", "").strip(),
        llm_api_key=environ.get("LLM_API_KEY", "").strip(),
        data_dir=_resolve(environ.get("DATA_DIR", "data")),
        database_url=environ.get("DATABASE_URL", "sqlite:///data/kb.sqlite").strip(),
        llm_cache_mode=mode,
        llm_timeout=float(environ.get("LLM_TIMEOUT", "120")),
        llm_max_retries=int(environ.get("LLM_MAX_RETRIES", "5")),
    )
