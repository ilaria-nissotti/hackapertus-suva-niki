"""Calibrate the triage cutoff and evaluate independent historical cases."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from urllib.parse import quote

from bger import fetch_opencaselaw, model_config
from triage import CUTOFF, classify, historical_cases

DEVELOPMENT_NEGATIVES = ("5A_195/2026", "2C_645/2025", "8C_111/2026")
HOLDOUT_NEGATIVES = ("1C_221/2024", "5A_12/2026")


def metrics(results, cutoff):
    scored = [r for r in results if "score" in r]
    tp = sum(r["expected"] and r["score"] >= cutoff for r in scored)
    fp = sum(not r["expected"] and r["score"] >= cutoff for r in scored)
    fn = sum(r["expected"] for r in results) - tp
    return {"cutoff": cutoff, "tp": tp, "fp": fp, "fn": fn,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f2": 5 * tp / (5 * tp + 4 * fn + fp) if tp + fn + fp else None,
            "failed": len(results) - len(scored)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positive-limit", type=int, help="Limit positives per split for a smoke run")
    args = parser.parse_args()
    if args.positive_limit is not None and args.positive_limit < 1:
        parser.error("--positive-limit must be positive")
    items = historical_cases()
    allowed = set(items["2025 Q4"])
    dev = items["2025 Q4"][:args.positive_limit]
    held = items["2026 Q1"][:args.positive_limit]
    config = model_config()

    def evaluate(item):
        docket, expected = item
        record = {"case": docket, "expected": expected}
        try:
            source = fetch_opencaselaw("decisions/" + quote("bger_" + docket.replace("/", "_")))
            if source.get("docket_number") != docket or not source.get("full_text"):
                raise ValueError("Could not match an original ruling")
            record.update(classify(config, {**source, "docket": docket}, allowed))
        except (OSError, ValueError) as exc:
            record["error"] = f"HTTP {exc.code}" if hasattr(exc, "code") else type(exc).__name__
        print(json.dumps(record, ensure_ascii=False), flush=True)
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        dev_results = list(pool.map(evaluate, [(d, True) for d in dev] +
                                    [(d, False) for d in DEVELOPMENT_NEGATIVES]))
        cutoff = max((metrics(dev_results, c) for c in (45, 70, 95)),
                     key=lambda row: (row["f2"] or 0, -abs(row["cutoff"] - CUTOFF)))
        held_results = list(pool.map(evaluate, [(d, True) for d in held] +
                                     [(d, False) for d in HOLDOUT_NEGATIVES]))
    print(json.dumps({"development": cutoff, "holdout": metrics(held_results, cutoff["cutoff"]),
                      "current_dashboard_cutoff": CUTOFF,
                      "smoke_run": args.positive_limit is not None}, indent=2))


if __name__ == "__main__":
    main()
