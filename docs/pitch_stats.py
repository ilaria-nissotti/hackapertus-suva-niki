"""Recompute the numbers in docs/pitch-stats.md that no single CLI command prints.

    python docs/pitch_stats.py            # fast part: dataset, rules, judge and evaluation files
    python docs/pitch_stats.py --cv       # also the cross-validated distinct values and case ranks

Reads data/scoring/dataset.jsonl, data/scoring/evaluation_train.json, data/scoring/judge_train.jsonl,
data/scoring/comparison.json. Train split only, except the counts of
the whole dev window. Each line prints the section of pitch-stats.md it belongs to.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from pathlib import Path

from suva.scoring.dataset import load_dataset
from suva.scoring.rules import load_rules
from suva.scoring.score import score_record
from suva.scoring.select import ranked

ROOT = Path(__file__).resolve().parents[1]
SCORING = ROOT / "data" / "scoring"
DATASET = SCORING / "dataset.jsonl"
IV_OFFICE = re.compile(r"IV-Stelle|Office de l'assurance-invalidité|office AI|Ufficio AI|IVSTA", re.I)
SUVA = re.compile(r"\bSuva\b|Schweizerische Unfallversicherungsanstalt|Caisse nationale suisse|\bCNA\b|\bINSAI\b")


def pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else f"{part}/0"


def hard_cases(records: list[dict], ruleset) -> list[dict]:
    """Accident or invalidity insurance subject, decided by 8C/9C."""
    topic = [ruleset.by_id("accident_insurance_subject"), ruleset.by_id("invalidity_insurance_subject")]
    chamber = ruleset.by_id("social_insurance_chamber")
    return [r for r in records if chamber.matches(r) and any(rule.matches(r) for rule in topic)]


def section_1_to_4(ruleset) -> None:
    every = load_dataset(DATASET, "all")
    train = [r for r in every if r["split"] == "train"]
    test = [r for r in every if r["split"] == "test"]
    picks = sum(r["relevant"] for r in every)
    print("== 1. problem")
    print(f"judgements in dev window: {len(every)}; picks {picks} (train {sum(r['relevant'] for r in train)}, "
          f"test {sum(r['relevant'] for r in test)}); share {100 * picks / len(every):.1f} %, 1 in {len(every) / picks:.0f}")
    hard = hard_cases(train, ruleset)
    rel = [r for r in hard if r["relevant"]]
    non = [r for r in hard if not r["relevant"]]
    print(f"hard cases (accident/invalidity subject, 8C/9C, train): {len(hard)}, picks {pct(len(rel), len(hard))}")

    print("== 2. topic rules (current rules.yaml; significance weights are 0)")
    scored = ranked((r, score_record(r, ruleset)) for r in train)
    for name, lo, hi in (("score >= 0", 0, None), ("-29 <= score < 0", -29.4444, 0), ("score < -29", None, -29.4444)):
        inside = [r for r, s in scored if (lo is None or s.display_score >= lo) and (hi is None or s.display_score < hi)]
        print(f"  {name:<18} judgements {len(inside):>5}  picks {sum(r['relevant'] for r in inside)}")
    print(f"distinct point values in train: {len({s.rule_points for _, s in scored})}")
    rank = {r["key"].split("@")[0]: i for i, (r, _) in enumerate(scored, 1)}
    for docket in ("8C_151/2025", "8C_384/2025"):
        print(f"rank of {docket} (pessimistic ties): {rank.get(docket)}")
    rel_points = [s.display_score for r, s in scored if r["relevant"]]
    print(f"lowest score (base + rule points) of a relevant training judgement: {min(rel_points):g} "
          f"(rule points {min(rel_points) - ruleset.base:g})")
    lowest = min(rel_points)
    print(f"training judgements with a score of exactly {lowest:g}: {sum(s.display_score == lowest for _, s in scored)}")

    print("== 3. signals on the hard cases: picks vs non-picks")
    checks = {
        "insurer appealed (rule insurer_appeals)": lambda r: ruleset.by_id("insurer_appeals").matches(r),
        "Suva itself appealed": lambda r: bool(SUVA.search(r.get("appellant") or "")),
        "appeal upheld fully or partly": lambda r: bool({"upheld", "partly_upheld"} & set(r.get("outcome") or [])),
        "sent back": lambda r: "sent_back" in (r.get("outcome") or []),
        "reasoning >= 4 pages": lambda r: (r.get("reasoning_pages") or 0) >= 4,
        "clarifies case law (rule)": lambda r: ruleset.by_id("clarifies_case_law").matches(r),
        "not entered (outcome)": lambda r: "not_entered" in (r.get("outcome") or []),
        "IV office appealed": lambda r: bool(IV_OFFICE.search(r.get("appellant") or "")),
        "cites BGE vol. 150/151": lambda r: any(re.match(r"BGE 15[01] ", b) for b in r.get("bge") or []),
        "has a BGE regeste (published leading decision)": lambda r: bool(r.get("regeste")),
    }
    for name, test_fn in checks.items():
        print(f"  {name:<48} picks {pct(sum(map(test_fn, rel)), len(rel)):<14} "
              f"non-picks {pct(sum(map(test_fn, non)), len(non))}")

    print("== tried and removed: citation counts")
    with_66 = sum("Art. 66 Abs. 1 BGG" in (r.get("articles") or []) for r in every)
    print(f"judgements citing Art. 66 Abs. 1 BGG: {pct(with_66, len(every))}")
    return train, hard


def section_5_granularity(train: list[dict], ruleset) -> None:
    """Distinct rule-point values with weights fitted once on all of train (topic rules vs + significance)."""
    from suva.scoring.compare import configurations, subset
    from suva.scoring.fit import apply_fitted, fit_weights

    for config in configurations(ruleset):
        part = subset(ruleset, config)
        fitted = apply_fitted(part, fit_weights(train, part))
        print(f"  {config.name}: distinct point values, one fit on all of train: "
              f"{len({score_record(r, fitted).rule_points for r in train})}")


def section_5_and_6() -> None:
    print("== 5. compare (comparison.json)")
    data = json.loads((SCORING / "comparison.json").read_text(encoding="utf-8"))
    for r in data["results"]:
        rk = r["ranking"]
        print(f"  {r['name']:<36} AP {rk['average_precision']:.2f}  top10 {round(rk['precision_at']['10'] * 10)}  "
              f"top20 {round(rk['precision_at']['20'] * 20)}  read90 {rk['depth_for_recall']['0.9']}  "
              f"AUC {rk['auc']:.3f}")
    print("== 6. judge (judge_train.jsonl)")
    rows = [json.loads(line) for line in (SCORING / "judge_train.jsonl").read_text(encoding="utf-8").splitlines() if line]
    high = [r for r in rows if r.get("relevance") == "high"]
    print(f"  verdicts {len(rows)}, high {len(high)}, high but not picked {sum(not r['relevant_label'] for r in high)}")


def section_5_cv(train: list[dict], ruleset) -> None:
    from suva.scoring.compare import configurations, cross_validated_scores, subset

    configs = configurations(ruleset)
    first, last = configs[0], configs[-1]
    ranks = {}
    for config in (first, last):
        scored = cross_validated_scores(train, subset(ruleset, config))
        print(f"  {config.name}: distinct cross-validated point values {len({s.rule_points for _, s in scored})}")
        ranks[config.name] = {r["key"].split("@")[0]: i for i, (r, _) in enumerate(ranked(scored), 1)}
    for docket in ("8C_384/2025", "8C_151/2025", "8C_314/2025", "9C_65/2025", "8C_351/2025"):
        print(f"  {docket}: rank {ranks[first.name].get(docket)} -> {ranks[last.name].get(docket)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cv", action="store_true", help="also recompute the cross-validated runs (slow)")
    args = p.parse_args()
    ruleset = load_rules()
    train, _ = section_1_to_4(ruleset)
    section_5_and_6()
    print("== 5. granularity")
    section_5_granularity(load_dataset(DATASET, "train"), ruleset)
    if args.cv:
        print("== 5. cross-validated distinct values and case ranks (topic rules -> + significance rules)")
        section_5_cv(load_dataset(DATASET, "train"), replace(ruleset))


if __name__ == "__main__":
    main()
