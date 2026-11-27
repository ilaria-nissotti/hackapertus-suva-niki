import sqlite3
from pathlib import Path

import pytest

from suva.ingest.importer import import_supplied

JUDGEMENTS = Path(__file__).resolve().parents[1] / "judgements"


@pytest.mark.skipif(not (JUDGEMENTS / "BG_Urteile_2025_Q4_2026_Q1.xlsx").exists(), reason="supplied data not present")
def test_import_supplied_counts_and_rerun(repo):
    rep = import_supplied(repo, JUDGEMENTS)
    assert (rep.examples, rep.xlsx_rows) == (55, 60)
    assert (rep.in_both, rep.xlsx_only, rep.exports_only) == (32, 28, 23)
    assert (rep.known_positives, rep.cantonal, rep.unresolved) == (83, 1, 0)
    assert len(repo.list_examples()) == 55
    assert len(repo.list_known_positives(court="bger")) == 82

    rep2 = import_supplied(repo, JUDGEMENTS)  # idempotent
    assert rep2.known_positives == 83 and len(repo.list_known_positives()) == 83

    kp = {r["ref"]: r for r in repo.list_known_positives()}
    assert kp["8C_458/2025@2025-11-18"]["in_xlsx"] == 1 and kp["8C_458/2025@2025-11-18"]["in_exports"] == 1
    assert kp["8C_458/2025@2025-11-18"]["notes"]  # records the prefix fix


def test_ambiguous_docket_and_atomic_replacement(repo, tmp_path, monkeypatch):
    from datetime import date

    from suva.ingest import importer
    from suva.ingest.exports import ExampleSummary
    from suva.ingest.picks import Pick

    docket = "8C_1/2025"
    examples = [
        ExampleSummary(docket, date(2025, 1, day), "title", "de", "de", "fr", None, None, None, f"{day}.html")
        for day in (1, 2)
    ]
    (tmp_path / "BG_Urteile_test.xlsx").touch()
    monkeypatch.setattr(importer, "parse_export_dir", lambda _: examples)
    monkeypatch.setattr(importer, "read_picks", lambda *_: [
        Pick(docket, docket, "bger", None, None),
    ])
    result = import_supplied(repo, tmp_path)
    assert (result.known_positives, result.unresolved, result.in_both) == (3, 1, 0)
    assert len(repo.list_examples()) == 2

    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_supplied([], [{"ref": "broken"}])
    assert len(repo.list_examples()) == 2
    assert len(repo.list_known_positives()) == 3

    with pytest.raises(ValueError, match="does not exist"):
        import_supplied(repo, tmp_path / "missing")
