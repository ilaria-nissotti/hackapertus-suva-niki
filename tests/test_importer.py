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
    assert kp["8C_458/2025"]["in_xlsx"] == 1 and kp["8C_458/2025"]["in_exports"] == 1
    assert kp["8C_458/2025"]["notes"]  # records the prefix fix
