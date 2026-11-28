"""Command line entry point: `python -m suva <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from suva.config import REPO_ROOT, configure_logging, load_settings
from suva.storage import open_repository

if TYPE_CHECKING:
    from suva.scoring.rules import RuleSet


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
    from suva.fetch import fetch
    from suva.windows import Window

    settings = load_settings()
    if args.start and args.end:
        window = Window("custom", date.fromisoformat(args.start), date.fromisoformat(args.end))
    else:
        window = _window(args)
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


def _window(args: argparse.Namespace):
    from suva.windows import challenge_window, dev_window, publication_window

    if args.published_from is not None:
        return publication_window(args.published_from, args.published_to)
    if args.window == "challenge":
        return challenge_window(date.fromisoformat(args.today) if args.today else date.today())
    return dev_window()


def cmd_score(args: argparse.Namespace) -> int:
    from suva.scoring.rules import load_rules
    from suva.scoring.store import score_window, window_judgements

    window, ruleset = _window(args), load_rules(args.rules)
    with closing(open_repository(load_settings())) as repo:
        rep = score_window(repo, window, ruleset)
        in_window = {row["key"] for row in window_judgements(repo, window)}
        top = [s for s in repo.list_scores() if s["key"] in in_window][:args.top]
    print(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False))
    print(f"\ntop {len(top)} stored scores:")
    for s in top:
        print(f"  {s['score']:>5.1f}  {s['band']:<5} {s['key']:<28} "
              + "; ".join(f"{f['points']:+g} {f['explanation']}" for f in s["fired"][:3]))
    if rep.not_parsed or rep.stale_parses:
        print(f"note: {rep.not_parsed} unparsed and {rep.stale_parses} stale judgements: "
              "run `python -m suva parse`, then `score` again", file=sys.stderr)
    return 0


def _scoring_dir() -> Path:
    return load_settings().data_dir / "scoring"


def _dataset_path(args: argparse.Namespace) -> Path:
    return Path(args.dataset) if getattr(args, "dataset", None) else _scoring_dir() / "dataset.jsonl"


def _scoring_records(args: argparse.Namespace, ruleset: RuleSet, split: str) -> list[dict]:
    """Records of a split, with the derived fields the rules use."""
    from suva.scoring.dataset import load_dataset

    return load_dataset(_dataset_path(args), split)


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


def _with_selection(ruleset: RuleSet, args: argparse.Namespace) -> RuleSet:
    """The rules file's selection, with --top-n / --floor-rule-points / --per overrides."""
    sel = ruleset.selection
    if getattr(args, "top_n", None) is not None:
        sel = replace(sel, top_n=args.top_n if args.top_n > 0 else None)
    if getattr(args, "floor_rule_points", None) is not None:
        sel = replace(sel, floor_rule_points=args.floor_rule_points)
    if getattr(args, "no_floor", False):
        sel = replace(sel, floor_rule_points=None)
    if getattr(args, "per", None):
        sel = replace(sel, per=args.per)
    return replace(ruleset, selection=sel)


def _period_word(per: str, count: int) -> str:
    word = {"month": "month", "week": "week", "period": "period"}[per]
    return word if count == 1 else word + "s"


def _selection_text(ruleset: RuleSet) -> str:
    sel = ruleset.selection
    top = f"top {sel.top_n}" if sel.top_n else "all"
    floor = (f", none below {sel.floor_rule_points:g} rule points" if sel.floor_rule_points is not None
             else ", no floor")
    per = " per month" if sel.per == "month" else " per week" if sel.per == "week" else " of the whole period"
    return top + per + floor


