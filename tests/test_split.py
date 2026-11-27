from pathlib import Path

import pytest

from suva.ingest.importer import import_supplied
from suva.ingest.split import apply_split, compute_split

JUDGEMENTS = Path(__file__).resolve().parents[1] / "judgements"

# Pinned: if this changes, the eval set moved and earlier scores are not comparable.
EXPECTED_EVAL = (
    "4A_106/2025 8C_129/2025 8C_172/2025 8C_180/2025 8C_185/2025 8C_311/2025 8C_314/2025 "
    "8C_318/2025 8C_330/2025 8C_351/2025 8C_450/2025 8C_508/2025 8C_531/2025 8C_607/2024 9C_382/2025"
).split()


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
    assert [e["docket"] for e in repo.list_examples(split="eval")] == EXPECTED_EVAL
    assert apply_split(repo)["newly_assigned"] == 0  # re-run changes nothing
