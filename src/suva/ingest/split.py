"""Create and replay versioned manifests for the supplied summary holdout."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from suva.hashing import sha256
from suva.storage.base import Repository

SEED = "examples-split-v2"
N_EVAL = 15


def _stratified_split(examples: list[dict], n_eval: int) -> dict[str, str]:
    strata: dict[tuple, list[str]] = defaultdict(list)
    for e in examples:
        strata[(e.get("language"), e.get("author"))].append(e["docket"])
    total = sum(len(v) for v in strata.values())
    if total < 2:
        raise ValueError(f"Need at least 2 example dockets to split, found {total}: "
                         "run `python -m suva import-supplied` first")
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


def compute_split(examples: list[dict], n_eval: int = N_EVAL) -> dict[str, str]:
    # Keep every dated decision for the same docket in one group.
    representatives = {}
    for row in sorted(examples, key=lambda row: row.get("decision_id", row["docket"])):
        representatives.setdefault(row["docket"], row)
    groups = list(representatives.values())
    split = _stratified_split(groups, n_eval)

    # Give each source language representation when the requested size allows it.
    languages = {row["language"] for row in groups if row.get("language")}
    eval_languages = Counter(row.get("language") for row in groups if split[row["docket"]] == "eval")
    if n_eval >= len(languages):
        ordered = sorted(groups, key=lambda row: sha256(SEED + row["docket"]))
        for language in sorted(languages - eval_languages.keys()):
            incoming = next(row for row in ordered if row.get("language") == language)
            outgoing = next(row for row in reversed(ordered) if split[row["docket"]] == "eval"
                            and (row.get("language") is None or eval_languages[row["language"]] > 1))
            split[incoming["docket"]] = "eval"
            split[outgoing["docket"]] = "fewshot"
            eval_languages[outgoing.get("language")] -= 1
            eval_languages[language] += 1
    return {row.get("decision_id", row["docket"]): split[row["docket"]] for row in examples}


def _source_hashes(examples: list[dict]) -> dict[str, str]:
    return {
        row["decision_id"]: sha256({key: val for key, val in row.items() if key not in ("split", "imported_at")})
        for row in examples
    }


def validate_manifest(manifest: dict, examples: list[dict]) -> None:
    assignments = manifest.get("assignments", {})
    hashes = _source_hashes(examples)
    if manifest.get("version") not in (1, 2) or manifest.get("source_hashes") != hashes:
        raise ValueError("Split manifest version or source hashes do not match the imported corpus")
    if assignments.keys() != hashes.keys() or set(assignments.values()) != {"eval", "fewshot"}:
        raise ValueError("Split manifest must assign every decision to an evaluation or few-shot group")
    grouped = defaultdict(set)
    for row in examples:
        grouped[row["docket"]].add(assignments[row["decision_id"]])
    if any(len(splits) > 1 for splits in grouped.values()):
        raise ValueError("Split manifest separates decisions with the same docket")


def apply_split(repo: Repository, n_eval: int = N_EVAL, manifest: dict | None = None) -> dict[str, int]:
    if manifest is None:
        manifest = repo.get_example_manifest()
    if manifest is None:
        examples = repo.list_examples()
        legacy = {row["decision_id"]: row["split"] for row in examples if row["split"]}
        if legacy and len(legacy) != len(examples):
            raise ValueError("Legacy split is incomplete. Build a new split in a fresh database")
        manifest = {
            "version": 1 if legacy else 2,
            "source_hashes": _source_hashes(examples),
            "assignments": legacy or compute_split(examples, n_eval),
        }
    return repo.freeze_examples(manifest)
