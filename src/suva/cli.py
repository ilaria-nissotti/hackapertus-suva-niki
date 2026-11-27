"""Command line entry point: `python -m suva <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

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
        path = args.manifest
        exists = path is not None and path.exists()
        manifest = json.loads(path.read_text(encoding="utf-8")) if exists else None
        result = apply_split(repo, args.n_eval, manifest)
        if path is not None and not exists:
            with path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(repo.get_example_manifest(), indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        repo.close()
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Download judgements for a window (+ Suva's picks) from OpenCaseLaw and entscheidsuche."""
    from datetime import date

    from suva.fetch import fetch
    from suva.windows import Window, challenge_window, dev_window

    settings = load_settings()
    if args.start and args.end:
        window = Window("custom", date.fromisoformat(args.start), date.fromisoformat(args.end))
    elif args.window == "challenge":
        window = challenge_window(date.fromisoformat(args.today) if args.today else date.today())
    else:
        window = dev_window()
    print(window, f"(published from {window.published_from})" if window.published_from else "")
    repo = open_repository(settings)
    try:
        rep = fetch(
            repo, settings.data_dir / "raw", window,
            use_entscheidsuche=not args.no_entscheidsuche, include_picks=not args.no_picks,
            html_for_picks=not args.no_html, refresh_opencaselaw=args.refresh_opencaselaw,
        )
    finally:
        repo.close()
    print(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False))
    return 1 if rep.picks_missing or rep.picks_ambiguous or rep.picks_without_text or rep.errors else 0


def cmd_coverage(args: argparse.Namespace) -> int:
    """Per-week decision counts per source; flags weeks where one source has gaps."""
    from suva.coverage import coverage_by_week, publication_coverage

    repo = open_repository(load_settings())
    try:
        audits = repo.list_audit(kind="fetch_run", limit=1)
        run = audits[0]["payload"] if audits else {}
        weeks = coverage_by_week(repo, args.start, args.end, run=run)
    finally:
        repo.close()
    cols = ["total", "opencaselaw", "entscheidsuche", "only_opencaselaw", "only_entscheidsuche", "no_text"]
    if "sightings" in run:
        print("Latest fetch source status:", json.dumps(run["source_runs"]))
    else:
        print("Historical sightings (run-level coverage unavailable)")
    print("week        " + "".join(f"{c:>20}" for c in cols) + "  Queries")
    for w in weeks:
        queries = ", ".join(f"{source}: {status}" for source, status in w.get("queries", {}).items())
        print(f"{w['week']}  " + "".join(f"{w.get(c, 0):>20}" for c in cols) + f"  {queries}")
    tot = {c: sum(w.get(c, 0) for w in weeks) for c in cols}
    print("TOTAL       " + "".join(f"{tot[c]:>20}" for c in cols))
    if run.get("published_from"):
        print(f"Publication coverage for {run['published_from']} through {run['end']}:",
              json.dumps(publication_coverage(run)))
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
    p.add_argument("--n-eval", type=int, default=15, help="number of docket groups to reserve")
    p.add_argument("--manifest", type=Path, help="write or replay a split manifest JSON file")
    p.set_defaults(func=cmd_split_examples)

    p = sub.add_parser("fetch", help="download judgements for the dev or challenge window (+ Suva picks)")
    p.add_argument("--window", choices=["dev", "challenge"], default="dev")
    p.add_argument("--today", help="reference date for the challenge window (YYYY-MM-DD, default: today)")
    p.add_argument("--start", help="custom window start (YYYY-MM-DD, with --end)")
    p.add_argument("--end", help="custom window end (YYYY-MM-DD)")
    p.add_argument("--no-entscheidsuche", action="store_true", help="OpenCaseLaw only (offline after download)")
    p.add_argument("--no-picks", action="store_true", help="skip Suva's picked judgements")
    p.add_argument("--no-html", action="store_true", help="skip HTML download for the picks")
    p.add_argument("--refresh-opencaselaw", action="store_true", help="re-download the parquet (~925 MB)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("coverage", help="per-week coverage per source for a decision-date range")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("llm-ping", help="one live call to check the Apertus endpoint")
    p.add_argument("--prompt", default="Antworte mit genau einem Wort: Bereit?")
    p.add_argument("--max-tokens", type=int, default=20)
    p.set_defaults(func=cmd_llm_ping)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