def cmd_evaluate(args: argparse.Namespace) -> int:
    from suva.scoring.evaluate import evaluate, load_verdicts, write_scores
    from suva.scoring.rules import load_rules

    ruleset = _with_selection(load_rules(args.rules), args)
    records = _scoring_records(args, ruleset, args.split)
    verdicts = None
    if args.with_judge:
        if not ruleset.judge_points:
            print("note: --with-judge needs judge.points in the rules file; ignoring verdicts", file=sys.stderr)
        verdicts = load_verdicts(_scoring_dir() / f"judge_{args.split}.jsonl")
    ev = evaluate(records, ruleset, args.split, n_cases=args.cases, verdicts=verdicts)
    sel, rk = ev.selection, ev.ranking
    print(f"split: {args.split}  judgements: {ev.n}  relevant: {ev.n_rel}  rules: {len(ruleset.rules)}"
          + (f"  judge verdicts used: {ev.judged}" if verdicts is not None else ""))
    print(f"\nReading list: {_selection_text(ruleset)}")
    print(f"  shown {sel.shown} in {sel.periods} {_period_word(sel.per, sel.periods)}: "
          f"recall at N {sel.recall:.0%} ({sel.true_pos} of {sel.n_rel} relevant), precision at N {sel.precision:.0%}")
    print(f"  equal points ordered not-relevant-first (above, the lower bound); in random order (expected): "
          f"recall {sel.expected_recall:.0%}, precision {sel.expected_precision:.0%}")
    print(f"  {'period':<9} {'judgements':>10} {'relevant':>9} {'shown':>6} {'found':>6}")
    for row in sel.per_period:
        print(f"  {row['period']:<9} {row['n']:>10} {row['relevant']:>9} {row['shown']:>6} {row['found']:>6}")
    if ev.budget:
        print(f"\nReading budget (same floor and periods, other N):")
        print(f"  {'N':>4} {'shown':>6} {'recall at N':>12} {'precision at N':>15}   {'expected, ties random':>22}")
        for b in ev.budget:
            mark = "  <- rules.yaml" if b.top_n == sel.top_n else ""
            print(f"  {b.top_n:>4} {b.shown:>6} {b.recall:>12.0%} {b.precision:>15.0%}   "
                  f"{b.expected_recall:>10.0%} / {b.expected_precision:<9.0%}{mark}")
    print("\nWhole ranking (secondary):")
    print("  relevant in top k:   " + "   ".join(f"top {k}: {p:.0%}" for k, p in rk.precision_at.items()))
    print("  read down to find:   " + "   ".join(
        f"{t:.0%} of relevant: {d if d is not None else '-'}" for t, d in rk.depth_for_recall.items()))
    print(f"  average precision {rk.average_precision:.2f}   AUC {rk.auc:.3f}")
    print("\nRule evidence (paste into rules.yaml `evidence:`):")
    for e in ev.evidence:
        print(f"  {e.id:<32} {e.weight:>+6g}  {e.text}")
    for title, cases in (("Relevant judgements not in the reading list", ev.misses),
                         ("Not relevant judgements in the reading list", ev.false_alarms)):
        print(f"\n{title} ({len(cases)} listed; score = base + rule points, rank in period):")
        for c in cases:
            print(f"  {c['score']:>+6g} #{c['period_rank']:<4} {c['key']:<28} {c['chamber'] or '?':<3} "
                  f"{c['subject'][:80]}")
            print(f"               fired: {', '.join(c['fired']) or '-'}")
    if args.split == "test":
        print("\nnote: test split. Use it for the final number, not for tuning rules.")
    out = _scoring_dir() / f"scores_{args.split}.csv"
    write_scores(records, ruleset, out, verdicts)
    (_scoring_dir() / f"evaluation_{args.split}.json").write_text(
        json.dumps(ev.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nscores: {out}")
    return 0


def cmd_fit_weights(args: argparse.Namespace) -> int:
    from suva.scoring.fit import FLOOR_RECALL, fit_weights, write_fitted
    from suva.scoring.rules import load_rules

    ruleset = load_rules(args.rules)
    records = _scoring_records(args, ruleset, "train")
    fitted = fit_weights(records, ruleset, l2=args.l2)
    print(f"fitted on {fitted.n} training judgements ({fitted.n_rel} relevant)")
    print(f"  {'rule':<32} {'current':>8} {'suggested':>10}")
    print(f"  {'(base)':<32} {ruleset.base:>8g} {fitted.base:>10}")
    for rule_id, w in fitted.weights.items():
        unit = " per match" if rule_id in fitted.graded else ""
        print(f"  {rule_id:<32} {fitted.current[rule_id]:>8g} {w:>10}{unit}")
    current_floor = ruleset.selection.floor_rule_points
    print(f"  suggested selection.floor_rule_points (keeps {FLOOR_RECALL:.0%} of relevant training judgements): "
          f"{fitted.floor_rule_points:g}   current: {'-' if current_floor is None else f'{current_floor:g}'}")
    print("  (a floor belongs to one set of weights: if you copy the weights, copy the floor too, "
          "and review judge.candidate_floor_rule_points; base does not affect either)")
    out = ruleset.path.with_name("rules.fitted.yaml")
    write_fitted(ruleset, fitted, out)
    print(f"\nwrote {out} (review, then copy the weights you agree with into rules.yaml)")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from suva.scoring.compare import FOLDS, compare
    from suva.scoring.dataset import load_dataset
    from suva.scoring.rules import load_rules

    ruleset = load_rules(args.rules)
    records = load_dataset(_dataset_path(args), "train")
    n_rel = sum(r["relevant"] for r in records)
    print(f"train split: {len(records)} judgements, {n_rel} relevant; {FOLDS}-fold cross-validation by docket "
          "(weights fitted without the scored judgements)")
    results = compare(records, ruleset,
                      progress=lambda name: print(f"  fitting: {name}", file=sys.stderr))

    print(f"\n  {'configuration':<36} {'AP':>5} {'top10':>6} {'top20':>6} {'top100':>7} "
          f"{'read for 90%':>13} {'read for 100%':>14} {'AUC':>6}")
    for r in results:
        rk = r.ranking
        print(f"  {r.name:<36} {rk.average_precision:>5.2f} {rk.precision_at.get(10, 0):>6.0%} "
              f"{rk.precision_at.get(20, 0):>6.0%} {rk.precision_at.get(100, 0):>7.0%} "
              f"{rk.depth_for_recall.get(0.9) or '-':>13} {rk.depth_for_recall.get(1.0) or '-':>14} {rk.auc:>6.3f}")
    print("\n  AP = average precision (1.0 = all relevant judgements ranked first); top k = share relevant;")
    print("  read for X% = judgements to read from the top to find X% of the relevant ones.")

    sel = ruleset.selection
    print(f"\nReading list on held-out folds only: top {sel.top_n} per {sel.per}, each fold gets its share of a "
          f"{sel.per}'s places")
    print(f"  (N = round({sel.top_n} x judgements of the {sel.per} in the fold / judgements of the {sel.per})); "
          "floor = floor_rule_points fitted with each fold's weights.")
    print(f"\n  {'configuration':<36} {'TP':>4} {'FP':>5} {'FN':>4} {'TN':>6} {'precision':>10} {'recall':>7}")
    for r in results:
        c = r.confusion
        print(f"  {r.name:<36} {c.tp:>4} {c.fp:>5} {c.fn:>4} {c.tn:>6} {c.precision:>10.0%} {c.recall:>7.0%}")
    full = results[-1]  # all rules (topic + significance)
    c = full.confusion
    print(f"\nConfusion matrix, {full.name} (summed over {len(c.floors)} held-out folds):")
    print(f"  {'':<20} {'Selected':>10} {'Not selected':>13}")
    print(f"  {'Picked by Suva':<20} {c.tp:>10} {c.fn:>13}")
    print(f"  {'Not picked by Suva':<20} {c.fp:>10} {c.tn:>13}")
    print(f"  precision = TP / (TP + FP) = {c.tp}/{c.tp + c.fp} = {c.precision:.1%}")
    print(f"  recall    = TP / (TP + FN) = {c.tp}/{c.tp + c.fn} = {c.recall:.1%}")
    print(f"  top_n = {c.top_n} per {c.per}; floor_rule_points per fold = "
          + ", ".join("-" if f is None else f"{f:g}" for f in c.floors))
    print("  (accuracy is not reported: about 1.5% of judgements are picks, so it would look high for any list)")
    out = _scoring_dir() / "comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"n": len(records), "n_relevant": n_rel, "folds": FOLDS,
                               "results": [r.as_dict() for r in results]}, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    from suva.llm import LLMClient
    from suva.scoring.judge import candidates as judge_candidates
    from suva.scoring.judge import judge_record
    from suva.scoring.rules import load_rules
    from suva.scoring.select import select

    settings = load_settings()
    ruleset = load_rules(args.rules)
    floor = args.candidate_floor_rule_points if args.candidate_floor_rule_points is not None         else ruleset.judge_floor_rule_points
    records = _scoring_records(args, ruleset, args.split)
    candidates = judge_candidates(records, ruleset, floor, args.limit)
    floor_text = "all" if floor is None else f"rule points >= {floor:g}"
    print(f"split: {args.split}  candidates ({floor_text}): {len(candidates)} of {len(records)}")

    out = _scoring_dir() / f"judge_{args.split}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    verdicts = []
    with closing(open_repository(settings)) as repo, out.open("w", encoding="utf-8") as f:
        llm = LLMClient(settings, repo=repo)
        for i, (rec, s) in enumerate(candidates, 1):
            try:
                v = judge_record(llm, rec, ruleset, s.rule_points)
            except Exception as exc:  # keep going; the failure is recorded per judgement
                print(f"  [{i}/{len(candidates)}] {rec['key']}: FAILED {type(exc).__name__}", file=sys.stderr)
                continue
            verdicts.append((rec, v))
            f.write(json.dumps({**v.as_dict(), "relevant_label": rec["relevant"]}, ensure_ascii=False) + "\n")
            print(f"  [{i}/{len(candidates)}] {rec['key']:<28} rule points {s.rule_points:>+6g}  judge {v.relevance or v.error}"
                  f"{'  (cached)' if v.cached else ''}")

    usable = [(r, v) for r, v in verdicts if v.relevance]
    n_rel = sum(r["relevant"] for r in records)
    listed = {item.key for item in select(records, ruleset)}  # the reading list without the judge
    judged_yes = {r["key"] for r, v in usable if v.relevant}
    by_key = {r["key"]: r for r in records}
    print(f"\nreading list from rules: {_selection_text(ruleset)}")
    for name, keys in (
        ("rules only (reading list)", listed),
        ("judge only (high/medium)", judged_yes),
        ("rules AND judge", listed & judged_yes),
        ("rules OR judge", listed | judged_yes),
    ):
        flagged = [by_key[k] for k in keys]
        tp = sum(r["relevant"] for r in flagged)
        prec = tp / len(flagged) if flagged else 0.0
        rec_ = tp / n_rel if n_rel else 0.0
        print(f"  {name:<26} flagged {len(flagged):>4}  precision {prec:>4.0%}  recall {rec_:>4.0%}")
    print(f"\nverdicts: {out}")
    return 0


def cmd_cv(args: argparse.Namespace) -> int:
    from suva.scoring.cv import fold_n, per_period_size, run_cv, summarise
    from suva.scoring.dataset import load_dataset
    from suva.scoring.rules import load_rules

    path = _dataset_path(args)
    if not path.exists():
        print(f"dataset not found: {path} (run `python -m suva dataset` first)", file=sys.stderr)
        return 1
    ruleset = _with_selection(load_rules(args.rules), args)
    sel = ruleset.selection
    records = load_dataset(path, "train")
    period_size = per_period_size(records, sel.per)
    fold_size = len(records) / args.folds
    print(f"{len(records)} training judgements, {args.folds} folds by docket x seeds {args.seeds}; "
          "mean ± spread across folds and seeds")
    print(f"Reading list: {_selection_text(ruleset)}. A fold is not a {sel.per}, so N is scaled to its size:")
    print(f"  N per fold = {sel.top_n} x fold size / {period_size:.0f} judgements per {sel.per} in train "
          f"(about {fold_n(sel.top_n, round(fold_size), period_size)} for a fold of {fold_size:.0f}).")
    print("  Floor: rules.yaml for current, refitted with the weights for refit and shuffled, none for random.")
    print(f"\n  {'mode':<10} {'AP (ties)':>14} {'random AP':>10} {'recall@N':>9} {'prec@N':>7} "
          f"{'AP (pessim.)':>13} {'P@20':>6} {'AUC':>6} {'read 90%':>9}")
    for mode in args.modes:
        per_fold = run_cv(records, ruleset, mode, k=args.folds, seeds=tuple(args.seeds), l2=args.l2)
        s = summarise(per_fold)
        ap, sd = s["average_precision_ties"]
        print(f"  {mode:<10} {ap:>8.3f} ±{sd:<5.3f} {s['random_ap'][0]:>10.3f} {s['recall_at_n'][0]:>9.0%} "
              f"{s['precision_at_n'][0]:>7.0%} {s['average_precision'][0]:>13.3f} {s['precision_at_20'][0]:>6.0%} "
              f"{s['auc'][0]:>6.3f} {s['depth_90'][0]:>8.0%}")
    print("\nAP (ties) and recall/precision at N average over the order of equal points. random AP is the null")
    print("for this fold size, not the base rate. read 90% = share of the fold read to find 90% of the relevant.")
    print("refit is the honest number for the current design; shuffled should sit well below it.")
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


def _selection_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--top-n", type=int, help="override selection.top_n (0 = no limit)")
    p.add_argument("--floor-rule-points", type=float, help="override selection.floor_rule_points (no base)")
    p.add_argument("--no-floor", action="store_true", help="ignore selection.floor_rule_points")
    p.add_argument("--per", choices=["month", "week", "period"], help="override selection.per")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
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
    p.add_argument("--published-from", type=date.fromisoformat, help="first publication date (YYYY-MM-DD)")
    p.add_argument("--published-to", type=date.fromisoformat, help="last publication date (YYYY-MM-DD)")
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

    p = sub.add_parser("score", help="score the window's judgements with rules.yaml and store it for the review app")
    p.add_argument("--window", choices=["dev", "challenge"], default="challenge")
    p.add_argument("--today", help="reference date for the challenge window (YYYY-MM-DD, default: today)")
    p.add_argument("--published-from", type=date.fromisoformat, help="first publication date (YYYY-MM-DD)")
    p.add_argument("--published-to", type=date.fromisoformat, help="last publication date (YYYY-MM-DD)")
    p.add_argument("--rules", help="rules file (default: rules/rules.yaml)")
    p.add_argument("--top", type=int, default=10, help="stored scores to print")
    p.set_defaults(func=cmd_score)

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

    p = sub.add_parser("evaluate", help="rank a split with rules.yaml: recall and precision of the top N per period")
    p.add_argument("--split", choices=split_choices, default="train")
    p.add_argument("--rules", help="rules file (default: rules/rules.yaml)")
    _selection_args(p)
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

    p = sub.add_parser("compare", help="what each group of signals adds: cross-validated ranking per configuration")
    p.add_argument("--rules")
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("judge", help="LLM judge: Apertus rates candidate judgements with the rubric in rules.yaml")
    p.add_argument("--split", choices=split_choices, default="train")
    p.add_argument("--rules")
    p.add_argument("--candidate-floor-rule-points", type=float,
                   help="override judge.candidate_floor_rule_points (rule points, no base)")
    p.add_argument("--limit", type=int, help="judge at most N candidates (most rules points first)")
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_judge)

    cv_modes = ["current", "refit", "shuffled", "random"]  # suva.scoring.cv.MODES, without importing numpy here
    p = sub.add_parser("cv", help="grouped cross-validation on train: AP and recall/precision of the top N per fold")
    p.add_argument("--rules")
    p.add_argument("--dataset")
    p.add_argument("--modes", nargs="+", choices=cv_modes, default=cv_modes)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    p.add_argument("--l2", type=float, default=1.0)
    p.add_argument("--top-n", type=int, help="override selection.top_n (scaled to each fold)")
    p.add_argument("--per", choices=["month", "week", "period"], help="override selection.per")
    p.set_defaults(func=cmd_cv)

    p = sub.add_parser("llm-ping", help="one live call to check the Apertus endpoint")
    p.add_argument("--prompt", default="Antworte mit genau einem Wort: Bereit?")
    p.add_argument("--max-tokens", type=int, default=20)
    p.set_defaults(func=cmd_llm_ping)

    args = parser.parse_args(argv)
    if args.command == "fetch" and bool(args.start) != bool(args.end):
        parser.error("fetch requires both --start and --end for a custom range")
    if args.command in ("fetch", "score"):
        first, last = args.published_from, args.published_to
        if (first is None) != (last is None):
            parser.error("specify both --published-from and --published-to")
        if first is not None and (first > last or args.today or getattr(args, "start", None)):
            parser.error("use an ascending publication range without --today, --start or --end")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
