"""Load, validate and match the relevance rules in rules/rules.yaml.

A rule looks in one field of a judgement record and fires when its terms match:

    - id: suva_party
      field: parties
      match: {any: ["Suva", "CNA", "INSAI"]}
      weight: 20
      explanation: "Suva is directly involved as a party."

A graded rule counts matches instead (points per match, capped):

    - id: uvg_articles
      field: articles
      match: {regex: "UVG$"}
      per_match: 3
      max: 15

Text fields match terms as whole words, case-insensitive by default; `none: [...]` stops
a rule from firing when one of those terms is present.
List fields (laws, articles, bge, chamber, language, outcome) match exact values.
Number fields (reasoning_pages) fire from `match: {min: N}`; graded, they count the number.
The fields appellant, outcome and reasoning_pages are derived in derive.py.

Selection turns rule points (without base) into the list lawyers read: the top N per period,
never below a floor:

    selection:
      top_n: 30              # judgements shown per period
      floor_rule_points: 66  # none shown below this many rule points, even if the period has fewer than N
      per: month             # month | week | period (the whole input as one list)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from suva.config import REPO_ROOT

DEFAULT_RULES_PATH = REPO_ROOT / "rules" / "rules.yaml"

TEXT_FIELDS = ("parties", "appellant", "subject", "header", "regeste", "facts", "considerations", "dispositiv",
               "text")
LIST_FIELDS = ("laws", "articles", "bge", "chamber", "language", "outcome")
NUMBER_FIELDS = ("reasoning_pages",)
FIELDS = TEXT_FIELDS + LIST_FIELDS + NUMBER_FIELDS
MATCH_KINDS = ("any", "all", "regex", "none", "min")
SOURCES = ("expert", "data", "apertus", "hypothesis")
GROUPS = ("topic", "significance")  # what a rule describes; `compare` adds groups one by one
JUDGE_LEVELS = ("high", "medium", "low")
PERIODS = ("month", "week", "period")  # selection.per: how judgements are grouped before taking the top N
OBSOLETE_KEYS = {  # keys of the old 0-100 score; rejected so a stale rules file does not silently change meaning
    "threshold": "replaced by selection.top_n and selection.floor_rule_points",
    "bands": "removed: judgements are ranked by points, see selection",
    "scale": "removed: points are no longer turned into a 0-100 score",
    "similarity": "similarity to past picks (citations, embeddings) was removed; see docs/pitch-stats.md",
}


class RulesError(ValueError):
    """rules.yaml is malformed; the message names the rule and the problem."""


@dataclass
class Rule:
    id: str
    field: str
    weight: float
    explanation: str
    description: str = ""
    any: list[str] = field(default_factory=list)
    all: list[str] = field(default_factory=list)
    regex: str | None = None
    none: list[str] = field(default_factory=list)  # the rule does not fire if any of these is found
    min: float | None = None  # number fields: fires from this value on
    case_sensitive: bool = False
    group: str = "topic"
    source: str = "hypothesis"
    evidence: str = ""
    per_match: float | None = None  # graded rule: points per match instead of a fixed weight
    max: float | None = None  # graded rule: cap on the points (same sign as per_match)
    _patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    _all_patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    _none_patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    _regex: re.Pattern | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        word = lambda term: re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags)  # noqa: E731
        if self.field in TEXT_FIELDS:
            self._patterns = [word(t) for t in self.any]
            self._all_patterns = [word(t) for t in self.all]
            self._none_patterns = [word(t) for t in self.none]
        if self.regex:
            self._regex = re.compile(self.regex, flags)

    @property
    def graded(self) -> bool:
        return self.per_match is not None

    @property
    def cap_count(self) -> float:
        """Matches after which a graded rule stops adding points."""
        if not self.graded or self.max is None or not self.per_match:
            return float("inf")
        return self.max / self.per_match

    def count(self, record: dict[str, Any]) -> int:
        """How many times the rule matches (0 = does not fire)."""
        value = record.get(self.field)
        if value is None:
            return 0
        if self.field in NUMBER_FIELDS:  # the count is the number itself, e.g. pages of reasoning
            number = int(value)
            return number if number >= (self.min if self.min is not None else 1) else 0
        if self.field in LIST_FIELDS:
            values = value if isinstance(value, (list, tuple, set)) else [value]
            norm = (lambda s: str(s)) if self.case_sensitive else (lambda s: str(s).casefold())
            have = {norm(v) for v in values}
            if have & {norm(t) for t in self.none}:
                return 0
            hits = len(have & {norm(t) for t in self.any}) if self.any else None
            if hits == 0 or (self.all and not {norm(t) for t in self.all} <= have):
                return 0
            regex_hits = sum(1 for v in values if self._regex.search(str(v))) if self._regex else None
            if regex_hits == 0:
                return 0
            return hits if hits is not None else regex_hits if regex_hits is not None else len(self.all)
        text = str(value)
        if any(p.search(text) for p in self._none_patterns):
            return 0
        hits = sum(len(p.findall(text)) for p in self._patterns) if self._patterns else None
        if hits == 0 or (self._all_patterns and not all(p.search(text) for p in self._all_patterns)):
            return 0
        regex_hits = len(self._regex.findall(text)) if self._regex else None
        if regex_hits == 0:
            return 0
        if hits is not None:
            return hits
        if regex_hits is not None:
            return regex_hits
        return sum(len(p.findall(text)) for p in self._all_patterns)

    def matches(self, record: dict[str, Any]) -> bool:
        return self.count(record) > 0

    def points_for(self, count: int) -> float:
        if count <= 0:
            return 0.0
        if not self.graded:
            return self.weight
        return self.per_match * min(count, self.cap_count)

    def feature(self, record: dict[str, Any]) -> float:
        """Input for fitting: 1/0 for a normal rule, capped match count for a graded rule."""
        count = self.count(record)
        return float(min(count, self.cap_count)) if self.graded else float(count > 0)


@dataclass
class Selection:
    """Which judgements are shown: the top_n by rule points per period, none below floor_rule_points."""

    top_n: int | None = 30  # None: no limit, only the floor applies
    floor_rule_points: float | None = None  # in rule points, without base; None: no floor
    per: str = "month"  # month | week | period


@dataclass
class RuleSet:
    rules: list[Rule]
    base: float = 0.0  # added to the rule points for display only; never used to rank, select or gate
    selection: Selection = field(default_factory=Selection)
    judge: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def by_id(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)

    @property
    def feature_ids(self) -> list[str]:
        """Inputs of the score that fit-weights can weigh: one per rule."""
        return [r.id for r in self.rules]

    @property
    def judge_points(self) -> dict[str, float]:
        return {k: float(v) for k, v in (self.judge.get("points") or {}).items()}

    @property
    def judge_floor_rule_points(self) -> float | None:
        """Judgements with at least this many rule points (no base) are sent to the judge (None: all)."""
        value = self.judge.get("candidate_floor_rule_points")
        return None if value is None else float(value)


def _rule_from_dict(raw: dict[str, Any], index: int) -> Rule:
    where = f"rule #{index + 1}" + (f" ({raw['id']!r})" if isinstance(raw, dict) and raw.get("id") else "")
    if not isinstance(raw, dict):
        raise RulesError(f"{where}: must be a mapping with id, field, match, weight, explanation")
    for key in ("id", "field", "match", "explanation"):
        if key not in raw:
            raise RulesError(f"{where}: missing '{key}'")
    graded = "per_match" in raw
    if graded == ("weight" in raw):
        raise RulesError(f"{where}: give either 'weight' (fixed points) or 'per_match' (points per match)")
    if "max" in raw and not graded:
        raise RulesError(f"{where}: 'max' only applies to rules with 'per_match'")
    if raw["field"] not in FIELDS:
        raise RulesError(f"{where}: field must be one of {', '.join(FIELDS)}; got {raw['field']!r}")
    match = raw["match"]
    if not isinstance(match, dict) or not match or set(match) - set(MATCH_KINDS):
        raise RulesError(f"{where}: match needs at least one of {', '.join(MATCH_KINDS)}")
    if "regex" in match and not isinstance(match["regex"], str):
        raise RulesError(f"{where}: match.regex must be a string")
    if "any" in match and "regex" in match:
        raise RulesError(f"{where}: use either match.any or match.regex, not both (only 'any' would be counted)")
    for kind in ("any", "all", "none"):
        terms = match.get(kind, [])
        if not isinstance(terms, list) or not all(isinstance(t, (str, int)) and str(t).strip() for t in terms):
            raise RulesError(f"{where}: match.{kind} must be a list of non-empty terms")
    is_number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    if raw["field"] in NUMBER_FIELDS:
        if set(match) != {"min"} or not is_number(match["min"]):
            raise RulesError(f"{where}: number field {raw['field']} needs match: {{min: <number>}} and nothing else")
    elif "min" in match:
        raise RulesError(f"{where}: match.min only applies to number fields ({', '.join(NUMBER_FIELDS)})")
    elif not set(match) & {"any", "all", "regex"}:
        raise RulesError(f"{where}: match.none only excludes; add any, all or regex to say what fires")
    group = raw.get("group", "topic")
    if group not in GROUPS:
        raise RulesError(f"{where}: group must be one of {', '.join(GROUPS)}")
    for key in ("weight", "per_match", "max"):
        if key in raw and not is_number(raw[key]):
            raise RulesError(f"{where}: {key} must be a number")
    if graded and "max" in raw and raw["max"] * raw["per_match"] < 0:
        raise RulesError(f"{where}: 'max' must have the same sign as 'per_match'")
    source = raw.get("source", "hypothesis")
    if source not in SOURCES:
        raise RulesError(f"{where}: source must be one of {', '.join(SOURCES)}")
    try:
        return Rule(
            id=str(raw["id"]), field=raw["field"], weight=float(raw.get("weight", 0)),
            per_match=float(raw["per_match"]) if graded else None,
            max=float(raw["max"]) if "max" in raw else None,
            explanation=str(raw["explanation"]), description=str(raw.get("description", "")),
            any=[str(t) for t in match.get("any", [])], all=[str(t) for t in match.get("all", [])],
            regex=match.get("regex"), none=[str(t) for t in match.get("none", [])],
            min=float(match["min"]) if "min" in match else None,
            case_sensitive=bool(raw.get("case_sensitive", False)), group=group,
            source=source, evidence=str(raw.get("evidence", "") or ""),
        )
    except re.error as exc:
        raise RulesError(f"{where}: invalid regex: {exc}") from exc


def parse_rules(data: dict[str, Any], path: Path | None = None) -> RuleSet:
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RulesError("rules file must contain a top-level 'rules:' list")
    rules = [_rule_from_dict(raw, i) for i, raw in enumerate(data["rules"])]
    seen: set[str] = set()
    for item_id in [r.id for r in rules]:
        if item_id in seen:
            raise RulesError(f"duplicate rule id {item_id!r}")
        seen.add(item_id)
    for key, why in OBSOLETE_KEYS.items():
        if key in data:
            raise RulesError(f"'{key}' is no longer used ({why}); remove it from the rules file")
    if "base" in data and not _is_number(data["base"]):
        raise RulesError("base must be a number")
    judge = data.get("judge") or {}
    if "candidate_threshold" in judge:
        raise RulesError("judge.candidate_threshold is no longer used: set judge.candidate_floor_rule_points "
                         "(rule points without base) instead")
    if "candidate_floor_points" in judge:
        raise RulesError("judge.candidate_floor_points is no longer used: set judge.candidate_floor_rule_points, "
                         "in rule points without base (old value minus base)")
    if judge.get("candidate_floor_rule_points") is not None and not _is_number(judge["candidate_floor_rule_points"]):
        raise RulesError("judge.candidate_floor_rule_points must be a number of rule points")
    points = judge.get("points") or {}
    if set(points) - set(JUDGE_LEVELS) or not all(isinstance(v, (int, float)) for v in points.values()):
        raise RulesError(f"judge.points may only set numbers for {', '.join(JUDGE_LEVELS)}")
    return RuleSet(
        rules=rules, base=float(data.get("base", 0)), selection=_parse_selection(data.get("selection")),
        judge=judge, path=path,
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_selection(data: Any) -> Selection:
    if data is None:
        return Selection()
    if isinstance(data, dict) and "floor_points" in data:
        raise RulesError("selection.floor_points is no longer used: set selection.floor_rule_points, "
                         "in rule points without base (old value minus base)")
    if not isinstance(data, dict) or set(data) - {"top_n", "floor_rule_points", "per"}:
        raise RulesError("selection may only set top_n, floor_rule_points and per")
    top_n = data.get("top_n", Selection.top_n)
    if top_n is not None and (not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1):
        raise RulesError("selection.top_n must be a whole number of at least 1 (or empty for no limit)")
    floor = data.get("floor_rule_points")
    if floor is not None and not _is_number(floor):
        raise RulesError("selection.floor_rule_points must be a number of rule points (or empty for no floor)")
    per = data.get("per", Selection.per)
    if per not in PERIODS:
        raise RulesError(f"selection.per must be one of {', '.join(PERIODS)}; got {per!r}")
    return Selection(top_n=top_n, floor_rule_points=None if floor is None else float(floor), per=per)


def load_rules(path: Path | str | None = None) -> RuleSet:
    path = Path(path) if path else DEFAULT_RULES_PATH
    with path.open(encoding="utf-8") as f:
        return parse_rules(yaml.safe_load(f), path)
