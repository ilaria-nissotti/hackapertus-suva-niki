"""Command line entry point: `python -m suva <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from suva.config import load_settings
from suva.storage import open_repository


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    repo = open_repository(settings, migrate=False)
    applied = repo.migrate()
    print(f"KB: {settings.sqlite_path}")
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    repo.close()
    return 0


def cmd_llm_ping(args: argparse.Namespace) -> int:
    """Check the gateway: one tiny uncached call, print model, latency, tokens."""
    from suva.llm import LLMClient

    settings = load_settings()
    repo = open_repository(settings)
    llm = LLMClient(settings, repo=repo, cache=None)  # bypass cache: we want a live round-trip
    print(f"endpoint host: {settings.llm_host}  model: {settings.llm_name}")
    try:
        r = llm.chat(
            [{"role": "user", "content": args.prompt}],
            prompt_version="ping/v1", temperature=0.0, max_tokens=args.max_tokens,
        )
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        repo.close()
    print(json.dumps({"served_model": r.model, "latency_ms": r.latency_ms, "usage": r.usage}, indent=2))
    print("reply:", r.text.strip()[:500])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="suva")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create/upgrade the knowledge base").set_defaults(func=cmd_init_db)

    p = sub.add_parser("llm-ping", help="one live call to check the Apertus endpoint")
    p.add_argument("--prompt", default="Antworte mit genau einem Wort: Bereit?")
    p.add_argument("--max-tokens", type=int, default=20)
    p.set_defaults(func=cmd_llm_ping)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
