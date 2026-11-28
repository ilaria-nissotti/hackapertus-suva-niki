"""Retrain the rule weights with the reviewers' verdicts, and show the expert what would change.

The feedback loop of the review app:

    reviewer verdicts (Relevant / Not relevant)  ─┐
    Suva's spreadsheet picks (train split)        ├─► fit_weights ─► proposal ─► expert accepts ─► rules.yaml
                                                  ┘                    │
                                     old vs new weights, agreement with the verdicts, recall of the picks,
                                     shortlist changes in the current window

Labels: a judgement's verdict wins over the spreadsheet label. Verdicts on judgements of the frozen test
split (by docket) are left out of training, so `evaluate --split test` stays an honest number. Verdicts on
judgements outside the dev window become extra training records.

Nothing is applied automatically: `build_proposal` only computes; `write_weights` changes rules.yaml when
the expert accepts. This module does not read the review app's database: the app passes the verdicts in.
"""

from __future__ import annotations

import math
import os
import random
import re
import tempfile
from dataclasses import dataclass, field, fields, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable

import yaml

from suva.scoring.dataset import build_dataset, features_record, load_dataset
from suva.scoring.derive import with_derived_fields
from suva.scoring.evaluate import at_n
from suva.scoring.fit import FittedWeights, apply_fitted, fit_weights
from suva.scoring.rules import Rule, RuleSet, parse_rules
from suva.scoring.score import score_record
from suva.scoring.store import reading_list_bands, rules_hash
from suva.storage.base import Repository
from suva.windows import Window

VERDICT_LABELS = {"Relevant": True, "Not relevant": False}  # the reviewer's answer -> training label
FOLDS = 5  # verdict agreement of the new weights is measured with grouped cross-validation over the verdicts
MAX_CASES = 12  # shortlist changes listed per direction
FEW_VERDICTS = 10  # below this, warn that the verdicts can barely move the weights


# --- training data ---------------------------------------------------------------------------------------

def load_or_build_dataset(repo: Repository, path: Path) -> list[dict[str, Any]]:
    """The labelled dev-window dataset (all splits); built from the knowledge base first if missing."""
    if not path.exists():
        build_dataset(repo, path)
    return load_dataset(path)


def verdict_record(repo: Repository, key: str) -> dict[str, Any] | None:
    """A judgement outside the dataset as a training record (same fields the rules look at)."""
    row = repo.get_judgement(key)
    if row is None:
        return None
    return with_derived_fields(features_record(repo, row))


