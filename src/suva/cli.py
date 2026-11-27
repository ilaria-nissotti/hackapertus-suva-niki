"""Command line entry point: `python -m suva <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

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


def cmd_import_supplied(args: argparse.Namespace) -> int:
    """Parse judgements/ (exports + xlsx) into the KB. Safe to re-run."""
    from pathlib import Path

    from suva.config import REPO_ROOT
    from suva.ingest.importer import import_supplied

    settings = load_settings()
    repo = open_repository(settings)
    try:
        rep = import_supplied(repo, Path(args.dir) if args.dir else REPO_ROOT / "judgements")
    finally:
        repo.close()
    d = rep.as_dict()
    notes = d.pop("notes")
    print(json.dumps(d, indent=2))
    for n in notes:
        print("note:", n)
    return 0


def cmd_split_examples(args: argparse.Namespace) -> int:
    """Freeze the few-shot / eval split of the example summaries."""
    from suva.ingest.split import apply_split

    repo = open_repository(load_settings())
    try:
        print(json.dumps(apply_split(repo, args.n_eval), indent=2))
    finally:
        repo.close()
    return 0


def cmd_llm_ping(args: argparse.Namespace) -> int:
    """Check the gateway: one tiny uncached call, print model, latency, tokens."""
    from suva.llm import LLMClient

    settings = load_settings()
    repo = open_repository(settings)
    llm = LLMClient(replace(settings, llm_cache_mode="off"), repo=repo)
    print(f"endpoint host: {settings.llm_host}  model: {settings.llm_name}")
    try:
        r = llm.chat(
            [{"role": "user", "content": args.prompt}],
            prompt_version="ping/v1", temperature=0.0, max_tokens=args.max_tokens,
        )
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}", file=sys.stderr)
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

    p = sub.add_parser("import-supplied", help="load Suva's example summaries + picked list into the KB")
    p.add_argument("--dir", help="directory with Export_*.html and BG_Urteile_*.xlsx (default: judgements/)")
    p.set_defaults(func=cmd_import_supplied)

    p = sub.add_parser("split-examples", help="freeze the few-shot / eval split (never moves an assigned one)")
    p.add_argument("--n-eval", type=int, default=15)
    p.set_defaults(func=cmd_split_examples)

    p = sub.add_parser("llm-ping", help="one live call to check the Apertus endpoint")
    p.add_argument("--prompt", default="Antworte mit genau einem Wort: Bereit?")
    p.add_argument("--max-tokens", type=int, default=20)
    p.set_defaults(func=cmd_llm_ping)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
