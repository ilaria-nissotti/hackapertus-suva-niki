"""Score a split with the rules and measure how well the reading list matches the Excel labels.

The main numbers follow how lawyers use the output (see select.py): of the top N judgements
per period (above the floor), how many are relevant (precision at N), and how many of all
relevant judgements they contain (recall at N). A sweep over N shows the reading-budget
trade-off. As secondary metrics the whole ranking is judged too: average precision, AUC and
how far down the sorted list one has to read to find 90/95/100% of the relevant judgements.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from suva.scoring.rules import RuleSet
from suva.scoring.score import ScoreResult, score_record
from suva.scoring.select import Verdicts, ranked, select_scored

TOP_K = (10, 20, 50, 100)
RECALL_DEPTHS = (0.9, 0.95, 1.0)
BUDGET_FACTORS = (1 / 3, 2 / 3, 1, 4 / 3, 5 / 3)  # the N sweep, as multiples of selection.top_n


@dataclass
class AtN:
    """Precision and recall of the selection: top N per period, none below the floor."""

    per: str
    top_n: int | None
    floor_rule_points: float | None
    periods: int
    shown: int  # judgements selected over all periods
    true_pos: int
    n_rel: int
    precision: float  # share of the shown judgements that are relevant (equal points: not relevant first)
    recall: float  # share of all relevant judgements that are shown (equal points: not relevant first)
    expected_precision: float = 0.0  # the same with equal points in random order (as production sees them)
    expected_recall: float = 0.0
    per_period: list[dict[str, Any]] = field(default_factory=list)  # period, n, relevant, shown, found


@dataclass
class RuleEvidence:
    id: str
    weight: float
    fires_rel: int
    fires_non: int
    n_rel: int
    n_non: int

    @property
    def text(self) -> str:
        return f"fires in {self.fires_rel}/{self.n_rel} relevant, {self.fires_non}/{self.n_non} not relevant"


@dataclass
class Ranking:
    precision_at: dict[int, float]  # top k of the whole list -> share relevant
    depth_for_recall: dict[float, int | None]  # recall target -> judgements to read
    average_precision: float
    auc: float


@dataclass
class Evaluation:
    split: str
    n: int
    n_rel: int
    selection: AtN  # the configured top_n / floor_rule_points / per
    budget: list[AtN]  # the same selection for other values of N
    ranking: Ranking
    evidence: list[RuleEvidence]
    judged: int = 0  # records that got judge points
    misses: list[dict[str, Any]] = field(default_factory=list)  # relevant, not selected
    false_alarms: list[dict[str, Any]] = field(default_factory=list)  # not relevant, selected

    def as_dict(self) -> dict:
        d = asdict(self)
        for e, ev in zip(d["evidence"], self.evidence):
            e["text"] = ev.text
        return d


def expected_hits(scored: list[tuple[dict, ScoreResult]], n: int | None,
                  floor_rule_points: float | None) -> tuple[float, float]:
    """Expected (relevant, shown) in the top n above the floor, with equal points in random order.

    If the cut at n falls inside a group of g judgements with equal points, r of them relevant,
    and m of them still fit, the expected number of relevant ones taken from it is m x r / g.
    """
    groups: dict[float, list[int]] = {}
    for rec, s in scored:
        if floor_rule_points is None or s.rule_points >= floor_rule_points:
            group = groups.setdefault(s.rule_points, [0, 0])
            group[0] += 1
            group[1] += bool(rec["relevant"])
    left = n if n is not None else len(scored)
    hits = taken = 0.0
    for points in sorted(groups, reverse=True):
        if left <= 0:
            break
        size, rel = groups[points]
        m = min(size, left)
        hits += m * rel / size
        taken += m
        left -= m
    return hits, taken


def at_n(scored: list[tuple[dict, ScoreResult]], n: int | None, floor_rule_points: float | None,
         per: str = "period") -> AtN:
    """Recall and precision at N, using the same selection as the reading list (ties: see select.py)."""
    chosen = select_scored(scored, n, floor_rule_points, per)
    periods: dict[str, dict[str, Any]] = {}
    by_period: dict[str, list[tuple[dict, ScoreResult]]] = {}
    for item in select_scored(scored, None, None, per):  # every record with its period
        row = periods.setdefault(item.period, {"period": item.period, "n": 0, "relevant": 0, "shown": 0, "found": 0})
        row["n"] += 1
        row["relevant"] += bool(item.record["relevant"])
        by_period.setdefault(item.period, []).append((item.record, item.result))
    for item in chosen:
        periods[item.period]["shown"] += 1
        periods[item.period]["found"] += bool(item.record["relevant"])
    tp = sum(bool(item.record["relevant"]) for item in chosen)
    n_rel = sum(bool(rec["relevant"]) for rec, _ in scored)
    expected = [expected_hits(items, n, floor_rule_points) for items in by_period.values()]
    e_hits, e_shown = sum(h for h, _ in expected), sum(t for _, t in expected)
    return AtN(per, n, floor_rule_points, len(periods), len(chosen), tp, n_rel,
               precision=tp / len(chosen) if chosen else 0.0, recall=tp / n_rel if n_rel else 0.0,
               expected_precision=e_hits / e_shown if e_shown else 0.0,
               expected_recall=e_hits / n_rel if n_rel else 0.0,
               per_period=[periods[k] for k in sorted(periods)])


def budget_sizes(top_n: int | None) -> list[int]:
    """The N values of the reading-budget sweep around the configured top_n (10..50 for 30)."""
    if not top_n:
        return []
    return sorted({max(1, round(top_n * f)) for f in BUDGET_FACTORS})


def ranking(scored: list[tuple[dict, ScoreResult]]) -> Ranking:
    order = [rec["relevant"] for rec, _ in ranked(scored)]
    n_rel = sum(order)
    hits, ap, depth = 0, 0.0, {}
    for i, rel in enumerate(order, 1):
        if rel:
            hits += 1
            ap += hits / i
            for target in RECALL_DEPTHS:
                if target not in depth and n_rel and hits >= target * n_rel - 1e-9:
                    depth[target] = i
    # AUC: chance that a random relevant judgement has more rule points than a random not-relevant one (ties half)
    rel_points = sorted(s.rule_points for rec, s in scored if rec["relevant"])
    non_points = sorted(s.rule_points for rec, s in scored if not rec["relevant"])
    wins, j_lo, j_hi = 0.0, 0, 0
    for x in rel_points:
        while j_lo < len(non_points) and non_points[j_lo] < x:
            j_lo += 1
        while j_hi < len(non_points) and non_points[j_hi] <= x:
            j_hi += 1
        wins += j_lo + 0.5 * (j_hi - j_lo)
    return Ranking(
        precision_at={k: sum(order[:k]) / min(k, len(order)) for k in TOP_K if order},
        depth_for_recall={t: depth.get(t) for t in RECALL_DEPTHS},
        average_precision=ap / n_rel if n_rel else 0.0,
        auc=wins / (len(rel_points) * len(non_points)) if rel_points and non_points else 0.0,
    )


def _case(rec: dict, s: ScoreResult, period: str = "", rank: int | None = None) -> dict[str, Any]:
    return {"key": rec["key"], "score": round(s.display_score, 1), "rule_points": round(s.rule_points, 1),
            "period": period, "period_rank": rank,
            "chamber": rec.get("chamber"), "subject": (rec.get("subject") or "")[:160],
            "parties": (rec.get("parties") or "")[:160], "fired": [f.id for f in s.fired]}


def score_all(records: list[dict[str, Any]], ruleset: RuleSet,
              verdicts: Verdicts | None = None) -> list[tuple[dict, ScoreResult]]:
    verdicts = verdicts or {}
    return [(rec, score_record(rec, ruleset, *verdicts.get(rec["key"], (None, "")))) for rec in records]


def evaluate(records: list[dict[str, Any]], ruleset: RuleSet, split: str, n_cases: int = 15,
             verdicts: Verdicts | None = None) -> Evaluation:
    scored = score_all(records, ruleset, verdicts)
    n_rel = sum(rec["relevant"] for rec in records)
    n_non = len(records) - n_rel
    evidence = []
    for rule in ruleset.rules:
        fires = [(rec["relevant"], rule.id in {f.id for f in s.fired}) for rec, s in scored]
        evidence.append(RuleEvidence(rule.id, rule.per_match if rule.graded else rule.weight,
                                     sum(1 for rel, hit in fires if rel and hit),
                                     sum(1 for rel, hit in fires if not rel and hit), n_rel, n_non))
    sel = ruleset.selection
    chosen = {item.key for item in select_scored(scored, sel.top_n, sel.floor_rule_points, sel.per)}
    place = {item.key: item for item in select_scored(scored, None, None, sel.per)}  # rank within the period

    def case(rec: dict, s: ScoreResult) -> dict[str, Any]:
        return _case(rec, s, place[rec["key"]].period, place[rec["key"]].rank)

    misses = sorted((case(r, s) for r, s in scored if r["relevant"] and r["key"] not in chosen),
                    key=lambda c: -c["rule_points"])
    alarms = sorted((case(r, s) for r, s in scored if not r["relevant"] and r["key"] in chosen),
                    key=lambda c: -c["rule_points"])
    judged = sum(1 for _, s in scored if any(f.id == "llm_judge" for f in s.fired))
    return Evaluation(split, len(records), n_rel, at_n(scored, sel.top_n, sel.floor_rule_points, sel.per),
                      [at_n(scored, k, sel.floor_rule_points, sel.per) for k in budget_sizes(sel.top_n)],
                      ranking(scored), evidence, judged,
                      misses[:n_cases], alarms[:n_cases])


def load_verdicts(path: Path) -> Verdicts:
    """Judge verdicts written by `python -m suva judge` (unusable answers are skipped)."""
    out: Verdicts = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                v = json.loads(line)
                if v.get("relevance"):
                    out[v["key"]] = (v["relevance"], v.get("reason") or "")
    return out


def write_scores(records: list[dict[str, Any]], ruleset: RuleSet, path: Path,
                 verdicts: Verdicts | None = None) -> None:
    """All judgements, most rule points first, with their rank in their period and whether they are selected.

    `score` is the display score (base + rule points); ranking and selection use `rule_points`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    scored = score_all(records, ruleset, verdicts)
    sel = ruleset.selection
    chosen = {item.key for item in select_scored(scored, sel.top_n, sel.floor_rule_points, sel.per)}
    place = {item.key: item for item in select_scored(scored, None, None, sel.per)}
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "key", "split", "relevant_label", "score", "rule_points", "period", "period_rank",
                    "selected",
                    "fired_rules", "explanation", "subject"])
        for i, (rec, s) in enumerate(ranked(scored), 1):
            item = place[rec["key"]]
            w.writerow([i, rec["key"], rec["split"], int(rec["relevant"]), f"{s.display_score:g}",
                        f"{s.rule_points:g}", item.period, item.rank,
                        int(rec["key"] in chosen), ";".join(f.id for f in s.fired), s.explanation,
                        (rec.get("subject") or "")[:200]])
