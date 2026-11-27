"""Score a split with the rules and measure how well the scores match the Excel labels.

Besides precision/recall at the threshold, the score is judged as a ranking, the way
lawyers use it: how many of the top 10/20 are relevant, and how far down the sorted
list one has to read to find 90/95/100% of the relevant judgements.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from suva.scoring.rules import RuleSet
from suva.scoring.score import BANDS, ScoreResult, score_record

SWEEP = (5, 10, 20, 30, 40, 50, 60, 70, 80, 90)
TOP_K = (10, 20, 50, 100)
RECALL_DEPTHS = (0.9, 0.95, 1.0)

Verdicts = dict[str, tuple[str, str]]  # key -> (judge level, reason)


@dataclass
class Metrics:
    threshold: float
    flagged: int
    true_pos: int
    false_pos: int
    false_neg: int
    precision: float
    recall: float


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
    precision_at: dict[int, float]  # top k -> share relevant
    depth_for_recall: dict[float, int | None]  # recall target -> judgements to read
    average_precision: float
    auc: float


@dataclass
class Evaluation:
    split: str
    n: int
    n_rel: int
    at_threshold: Metrics
    sweep: list[Metrics]
    ranking: Ranking
    bands: dict[str, list[int]]  # band -> [relevant, not relevant]
    calibration: list[dict[str, Any]]  # per score bin: n, relevant, mean score, actual rate
    evidence: list[RuleEvidence]
    judged: int = 0  # records that got judge points
    misses: list[dict[str, Any]] = field(default_factory=list)  # relevant, scored below threshold
    false_alarms: list[dict[str, Any]] = field(default_factory=list)  # not relevant, scored at/above threshold

    def as_dict(self) -> dict:
        d = asdict(self)
        for e, ev in zip(d["evidence"], self.evidence):
            e["text"] = ev.text
        return d


def metrics(scored: list[tuple[dict, ScoreResult]], threshold: float) -> Metrics:
    tp = sum(1 for rec, s in scored if s.score >= threshold and rec["relevant"])
    fp = sum(1 for rec, s in scored if s.score >= threshold and not rec["relevant"])
    fn = sum(1 for rec, s in scored if s.score < threshold and rec["relevant"])
    return Metrics(threshold, tp + fp, tp, fp, fn,
                   precision=tp / (tp + fp) if tp + fp else 0.0, recall=tp / (tp + fn) if tp + fn else 0.0)


def ranked(scored: list[tuple[dict, ScoreResult]]) -> list[tuple[dict, ScoreResult]]:
    """Highest score first (points break ties where the 0-100 score saturates); remaining ties are
    broken pessimistically (not relevant first), so ties never flatter."""
    return sorted(scored, key=lambda rs: (-rs[1].score, -rs[1].points, rs[0]["relevant"], rs[0]["key"]))


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
    # AUC: chance that a random relevant judgement scores above a random not-relevant one (ties count half)
    rel_scores = sorted(s.score for rec, s in scored if rec["relevant"])
    non_scores = sorted(s.score for rec, s in scored if not rec["relevant"])
    wins, j_lo, j_hi = 0.0, 0, 0
    for x in rel_scores:
        while j_lo < len(non_scores) and non_scores[j_lo] < x:
            j_lo += 1
        while j_hi < len(non_scores) and non_scores[j_hi] <= x:
            j_hi += 1
        wins += j_lo + 0.5 * (j_hi - j_lo)
    return Ranking(
        precision_at={k: sum(order[:k]) / min(k, len(order)) for k in TOP_K if order},
        depth_for_recall={t: depth.get(t) for t in RECALL_DEPTHS},
        average_precision=ap / n_rel if n_rel else 0.0,
        auc=wins / (len(rel_scores) * len(non_scores)) if rel_scores and non_scores else 0.0,
    )


def calibration(scored: list[tuple[dict, ScoreResult]], width: int = 10) -> list[dict[str, Any]]:
    bins: dict[int, list[tuple[dict, ScoreResult]]] = {}
    for rec, s in scored:
        bins.setdefault(min(int(s.score // width), 100 // width - 1), []).append((rec, s))
    out = []
    for b in sorted(bins):
        rows = bins[b]
        n_rel = sum(rec["relevant"] for rec, _ in rows)
        out.append({"bin": f"{b * width}-{(b + 1) * width}", "n": len(rows), "relevant": n_rel,
                    "mean_score": round(sum(s.score for _, s in rows) / len(rows), 1),
                    "actual_rate": round(100 * n_rel / len(rows), 1)})
    return out


def _case(rec: dict, s: ScoreResult) -> dict[str, Any]:
    return {"key": rec["key"], "score": round(s.score, 1), "chamber": rec.get("chamber"),
            "subject": (rec.get("subject") or "")[:160], "parties": (rec.get("parties") or "")[:160],
            "fired": [f.id for f in s.fired]}


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
    bands = {b: [0, 0] for b in BANDS}
    for rec, s in scored:
        bands[s.band][0 if rec["relevant"] else 1] += 1
    t = ruleset.threshold
    misses = sorted((_case(r, s) for r, s in scored if r["relevant"] and s.score < t), key=lambda c: c["score"])
    alarms = sorted((_case(r, s) for r, s in scored if not r["relevant"] and s.score >= t), key=lambda c: -c["score"])
    judged = sum(1 for _, s in scored if any(f.id == "llm_judge" for f in s.fired))
    return Evaluation(split, len(records), n_rel, metrics(scored, t), [metrics(scored, x) for x in SWEEP],
                      ranking(scored), bands, calibration(scored), evidence, judged, misses[:n_cases], alarms[:n_cases])


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
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ranked(score_all(records, ruleset, verdicts))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "key", "split", "relevant_label", "score", "band", "points", "flagged",
                    "fired_rules", "explanation", "subject"])
        for i, (rec, s) in enumerate(rows, 1):
            w.writerow([i, rec["key"], rec["split"], int(rec["relevant"]), f"{s.score:.3g}", s.band, f"{s.points:g}",
                        int(s.relevant), ";".join(f.id for f in s.fired), s.explanation,
                        (rec.get("subject") or "")[:200]])
