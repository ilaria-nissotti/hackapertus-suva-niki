from pathlib import Path

import pytest

from suva.ingest.exports import parse_export_dir
from suva.ingest.picks import normalise_pick, read_picks

JUDGEMENTS = Path(__file__).resolve().parents[1] / "judgements"
XLSX = JUDGEMENTS / "BG_Urteile_2025_Q4_2026_Q1.xlsx"


def test_plain_docket():
    assert normalise_pick("8C_464/2025", set()) == ("8C_464/2025", "bger", [])


def test_missing_prefix_completed_only_if_unique():
    d, court, notes = normalise_pick("BGer-Urteil 458/2025 vom 18. November 2025", {"8C_458/2025"})
    assert (d, court) == ("8C_458/2025", "bger") and "completed" in notes[0]
    d, court, _ = normalise_pick("BGer-Urteil 458/2025 vom 18. November 2025", {"8C_458/2025", "9C_458/2025"})
    assert (d, court) == (None, "unresolved")
    assert normalise_pick("BGer-Urteil 458/2025", set())[1] == "unresolved"


def test_cantonal_kept_aside():
    d, court, notes = normalise_pick("Entscheid des Kantonsgerichts Basel-Landschaft vom 5. Februar 2026", set())
    assert (d, court) == (None, "cantonal") and notes


@pytest.mark.skipif(not XLSX.exists(), reason="supplied data not present")
def test_supplied_xlsx():
    known = {e.docket for e in parse_export_dir(JUDGEMENTS)}
    picks = read_picks(XLSX, known)
    assert len(picks) == 60
    courts = [p.court for p in picks]
    assert courts.count("cantonal") == 1 and courts.count("unresolved") == 0
    dockets = {p.docket for p in picks if p.docket}
    assert "8C_458/2025" in dockets and len(dockets) == 59
    assert {p.quarter for p in picks} == {"2025 Q4", "2026 Q1"}
    assert all(p.summary_created_at for p in picks)


def test_text_dates_and_blank_rows(tmp_path):
    import pandas as pd

    from suva.ingest.picks import read_picks

    path = tmp_path / "picks.xlsx"
    pd.DataFrame({
        "Urteilsnummer / Titel": ["8C_1/2025", "8C_2/2025", None],
        "Jahr / Quartal": ["2025 Q1"] * 3,
        "Erstellt": ["04.03.2025", "unknown", None],
    }).to_excel(path, index=False)
    dated, invalid = read_picks(path)
    assert dated.summary_created_at.date().isoformat() == "2025-03-04"
    assert invalid.summary_created_at is None
    assert invalid.notes == ["could not parse Erstellt date"]
