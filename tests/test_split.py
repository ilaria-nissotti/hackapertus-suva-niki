from pathlib import Path

import pytest

from suva.ingest.importer import import_supplied
from suva.ingest.split import apply_split, compute_split

JUDGEMENTS = Path(__file__).resolve().parents[1] / "judgements"

def fake(n, lang="de", author="A"):
    return [{"docket": f"8C_{i}/2025", "language": lang, "author": author} for i in range(n)]


def test_exact_size_and_deterministic():
    exs = fake(30) + [{"docket": f"9C_{i}/2025", "language": "fr", "author": "B"} for i in range(25)]
    a, b = compute_split(exs, 15), compute_split(list(reversed(exs)), 15)
    assert a == b
    assert sum(v == "eval" for v in a.values()) == 15


def test_quota_follows_strata():
    exs = fake(40) + [{"docket": f"9C_{i}/2025", "language": "fr", "author": "B"} for i in range(10)]
    s = compute_split(exs, 10)
    assert sum(s[f"8C_{i}/2025"] == "eval" for i in range(40)) == 8
    assert sum(s[f"9C_{i}/2025"] == "eval" for i in range(10)) == 2


def test_bad_size():
    with pytest.raises(ValueError):
        compute_split(fake(5), 5)


@pytest.mark.skipif(not JUDGEMENTS.exists(), reason="supplied data not present")
def test_supplied_split_is_pinned_and_frozen(repo):
    import_supplied(repo, JUDGEMENTS)
    assert apply_split(repo) == {"newly_assigned": 55, "fewshot": 40, "eval": 15}
    assert {e["language"] for e in repo.list_examples(split="eval")} == {"de", "fr", "it"}
    manifest = repo.get_example_manifest()
    assert manifest["version"] == 2
    assert apply_split(repo, manifest=manifest)["newly_assigned"] == 0
    from suva.storage import SQLiteRepository

    rebuilt = SQLiteRepository(":memory:")
    try:
        rebuilt.migrate()
        import_supplied(rebuilt, JUDGEMENTS)
        assert apply_split(rebuilt, manifest=manifest)["eval"] == 15
        assert rebuilt.get_example_manifest() == manifest
    finally:
        rebuilt.close()
    assert apply_split(repo)["newly_assigned"] == 0  # re-run changes nothing


def test_same_docket_decisions_share_a_split():
    examples = fake(8)
    examples.extend([
        {**examples[0], "decision_id": "8C_0/2025@2025-01-01"},
        {**examples[0], "decision_id": "8C_0/2025@2025-01-02"},
    ])
    assignments = compute_split(examples, 3)
    assert assignments["8C_0/2025@2025-01-01"] == assignments["8C_0/2025@2025-01-02"]
