"""LLM judge (second stage): Apertus rates the relevance of candidate judgements.

Runs only on candidates (rules score >= judge.candidate_threshold in rules.yaml),
with a rubric that also lives in rules.yaml so experts control it. Answers are
cached by the LLM client (temperature 0), so a judgement is not judged twice
differently.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from suva.llm import LLMClient
from suva.scoring.rules import RuleSet

PROMPT_VERSION = "judge/v2"
LEVELS = ("high", "medium", "low")
DEFAULT_RUBRIC = (
    "Suva is the largest Swiss accident insurer. Its lawyers summarise Federal Supreme Court judgements "
    "that matter for their daily work in accident insurance (UVG/LAA), e.g. new or clarified case law on "
    "the notion of accident, causal link, benefits, integrity compensation, pensions, insurance obligation "
    "and premiums, or procedural questions under ATSG that affect accident insurers. Judgements that only "
    "concern other insurance branches, or are dismissed on purely formal grounds without a legal statement "
    "of interest, are usually not relevant."
)
INSTRUCTIONS = (
    "Rate how relevant the judgement excerpt below is for Suva's lawyers.\n"
    'Answer ONLY with JSON: {"relevance": "high" | "medium" | "low", "reason": "<one or two sentences>"}.\n'
    "Base the reason on the excerpt; do not invent facts."
)
JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class Verdict:
    key: str
    relevance: str | None  # high | medium | low, None if the answer was unusable
    reason: str
    rules_score: float
    cached: bool
    error: str | None = None

    @property
    def relevant(self) -> bool:
        return self.relevance in ("high", "medium")

    def as_dict(self) -> dict:
        return asdict(self)


def excerpt(record: dict[str, Any], facts_chars: int = 1500, ruling_chars: int = 600) -> str:
    parts = [
        f"Case: {record.get('docket')} ({record.get('decision_date')}), chamber {record.get('chamber')}",
        f"Parties: {record.get('parties') or '-'}",
        f"Subject: {record.get('subject') or '-'}",
        f"Laws cited: {', '.join(record.get('laws') or []) or '-'}",
        *([f"Headnote (leading decision): {record['regeste'][:facts_chars]}"] if record.get("regeste") else []),
        f"Facts (start): {(record.get('facts') or record.get('text') or '')[:facts_chars]}",
        f"Ruling: {(record.get('dispositiv') or '')[:ruling_chars] or '-'}",
    ]
    return "\n\n".join(parts)


def parse_verdict(text: str) -> tuple[str | None, str, str | None]:
    match = JSON_RE.search(text or "")
    if not match:
        return None, "", "no_json"
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, "", "invalid_json"
    level = str(data.get("relevance", "")).strip().lower()
    if level not in LEVELS:
        return None, str(data.get("reason", "")), "invalid_relevance"
    return level, str(data.get("reason", "")).strip(), None


def judge_record(llm: LLMClient, record: dict[str, Any], ruleset: RuleSet, rules_score: float) -> Verdict:
    rubric = ruleset.judge.get("rubric") or DEFAULT_RUBRIC
    messages = [
        {"role": "system", "content": f"{rubric.strip()}\n\n{INSTRUCTIONS}"},
        {"role": "user", "content": excerpt(record)},
    ]
    result = llm.chat(messages, prompt_version=PROMPT_VERSION, max_tokens=300,
                      model=ruleset.judge.get("model") or None)
    level, reason, error = parse_verdict(result.text)
    return Verdict(record["key"], level, reason, rules_score, result.cached, error)
