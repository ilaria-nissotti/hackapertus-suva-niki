from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from suva.sources.opencaselaw import read_bger


def write(tmp_path, rows):
    cols = {k: [r.get(k) for r in rows] for k in (
        "decision_id", "docket_number", "decision_date", "publication_date", "language",
        "chamber", "source_url", "full_text", "has_full_text", "scraped_at")}
    p = tmp_path / "bger.parquet"
    pq.write_table(pa.table(cols), p)
    return p


def row(docket, d, text="Erwägungen", **kw):
    base = dict(decision_id=f"bger_{docket}", docket_number=docket, decision_date=d,
                publication_date=None, language="de", chamber="IV. öffentlich-rechtliche Abteilung",
                source_url="http://relevancy.bger.ch/x", full_text=text, has_full_text=text is not None,
                scraped_at="2026-11-26T14:00:00")
    base.update(kw)
    return base


def test_date_filter_and_fields(tmp_path):
    p = write(tmp_path, [row("8C_1/2025", "2025-10-01", publication_date="2025-10-20"),
                         row("8C_2/2025", "2026-05-01")])
    recs = list(read_bger(p, date(2025, 10, 1), date(2026, 3, 31)))
    assert [r.docket for r in recs] == ["8C_1/2025"]
    r = recs[0]
    assert r.key == "8C_1/2025#2025-10-01"
    assert r.publication_date == date(2025, 10, 20) and r.text == "Erwägungen"


def test_docket_filter(tmp_path):
    p = write(tmp_path, [row("8C_1/2025", "2025-10-01"), row("9C_5/2025", "2024-01-01")])
    assert [r.docket for r in read_bger(p, dockets={"9C_5/2025"})] == ["9C_5/2025"]


def test_duplicates_collapsed_prefer_text(tmp_path):
    p = write(tmp_path, [row("8C_1/2025", "2025-10-01", text=None),
                         row("8C_1/2025", "2025-10-01", text="long text here")])
    recs = list(read_bger(p))
    assert len(recs) == 1 and recs[0].text == "long text here"


def test_same_docket_two_decisions_kept(tmp_path):
    p = write(tmp_path, [row("8C_1/2025", "2025-10-01"), row("8C_1/2025", "2025-12-01")])
    assert len(list(read_bger(p))) == 2


def test_without_text(tmp_path):
    p = write(tmp_path, [row("8C_1/2025", "2025-10-01")])
    assert list(read_bger(p, with_text=False))[0].text is None
