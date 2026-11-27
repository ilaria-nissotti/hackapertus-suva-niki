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

Text fields match terms as whole words, case-insensitive by default.
List fields (laws, articles, bge, chamber, language) match exact values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from suva.config import REPO_ROOT

DEFAULT_RULES_PATH = REPO_ROOT / "rules" / "rules.yaml"

TEXT_FIELDS = ("parties", "subject", "header", "facts", "considerations", "dispositiv", "text")
LIST_FIELDS = ("laws", "articles", "bge", "chamber", "language")
FIELDS = TEXT_FIELDS + LIST_FIELDS
MATCH_KINDS = ("any", "all", "regex")
SOURCES = ("expert", "data", "apertus", "hypothesis")
JUDGE_LEVELS = ("high", "medium", "low")
DEFAULT_SCALE = 10.0  # points per unit of log-odds: +10 points multiplies the odds of relevance by e


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
    case_sensitive: bool = False
    source: str = "hypothesis"
    evidence: str = ""
    per_match: float | None = None  # graded rule: points per match instead of a fixed weight
    max: float | None = None  # graded rule: cap on the points (same sign as per_match)
    _patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    _all_patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    _regex: re.Pattern | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        word = lambda term: re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags)  # noqa: E731
        if self.field in TEXT_FIELDS:
            self._patterns = [word(t) for t in self.any]
            self._all_patterns = [word(t) for t in self.all]
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
        if self.field in LIST_FIELDS:
            values = value if isinstance(value, (list, tuple, set)) else [value]
            norm = (lambda s: str(s)) if self.case_sensitive else (lambda s: str(s).casefold())
            have = {norm(v) for v in values}
            hits = len(have & {norm(t) for t in self.any}) if self.any else None
            if hits == 0 or (self.all and not {norm(t) for t in self.all} <= have):
                return 0
            regex_hits = sum(1 for v in values if self._regex.search(str(v))) if self._regex else None
            if regex_hits == 0:
                return 0
            return hits if hits is not None else regex_hits if regex_hits is not None else len(self.all)
        text = str(value)
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
class RuleSet:
    rules: list[Rule]
    threshold: float = 50.0  # on the 0-100 score
    base: float = 0.0  # starting points before any rule fires
    scale: float = DEFAULT_SCALE
    bands: dict[str, float] = field(default_factory=lambda: {"high": 70.0, "low": 30.0})
    judge: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def by_id(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)

    @property
    def judge_points(self) -> dict[str, float]:
        return {k: float(v) for k, v in (self.judge.get("points") or {}).items()}


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
    for kind in ("any", "all"):
        terms = match.get(kind, [])
        if not isinstance(terms, list) or not all(isinstance(t, (str, int)) and str(t).strip() for t in terms):
            raise RulesError(f"{where}: match.{kind} must be a list of non-empty terms")
    is_number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
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
            regex=match.get("regex"), case_sensitive=bool(raw.get("case_sensitive", False)),
            source=source, evidence=str(raw.get("evidence", "") or ""),
        )
    except re.error as exc:
        raise RulesError(f"{where}: invalid regex: {exc}") from exc


def parse_rules(data: dict[str, Any], path: Path | None = None) -> RuleSet:
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RulesError("rules file must contain a top-level 'rules:' list")
    rules = [_rule_from_dict(raw, i) for i, raw in enumerate(data["rules"])]
    seen: set[str] = set()
    for r in rules:
        if r.id in seen:
            raise RulesError(f"duplicate rule id {r.id!r}")
        seen.add(r.id)
    for key in ("threshold", "base", "scale"):
        if key in data and not isinstance(data[key], (int, float)):
            raise RulesError(f"{key} must be a number")
    if data.get("scale", DEFAULT_SCALE) <= 0:
        raise RulesError("scale must be positive")
    bands = {"high": 70.0, "low": 30.0} | {k: float(v) for k, v in (data.get("bands") or {}).items()}
    if set(bands) != {"high", "low"} or not 0 <= bands["low"] <= bands["high"] <= 100:
        raise RulesError("bands needs 'high' and 'low' between 0 and 100, with low <= high")
    judge = data.get("judge") or {}
    points = judge.get("points") or {}
    if set(points) - set(JUDGE_LEVELS) or not all(isinstance(v, (int, float)) for v in points.values()):
        raise RulesError(f"judge.points may only set numbers for {', '.join(JUDGE_LEVELS)}")
    return RuleSet(
        rules=rules, threshold=float(data.get("threshold", 50)), base=float(data.get("base", 0)),
        scale=float(data.get("scale", DEFAULT_SCALE)), bands=bands, judge=judge, path=path,
    )


def load_rules(path: Path | str | None = None) -> RuleSet:
    path = Path(path) if path else DEFAULT_RULES_PATH
    with path.open(encoding="utf-8") as f:
        return parse_rules(yaml.safe_load(f), path)
