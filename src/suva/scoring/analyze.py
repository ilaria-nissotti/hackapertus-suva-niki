"""Find candidate rules from the data: which features separate relevant from not relevant.

For every feature (a word in the subject, a law cited, a BGE cited, the chamber ...)
count in how many relevant and not-relevant judgements it appears. Features that
appear in enough relevant judgements AND much more often there are rule candidates.
Run on the training split only, so the test split stays unseen.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

TOKEN_RE = re.compile(r"[^\W\d_][\w'’-]*[^\W_]|[^\W\d_]{2,}", re.UNICODE)
STOPWORDS = {
    # DE
    "der", "die", "das", "den", "dem", "des", "und", "oder", "von", "vom", "zur", "zum", "mit", "für", "auf",
    "aus", "bei", "gegen", "nach", "über", "eine", "einer", "eines", "ein", "ist", "im", "in", "an",
    "vertreten", "durch", "rechtsanwalt", "rechtsanwältin", "beschwerdeführer", "beschwerdeführerin",
    "beschwerdegegner", "beschwerdegegnerin", "urteil", "entscheid", "kantons", "kantonsgerichts",
    # FR
    "les", "des", "une", "par", "pour", "sur", "dans", "contre", "aux", "du", "de", "la", "le", "et", "en",
    "représenté", "représentée", "recourant", "recourante", "intimé", "intimée", "arrêt", "avocat", "avocate",
    # IT
    "del", "della", "dei", "delle", "il", "lo", "gli", "per", "con", "contro", "patrocinato", "patrocinata",
    "ricorrente", "opponente", "sentenza", "avv",
}
TEXT_FEATURE_FIELDS = ("subject", "parties")
LIST_FEATURE_FIELDS = ("chamber", "language", "laws", "articles", "bge")


def tokens(text: str) -> set[str]:
    words = [w.casefold() for w in TOKEN_RE.findall(text.replace("_", " "))]
    words = [w for w in words if len(w) >= 3 and w not in STOPWORDS]
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}


def features(record: dict[str, Any]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for f in TEXT_FEATURE_FIELDS:
        out |= {(f, t) for t in tokens(record.get(f) or "")}
    for f in LIST_FEATURE_FIELDS:
        value = record.get(f)
        values = value if isinstance(value, list) else ([value] if value else [])
        out |= {(f, str(v)) for v in values}
    return out


@dataclass
class FeatureStat:
    field: str
    value: str
    n_rel: int
    n_non: int
    share_rel: float
    share_non: float
    lift: float  # share_rel / share_non (smoothed)
    log_odds: float
    precision: float  # of judgements with this feature, share relevant


def feature_stats(records: Iterable[dict[str, Any]]) -> tuple[list[FeatureStat], int, int]:
    rel, non = Counter(), Counter()
    n_rel = n_non = 0
    for rec in records:
        feats = features(rec)
        if rec["relevant"]:
            n_rel += 1
            rel.update(feats)
        else:
            n_non += 1
            non.update(feats)
    stats = []
    for key in set(rel) | set(non):
        a, b = rel[key], non[key]
        s_rel = (a + 0.5) / (n_rel + 1)
        s_non = (b + 0.5) / (n_non + 1)
        stats.append(FeatureStat(
            field=key[0], value=key[1], n_rel=a, n_non=b,
            share_rel=a / n_rel if n_rel else 0.0, share_non=b / n_non if n_non else 0.0,
            lift=s_rel / s_non,
            log_odds=math.log(s_rel / (1 - s_rel)) - math.log(s_non / (1 - s_non)),
            precision=a / (a + b) if a + b else 0.0,
        ))
    return stats, n_rel, n_non


def positive_candidates(stats: list[FeatureStat], min_rel: int, top: int) -> list[FeatureStat]:
    return sorted((s for s in stats if s.n_rel >= min_rel), key=lambda s: (-s.log_odds, -s.n_rel))[:top]


def negative_candidates(stats: list[FeatureStat], min_non: int, top: int) -> list[FeatureStat]:
    return sorted((s for s in stats if s.n_non >= min_non), key=lambda s: (s.log_odds, -s.n_non))[:top]


def write_csv(stats: list[FeatureStat], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["field", "value", "n_rel", "n_non", "share_rel", "share_non", "lift", "log_odds", "precision"])
        for s in sorted(stats, key=lambda s: -s.log_odds):
            w.writerow([s.field, s.value, s.n_rel, s.n_non, f"{s.share_rel:.3f}", f"{s.share_non:.3f}",
                        f"{s.lift:.2f}", f"{s.log_odds:.2f}", f"{s.precision:.3f}"])


def overview(records: list[dict[str, Any]]) -> dict[str, dict[str, list[int]]]:
    """Counts of relevant / not relevant per chamber and language."""
    out: dict[str, dict[str, list[int]]] = {}
    for f in ("chamber", "language", "parse_status"):
        table: dict[str, list[int]] = {}
        for rec in records:
            row = table.setdefault(str(rec.get(f) or "?"), [0, 0])
            row[0 if rec["relevant"] else 1] += 1
        out[f] = dict(sorted(table.items(), key=lambda kv: -sum(kv[1])))
    return out


SUVA_TERMS = re.compile(r"(?<!\w)(Suva|SUVA|Schweizerische Unfallversicherungsanstalt|CNA|INSAI|"
                        r"Caisse nationale suisse d'assurance|Istituto nazionale svizzero di assicurazione)(?!\w)")


def suva_check(records: list[dict[str, Any]]) -> dict[str, int]:
    """Key question: of the judgements where Suva is a party, how many are relevant?"""
    suva = [bool(SUVA_TERMS.search(r.get("parties") or "")) for r in records]
    return {
        "suva_party": sum(suva),
        "suva_party_relevant": sum(r["relevant"] for r, s in zip(records, suva) if s),
        "relevant_total": sum(r["relevant"] for r in records),
        "relevant_without_suva_party": sum(r["relevant"] for r, s in zip(records, suva) if not s),
    }