def training_set(dataset: list[dict[str, Any]], verdicts: dict[str, bool],
                 lookup: Callable[[str], dict[str, Any] | None]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Train-split records with the verdicts merged in, and what happened to each verdict.

    Every returned record has `relevant` (the label used), `spreadsheet` (the spreadsheet label, None for a
    judgement outside the dataset) and `verdict` (the reviewer's label, None without one).
    """
    test_dockets = {r["docket"] for r in dataset if r["split"] == "test"}
    by_key = {r["key"]: r for r in dataset}
    records = []
    for r in dataset:
        if r["split"] != "train":
            continue
        verdict = verdicts.get(r["key"])
        records.append(r | {"spreadsheet": bool(r["relevant"]), "verdict": verdict,
                            "relevant": bool(r["relevant"]) if verdict is None else verdict})
    report = {"used": 0, "used_relevant": 0, "overrides": 0, "added": 0, "test_split": 0, "missing": 0}
    for key, verdict in sorted(verdicts.items()):
        known = by_key.get(key)
        if known is not None:
            if known["split"] != "train":  # the frozen test split never trains anything
                report["test_split"] += 1
                continue
            report["overrides"] += verdict != bool(known["relevant"])  # merged into its record above
        else:
            rec = lookup(key)
            if rec is None:
                report["missing"] += 1
                continue
            if rec["docket"] in test_dockets:  # another decision of a test docket: keep the docket in one split
                report["test_split"] += 1
                continue
            records.append(rec | {"split": "train", "spreadsheet": None, "verdict": verdict, "relevant": verdict})
            report["added"] += 1
        report["used"] += 1
        report["used_relevant"] += verdict
    return records, report


# --- speed: a proposal refits and rescores the same judgements many times --------------------------------

@dataclass
class CachedRule(Rule):
    """A rule that remembers its match count per judgement key.

    Match counts don't depend on the weights, but every refit and every scoring pass would run the rule's
    patterns over the full judgement texts again. dataclasses.replace (used when weights are applied) keeps
    the same cache, so all weight variants of a rule share it.
    """

    cache: dict[str, int] = field(default_factory=dict, repr=False, compare=False)

    def count(self, record: dict[str, Any]) -> int:
        key = record.get("key")
        if key is None:
            return super().count(record)
        if key not in self.cache:
            self.cache[key] = super().count(record)
        return self.cache[key]


def with_count_cache(ruleset: RuleSet) -> RuleSet:
    rules = [CachedRule(**{f.name: getattr(rule, f.name) for f in fields(Rule) if f.init}) for rule in ruleset.rules]
    return replace(ruleset, rules=rules)


# --- comparison ------------------------------------------------------------------------------------------

def shown(points: float, floor: float | None) -> bool:
    """Would the rules put this judgement forward (at or above the rule-points floor)?"""
    return floor is None or points >= floor


def verdict_folds(records: list[dict[str, Any]], k: int = FOLDS, seed: int = 0) -> dict[str, int]:
    """docket -> fold, for the dockets that carry a verdict (all decisions of a docket in one fold)."""
    dockets = sorted({r["docket"] for r in records if r["verdict"] is not None})
    random.Random(seed).shuffle(dockets)
    k = max(1, min(k, len(dockets)))
    return {docket: i % k for i, docket in enumerate(dockets)}


def held_out_points(records: list[dict[str, Any]], ruleset: RuleSet, l2: float = 1.0) -> dict[str, tuple[float, float]]:
    """key -> (rule points, floor) of each verdict under weights refitted WITHOUT its docket.

    This is how the new weights would treat a case they have not seen, so they can be compared fairly with
    the current weights, which never saw the verdicts either.
    """
    fold_of = verdict_folds(records)
    out = {}
    for fold in sorted(set(fold_of.values())):
        fit_data = [r for r in records if fold_of.get(r["docket"]) != fold]
        active = apply_fitted(ruleset, fit_weights(fit_data, ruleset, l2=l2))
        floor = active.selection.floor_rule_points
        for r in records:
            if r["verdict"] is not None and fold_of.get(r["docket"]) == fold:
                out[r["key"]] = (score_record(r, active).rule_points, floor)
    return out


def change_label(current: float, proposed: float) -> str:
    """The change of one rule in plain words."""
    if proposed == current:
        return "No change"
    if proposed == 0:
        return "No longer counts"
    if current == 0 or (current > 0) != (proposed > 0):
        return "Now counts against" if proposed < 0 else "Now counts in favour"
    change = "Counts more" if abs(proposed) > abs(current) else "Counts less"
    return change + (" against" if proposed < 0 else "")


def weight_rows(ruleset: RuleSet, fitted: FittedWeights) -> list[dict[str, Any]]:
    rows = []
    for rule in ruleset.rules:
        current, proposed = fitted.current[rule.id], fitted.weights[rule.id]
        cap = rule.cap_count if rule.graded and rule.cap_count != math.inf else None
        rows.append({
            "id": rule.id, "explanation": rule.explanation, "description": rule.description, "group": rule.group,
            "graded": rule.graded, "cap_count": cap, "current": current, "proposed": proposed,
            # the most a rule can add or subtract, so graded rules compare with fixed ones
            "current_max": current * cap if cap else current, "proposed_max": proposed * cap if cap else proposed,
            "change": change_label(current, proposed),
        })
    rows.sort(key=lambda row: -abs(row["proposed_max"] - row["current_max"]))
    return rows


def _case(rec: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"key": rec["key"], "docket": rec["docket"], "title": rec.get("subject") or rec["docket"],
            "decision_date": rec.get("decision_date")} | extra


def shortlist_changes(window_records: list[dict[str, Any]], current: RuleSet, proposed: RuleSet) -> dict[str, Any]:
    """Which judgements of the current window enter or leave the shortlist (the reading list) with new weights."""
    before = {rec["key"]: (rec, score_record(rec, current)) for rec in window_records}
    after = {rec["key"]: score_record(rec, proposed) for rec in window_records}
    old_band = reading_list_bands(list(before.values()), current, whole_period=True)
    new_band = reading_list_bands([(rec, after[key]) for key, (rec, _) in before.items()], proposed,
                                  whole_period=True)
    joined = [key for key in before if new_band[key] == "high" != old_band[key]]
    left = [key for key in before if old_band[key] == "high" != new_band[key]]

    def cases(keys):
        keys = sorted(keys, key=lambda k: -after[k].rule_points)
        return [_case(before[k][0], current_points=before[k][1].rule_points, proposed_points=after[k].rule_points)
                for k in keys[:MAX_CASES]]

    return {"judgements": len(window_records), "before": sum(b == "high" for b in old_band.values()),
            "after": sum(b == "high" for b in new_band.values()), "joined": len(joined), "left": len(left),
            "joined_cases": cases(joined), "left_cases": cases(left)}


def picks_recall(records: list[dict[str, Any]], ruleset: RuleSet) -> float:
    """Share of Suva's spreadsheet picks (training months) in the reading list: top N per period above the floor."""
    labelled = [r | {"relevant": r["spreadsheet"]} for r in records if r["spreadsheet"] is not None]
    sel = ruleset.selection
    return at_n([(r, score_record(r, ruleset)) for r in labelled], sel.top_n, sel.floor_rule_points,
                sel.per).expected_recall


def build_proposal(repo: Repository, ruleset: RuleSet, dataset_path: Path, verdicts: dict[str, bool],
                   window: Window, l2: float = 1.0) -> dict[str, Any]:
    """New weights fitted on the spreadsheet picks plus the verdicts, and how they compare with the current ones.

    The result is plain JSON for the review app; `write_weights` applies it.
    """
    digest = rules_hash(ruleset)
    ruleset = with_count_cache(ruleset)
    dataset = load_or_build_dataset(repo, dataset_path)
    records, used = training_set(dataset, verdicts, lambda key: verdict_record(repo, key))
    fitted = fit_weights(records, ruleset, l2=l2)
    proposed = apply_fitted(ruleset, fitted)
    current_floor = ruleset.selection.floor_rule_points

    held_out = held_out_points(records, ruleset, l2) if used["used"] else {}
    cases = []
    for r in records:
        if r["verdict"] is None:
            continue
        now = score_record(r, ruleset).rule_points
        new, new_floor = held_out[r["key"]]
        cases.append(_case(r, verdict=r["verdict"], current_points=now, current_shown=shown(now, current_floor),
                           proposed_points=new, proposed_shown=shown(new, new_floor)))
    cases.sort(key=lambda c: (c["current_shown"] == c["verdict"], c["proposed_shown"] != c["verdict"], c["key"]))

    window_records = [with_derived_fields(features_record(repo, row)) for row in repo.list_judgements(window=window)]
    recall = {"current": picks_recall(records, ruleset), "proposed": picks_recall(records, proposed)}
    agreement = {
        "verdicts": len(cases),
        "current": sum(c["current_shown"] == c["verdict"] for c in cases),
        "proposed": sum(c["proposed_shown"] == c["verdict"] for c in cases),
    }
    warnings = []
    if agreement["proposed"] < agreement["current"]:
        warnings.append("The new weights agree with fewer expert verdicts than the current ones.")
    if recall["proposed"] < recall["current"] - 0.05:
        warnings.append("The new weights put noticeably fewer of Suva's past picks in the reading list.")
    if used["used"] < FEW_VERDICTS:
        warnings.append(f"Only {used['used']} verdicts could be used: the new weights still come mostly from "
                        "Suva's spreadsheet, so expect small changes.")
    return {
        "rules_hash": digest,
        "training": {"judgements": len(records), "relevant": int(fitted.n_rel),
                     "spreadsheet_picks": sum(bool(r["spreadsheet"]) for r in records)},
        "verdicts": used,
        "weights": weight_rows(ruleset, fitted),
        "proposed_weights": fitted.weights,
        "base": {"current": ruleset.base, "proposed": fitted.base},
        "floor": {"current": current_floor, "proposed": fitted.floor_rule_points},
        "agreement": agreement,
        "verdict_cases": cases,
        "picks_recall": recall,
        "shortlist": shortlist_changes(window_records, ruleset, proposed),
        "window": [window.published_from.isoformat() if window.published_from else None, window.end.isoformat()],
        "warnings": warnings,
    }


# --- applying an accepted proposal -----------------------------------------------------------------------

NUMBER = r"-?\d+(?:\.\d+)?"
MARKER = "# Weights last updated in the review app"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def proposed_ruleset(ruleset: RuleSet, weights: dict[str, float], base: float, floor: float) -> RuleSet:
    """The rule set as it will read after write_weights (graded caps kept in matches, like fit.apply_weights)."""
    rules: list[Rule] = []
    for rule in ruleset.rules:
        new = float(weights[rule.id])
        if rule.graded:
            cap = rule.cap_count
            rules.append(replace(rule, per_match=new, max=None if cap == math.inf else float(round(new * cap))))
        else:
            rules.append(replace(rule, weight=new))
    return replace(ruleset, rules=rules, base=float(base),
                   selection=replace(ruleset.selection, floor_rule_points=float(floor)))


def updated_rules_text(text: str, ruleset: RuleSet, weights: dict[str, float], base: float, floor: float,
                       note: str) -> str:
    """rules.yaml with new numbers for the weights, base and floors, every comment and line kept.

    Only the numbers on `weight:`, `per_match:`, `max:`, `base:`, `selection.floor_rule_points` and (when it
    followed the selection floor) `judge.candidate_floor_rule_points` lines change, plus one marker comment
    above `base:`. The result is parsed back and compared with the intended rule set; any difference raises.
    """
    target = proposed_ruleset(ruleset, weights, base, floor)
    old_floor = ruleset.selection.floor_rule_points
    sync_judge = ruleset.judge_floor_rule_points is not None and ruleset.judge_floor_rule_points == old_floor
    values = {r.id: {"weight": r.weight, "per_match": r.per_match, "max": r.max} for r in target.rules}
    newline = "\r\n" if "\r\n" in text else "\n"
    out, section, rule_id, seen = [], None, None, set()
    for line in text.splitlines():
        top = re.match(r"^([A-Za-z_]\w*):", line)
        if top:
            section, rule_id = top.group(1), None
        if line.startswith(MARKER):
            continue  # replaced below, above base:
        if section == "rules" and (m := re.match(r"^\s*-\s+id:\s*['\"]?([\w-]+)", line)):
            rule_id = m.group(1)
        if section == "base" and (m := re.match(rf"^base:(\s*){NUMBER}(.*)$", line)):
            out.append(f"{MARKER} ({note}).")
            line = f"base:{m.group(1)}{_number(target.base)}{m.group(2)}"
            seen.add("base")
        elif section == "selection" and (m := re.match(rf"^(\s+floor_rule_points:\s*)(?:{NUMBER}|null|~)?(.*)$", line)):
            line = f"{m.group(1)}{_number(floor)}{m.group(2)}"
            seen.add("floor")
        elif section == "judge" and sync_judge and (
                m := re.match(rf"^(\s+candidate_floor_rule_points:\s*){NUMBER}(.*)$", line)):
            line = f"{m.group(1)}{_number(floor)}{m.group(2)}"
        elif section == "rules" and rule_id in values and (
                m := re.match(rf"^(\s+)(weight|per_match|max):(\s*){NUMBER}(.*)$", line)):
            value = values[rule_id][m.group(2)]
            line = f"{m.group(1)}{m.group(2)}:{m.group(3)}{_number(value)}{m.group(4)}"
            seen.add((rule_id, m.group(2)))
        out.append(line)
    if not {"base", "floor"} <= seen:
        raise ValueError("rules.yaml needs a numeric `base:` and a `selection: floor_rule_points:` line to update")
    new_text = newline.join(out) + (newline if text.endswith(("\n", "\r\n")) else "")
    check = parse_rules(yaml.safe_load(new_text))
    if rules_hash(check) != rules_hash(target):
        raise ValueError("rules.yaml could not be updated safely (unexpected layout); copy the weights by hand")
    return new_text


def write_weights(path: Path, ruleset: RuleSet, weights: dict[str, float], base: float, floor: float,
                  verdicts: int) -> None:
    """Write accepted weights into rules.yaml in place (comments kept), atomically."""
    with path.open(encoding="utf-8", newline="") as f:
        text = f.read()
    note = f"{date.today().isoformat()}, from {verdicts} expert verdicts and Suva's spreadsheet picks"
    new_text = updated_rules_text(text, ruleset, weights, base, floor, note)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".rules-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
