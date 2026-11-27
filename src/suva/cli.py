"""Command line entry point: `python -m suva <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from suva.config import REPO_ROOT, load_settings
from suva.storage import open_repository


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    with closing(open_repository(settings, migrate=False)) as repo:
        applied = repo.migrate()
    print(f"KB: {settings.sqlite_path}")
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


def cmd_import_supplied(args: argparse.Namespace) -> int:
    from suva.ingest.importer import import_supplied

    settings = load_settings()
    with closing(open_repository(settings)) as repo:
        rep = import_supplied(repo, Path(args.dir) if args.dir else REPO_ROOT / "judgements")
    d = rep.as_dict()
    notes = d.pop("notes")
    print(json.dumps(d, indent=2))
    for n in notes:
        print("note:", n)
    return 0


def cmd_split_examples(args: argparse.Namespace) -> int:
    from suva.ingest.split import apply_split

    with closing(open_repository(load_settings())) as repo:
        path = args.manifest
        exists = path is not None and path.exists()
        manifest = json.loads(path.read_text(encoding="utf-8")) if exists else None
        result = apply_split(repo, args.n_eval, manifest)
        if path is not None and not exists:
            with path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(repo.get_example_manifest(), indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, indent=2))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
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
    with closing(open_repository(settings)) as repo:
        rep = fetch(
            repo, settings.data_dir / "raw", window,
            use_entscheidsuche=not args.no_entscheidsuche, include_picks=not args.no_picks,
            html_for_picks=not args.no_html, text_fallback=not args.no_text_fallback, refresh_opencaselaw=args.refresh_opencaselaw,
        )
    print(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False))
    return 1 if rep.picks_missing or rep.picks_ambiguous or rep.picks_without_text or rep.errors else 0


def cmd_coverage(args: argparse.Namespace) -> int:
    from suva.coverage import coverage_by_week, publication_coverage

    with closing(open_repository(load_settings())) as repo:
        audits = repo.list_audit(kind="fetch_run", limit=1)
        run = audits[0]["payload"] if audits else {}
        weeks = coverage_by_week(repo, args.start, args.end, run=run)
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


def cmd_parse(args: argparse.Namespace) -> int:
    from suva.parse.run import parse_all

    with closing(open_repository(load_settings())) as repo:
        rep = parse_all(repo, force=args.force)
    print(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False))
    return 1 if rep.status.get("stale") else 0


def _scoring_dir() -> Path:
    return load_settings().data_dir / "scoring"


def _dataset_path(args: argparse.Namespace) -> Path:
    return Path(args.dataset) if getattr(args, "dataset", None) else _scoring_dir() / "dataset.jsonl"


def cmd_dataset(args: argparse.Namespace) -> int:
    from datetime import date

    from suva.scoring.dataset import TEST_FROM, build_dataset
    from suva.windows import DEV_END, DEV_START

    out = _dataset_path(args)
    with closing(open_repository(load_settings())) as repo:
        rep = build_dataset(
            repo, out,
            start=date.fromisoformat(args.start) if args.start else DEV_START,
            end=date.fromisoformat(args.end) if args.end else DEV_END,
            test_from=date.fromisoformat(args.test_from) if args.test_from else TEST_FROM,
        )
    print(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False))
    print(f"wrote {out}")
    if not rep.records:
        print("note: no judgements in the period yet: run `fetch --window dev` and `parse` first", file=sys.stderr)
        return 1
    if rep.stale_parses or rep.parse_status.get("not_parsed"):
        print(f"note: {rep.stale_parses} stale and {rep.parse_status.get('not_parsed', 0)} unparsed judgements: "
              "run `python -m suva parse`, then `dataset` again", file=sys.stderr)
    return 0


def _print_stats(title: str, stats) -> None:
    print(f"\n{title}")
    print(f"  {'field':<9} {'value':<45} {'rel':>5} {'not':>6} {'%rel':>6} {'%not':>6} {'lift':>7} {'prec':>5}")
    for s in stats:
        print(f"  {s.field:<9} {s.value[:45]:<45} {s.n_rel:>5} {s.n_non:>6} {s.share_rel:>6.0%} "
              f"{s.share_non:>6.0%} {s.lift:>7.1f} {s.precision:>5.0%}")


def cmd_analyze(args: argparse.Namespace) -> int:
    from suva.scoring import analyze
    from suva.scoring.dataset import load_dataset

    records = load_dataset(_dataset_path(args), args.split)
    n_rel = sum(r["relevant"] for r in records)
    print(f"split: {args.split}  judgements: {len(records)}  relevant: {n_rel}  not relevant: {len(records) - n_rel}")
    for field, table in analyze.overview(records).items():
        print(f"\nby {field}:  (relevant / not relevant)")
        for value, (rel, non) in list(table.items())[:12]:
            print(f"  {value:<12} {rel:>5} / {non:<6}")
    print("\nSuva as a party:", json.dumps(analyze.suva_check(records)))

    stats, _, _ = analyze.feature_stats(records)
    _print_stats(f"Positive rule candidates (in >= {args.min_rel} relevant, most over-represented):",
                 analyze.positive_candidates(stats, args.min_rel, args.top))
    _print_stats(f"Negative rule candidates (in >= {args.min_non} not relevant, most under-represented):",
                 analyze.negative_candidates(stats, args.min_non, args.top))
    out = _scoring_dir() / f"features_{args.split}.csv"
    analyze.write_csv(stats, out)
    print(f"\nall {len(stats)} features: {out}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from suva.scoring.dataset import load_dataset
    from suva.scoring.evaluate import evaluate, load_verdicts, write_scores
    from suva.scoring.rules import load_rules

    ruleset = load_rules(args.rules)
    if args.threshold is not None:
        ruleset.threshold = args.threshold
    records = load_dataset(_dataset_path(args), args.split)
    verdicts = None
    if args.with_judge:
        if not ruleset.judge_points:
            print("note: --with-judge needs judge.points in the rules file; ignoring verdicts", file=sys.stderr)
        verdicts = load_verdicts(_scoring_dir() / f"judge_{args.split}.jsonl")
    ev = evaluate(records, ruleset, args.split, n_cases=args.cases, verdicts=verdicts)
    m, rk = ev.at_threshold, ev.ranking
    print(f"split: {args.split}  judgements: {ev.n}  relevant: {ev.n_rel}  rules: {len(ruleset.rules)}"
          + (f"  judge verdicts used: {ev.judged}" if verdicts is not None else ""))
    print("\nRanking (how lawyers use the sorted list):")
    print("  relevant in top k:   " + "   ".join(f"top {k}: {p:.0%}" for k, p in rk.precision_at.items()))
    print("  read down to find:   " + "   ".join(
        f"{t:.0%} of relevant: {d if d is not None else '-'}" for t, d in rk.depth_for_recall.items()))
    print(f"  average precision {rk.average_precision:.2f}   AUC {rk.auc:.3f}")
    print(f"\nAt threshold {m.threshold:g}: flagged {m.flagged}, precision {m.precision:.0%}, recall {m.recall:.0%} "
          f"(missed {m.false_neg} relevant, {m.false_pos} false alarms)")
    print(f"\nBands (high >= {ruleset.bands['high']:g}, low < {ruleset.bands['low']:g}):  relevant / not relevant")
    for band, (rel, non) in ev.bands.items():
        print(f"  {band:<6} {rel:>5} / {non:<6}")
    print("\nThreshold sweep:")
    print(f"  {'thr':>4} {'flagged':>8} {'precision':>10} {'recall':>7}")
    for s in ev.sweep:
        print(f"  {s.threshold:>4g} {s.flagged:>8} {s.precision:>10.0%} {s.recall:>7.0%}")
    print("\nCalibration (does a score of ~X mean ~X% relevant?):")
    print(f"  {'score':>7} {'n':>6} {'relevant':>9} {'mean score':>11} {'actual %':>9}")
    for c in ev.calibration:
        print(f"  {c['bin']:>7} {c['n']:>6} {c['relevant']:>9} {c['mean_score']:>11} {c['actual_rate']:>9}")
    print("\nRule evidence (paste into rules.yaml `evidence:`):")
    for e in ev.evidence:
        print(f"  {e.id:<32} {e.weight:>+6g}  {e.text}")
    for title, cases in (("Missed relevant judgements", ev.misses), ("False alarms", ev.false_alarms)):
        print(f"\n{title} ({len(cases)} shown):")
        for c in cases:
            print(f"  {c['score']:>5g}  {c['key']:<28} {c['chamber'] or '?':<3} {c['subject'][:90]}")
            print(f"         fired: {', '.join(c['fired']) or '-'}")
    if args.split == "test":
        print("\nnote: test split. Use it for the final number, not for tuning rules.")
    out = _scoring_dir() / f"scores_{args.split}.csv"
    write_scores(records, ruleset, out, verdicts)
    (_scoring_dir() / f"evaluation_{args.split}.json").write_text(
        json.dumps(ev.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nscores: {out}")
    return 0


def cmd_fit_weights(args: argparse.Namespace) -> int:
    from suva.scoring.dataset import load_dataset
    from suva.scoring.fit import fit_weights, write_fitted
    from suva.scoring.rules import load_rules

    ruleset = load_rules(args.rules)
    records = load_dataset(_dataset_path(args), "train")
    fitted = fit_weights(records, ruleset, l2=args.l2)
    print(f"fitted on {fitted.n} training judgements ({fitted.n_rel} relevant)")
    print(f"  {'rule':<32} {'current':>8} {'suggested':>10}")
    print(f"  {'(base)':<32} {ruleset.base:>8g} {fitted.base:>10}")
    for rule_id, w in fitted.weights.items():
        unit = " per match" if rule_id in fitted.graded else ""
        print(f"  {rule_id:<32} {fitted.current[rule_id]:>8g} {w:>10}{unit}")
    print(f"  suggested threshold (keeps 90% recall on train): {fitted.threshold:g}   current: {ruleset.threshold:g}")
    out = ruleset.path.with_name("rules.fitted.yaml")
    write_fitted(ruleset, fitted, out)
    print(f"\nwrote {out} (review, then copy the weights you agree with into rules.yaml)")
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    from suva.llm import LLMClient
    from suva.scoring.dataset import load_dataset
    from suva.scoring.judge import judge_record
    from suva.scoring.rules import load_rules
    from suva.scoring.score import score_record

    settings = load_settings()
    ruleset = load_rules(args.rules)
    cutoff = args.candidate_threshold if args.candidate_threshold is not None \
        else float(ruleset.judge.get("candidate_threshold", 20))
    records = load_dataset(_dataset_path(args), args.split)
    candidates = [(r, s) for r in records if (s := score_record(r, ruleset)).score >= cutoff]
    candidates.sort(key=lambda rs: -rs[1].score)
    if args.limit:
        candidates = candidates[:args.limit]
    print(f"split: {args.split}  candidates (rules score >= {cutoff:g}): {len(candidates)} of {len(records)}")

    out = _scoring_dir() / f"judge_{args.split}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    verdicts = []
    with closing(open_repository(settings)) as repo, out.open("w", encoding="utf-8") as f:
        llm = LLMClient(settings, repo=repo)
        for i, (rec, s) in enumerate(candidates, 1):
            try:
                v = judge_record(llm, rec, ruleset, s.score)
            except Exception as exc:  # keep going; the failure is recorded per judgement
                print(f"  [{i}/{len(candidates)}] {rec['key']}: FAILED {type(exc).__name__}", file=sys.stderr)
                continue
            verdicts.append((rec, v))
            f.write(json.dumps({**v.as_dict(), "relevant_label": rec["relevant"]}, ensure_ascii=False) + "\n")
            print(f"  [{i}/{len(candidates)}] {rec['key']:<28} rules {s.score:>5.1f}  judge {v.relevance or v.error}"
                  f"{'  (cached)' if v.cached else ''}")

    usable = [(r, v) for r, v in verdicts if v.relevance]
    n_rel = sum(r["relevant"] for r in records)
    for name, flagged in (
        ("rules only", [r for r, s in candidates if s.score >= ruleset.threshold]),
        ("judge only (high/medium)", [r for r, v in usable if v.relevant]),
        ("rules AND judge", [r for r, v in usable if v.relevant and v.rules_score >= ruleset.threshold]),
        ("rules OR judge", [r for r, v in usable if v.relevant or v.rules_score >= ruleset.threshold]),
    ):
        tp = sum(r["relevant"] for r in flagged)
        prec = tp / len(flagged) if flagged else 0.0
        rec_ = tp / n_rel if n_rel else 0.0
        print(f"  {name:<26} flagged {len(flagged):>4}  precision {prec:>4.0%}  recall {rec_:>4.0%}")
    print(f"\nverdicts: {out}")
    return 0


def cmd_llm_ping(args: argparse.Namespace) -> int:
    from suva.llm import LLMClient

    settings = load_settings()
    print(f"endpoint host: {settings.llm_host}  model: {settings.llm_name}")
    with closing(open_repository(settings)) as repo:
        llm = LLMClient(replace(settings, llm_cache_mode="off"), repo=repo)
        try:
            r = llm.chat(
                [{"role": "user", "content": args.prompt}],
                prompt_version="ping/v1", max_tokens=args.max_tokens,
            )
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}", file=sys.stderr)
            return 1
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
    p.add_argument("--no-text-fallback", action="store_true",
                   help="don't fetch entscheidsuche HTML for decisions OpenCaseLaw has no usable text for")
    p.add_argument("--refresh-opencaselaw", action="store_true", help="re-download the parquet (~925 MB)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("coverage", help="per-week coverage per source for a decision-date range")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("parse", help="split judgements into passages (E. numbers) and extract citations")
    p.add_argument("--force", action="store_true", help="re-parse everything, not only new/changed texts")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("dataset", help="build the labelled dataset (Excel = relevant) with a time-based split")
    p.add_argument("--start", help="first decision date (default: 2025-10-01)")
    p.add_argument("--end", help="last decision date (default: 2026-03-31)")
    p.add_argument("--test-from", help="decisions on/after this date are the test set (default: 2026-03-01)")
    p.add_argument("--dataset", help="output path (default: data/scoring/dataset.jsonl)")
    p.set_defaults(func=cmd_dataset)

    split_choices = ["train", "test", "all"]
    p = sub.add_parser("analyze", help="find candidate rules: features that separate relevant from not relevant")
    p.add_argument("--split", choices=split_choices, default="train")
    p.add_argument("--min-rel", type=int, default=3, help="minimum relevant judgements for a positive candidate")
    p.add_argument("--min-non", type=int, default=20, help="minimum not-relevant judgements for a negative one")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("evaluate", help="score a split with rules.yaml: precision, recall, misses, false alarms")
    p.add_argument("--split", choices=split_choices, default="train")
    p.add_argument("--rules", help="rules file (default: rules/rules.yaml)")
    p.add_argument("--threshold", type=float, help="override the threshold in the rules file")
    p.add_argument("--cases", type=int, default=15, help="misses / false alarms to list")
    p.add_argument("--with-judge", action="store_true",
                   help="add judge.points from the verdicts of `judge` (data/scoring/judge_<split>.jsonl)")
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("fit-weights", help="suggest rule weights from the training data (rules.fitted.yaml)")
    p.add_argument("--rules")
    p.add_argument("--l2", type=float, default=1.0, help="regularisation strength (higher = smaller weights)")
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_fit_weights)

    p = sub.add_parser("judge", help="LLM judge: Apertus rates candidate judgements with the rubric in rules.yaml")
    p.add_argument("--split", choices=split_choices, default="train")
    p.add_argument("--rules")
    p.add_argument("--candidate-threshold", type=float, help="override judge.candidate_threshold")
    p.add_argument("--limit", type=int, help="judge at most N candidates (highest rules score first)")
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("llm-ping", help="one live call to check the Apertus endpoint")
    p.add_argument("--prompt", default="Antworte mit genau einem Wort: Bereit?")
    p.add_argument("--max-tokens", type=int, default=20)
    p.set_defaults(func=cmd_llm_ping)

    args = parser.parse_args(argv)
    if args.command == "fetch" and bool(args.start) != bool(args.end):
        parser.error("fetch requires both --start and --end for a custom range")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
