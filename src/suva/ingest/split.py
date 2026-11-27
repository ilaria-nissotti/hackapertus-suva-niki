"""Frozen few-shot / eval split of Suva's example summaries.

Decided in step 2, before any prompt work, so the summary prompts can never be
tuned on the evaluation set:
- eval: N_EVAL examples (default 15) used only to grade our generated summaries
- fewshot: the rest; the only pool Apertus may copy style from

Deterministic: examples are stratified by (decision language, author) and ordered
by sha256(SEED + docket); the eval quota is spread over strata by largest
remainder. Same input files -> same split on any machine.
"""

from __future__ import annotations

import math
from collections import defaultdict

from suva.hashing import sha256
from suva.storage.base import Repository

SEED = "examples-split-v1"
N_EVAL = 15


def compute_split(examples: list[dict], n_eval: int = N_EVAL) -> dict[str, str]:
    strata: dict[tuple, list[str]] = defaultdict(list)
    for e in examples:
        strata[(e.get("language"), e.get("author"))].append(e["docket"])
    total = sum(len(v) for v in strata.values())
    if not 0 < n_eval < total:
        raise ValueError(f"n_eval must be between 1 and {total - 1}")

    quota = {k: n_eval * len(v) / total for k, v in strata.items()}
    take = {k: math.floor(q) for k, q in quota.items()}
    by_remainder = sorted(strata, key=lambda k: (-(quota[k] - take[k]), str(k)))
    for k in by_remainder[: n_eval - sum(take.values())]:
        take[k] += 1

    split = {}
    for k, dockets in strata.items():
        ordered = sorted(dockets, key=lambda d: sha256(SEED + d))
        for i, d in enumerate(ordered):
            split[d] = "eval" if i < take[k] else "fewshot"
    return split


def apply_split(repo: Repository, n_eval: int = N_EVAL) -> dict[str, int]:
    """Assign the split to every example that has none yet; never moves an assigned one."""
    examples = repo.list_examples()
    split = compute_split(examples, n_eval)
    assigned = 0
    for e in examples:
        if e["split"] is None:
            repo.set_example_split(e["docket"], split[e["docket"]])
            assigned += 1
        elif e["split"] != split[e["docket"]]:
            # frozen earlier under different inputs: keep it, but make it visible
            print(f"warning: {e['docket']} frozen as {e['split']}, recomputed {split[e['docket']]}")
    counts = {"fewshot": 0, "eval": 0}
    for e in repo.list_examples():
        counts[e["split"]] += 1
    return {"newly_assigned": assigned, **counts}
