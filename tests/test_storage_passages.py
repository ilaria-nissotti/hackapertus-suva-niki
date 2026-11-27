from datetime import date

from suva.parse.citations import extract
from suva.parse.segment import segment
from suva.sources.base import SourceRecord

TEXT = ("Bundesgericht\n\n8C_1/2025\n\nGegenstand\n\nUnfallversicherung\n\nErwägungen:\n\n1.\n\n"
        "Nach Art. 6 Abs. 2 UVG und BGE 134 V 109 E. 2.1 gilt …\n\nDemnach erkennt das Bundesgericht:\n\n1.\n\n"
        "Die Beschwerde wird abgewiesen.") + " x" * 600
KEY = "8C_1/2025@2025-10-02"


def store(repo, text=TEXT):
    repo.upsert_judgement(SourceRecord(source="opencaselaw", source_id="1", docket="8C_1/2025",
                                       decision_date=date(2025, 10, 2), text=text))
    seg = segment(KEY, text)
    cits = {p.pid: extract(p.text, self_docket="8C_1/2025") for p in seg.passages}
    repo.save_parse(seg, cits, text_hash="h")
    return seg


def test_save_and_read_back(repo):
    seg = store(repo)
    p = repo.get_parse(KEY)
    assert p["status"] == seg.status and p["passages"] == len(seg.passages) and p["citations"] == 2
    pids = [x["pid"] for x in repo.list_passages(KEY)]
    assert pids == [x.pid for x in seg.passages] and f"{KEY}#E.1" in pids
    assert [c["canonical"] for c in repo.list_citations(KEY)] == ["Art. 6 Abs. 2 UVG", "BGE 134 V 109"]


def test_reparse_replaces(repo):
    store(repo)
    store(repo, TEXT.replace("BGE 134 V 109 E. 2.1", "nichts"))
    assert [c["canonical"] for c in repo.list_citations(KEY)] == ["Art. 6 Abs. 2 UVG"]
    assert len(repo.list_passages(KEY)) == len(segment(KEY, TEXT).passages)


def test_summary(repo):
    store(repo)
    repo.upsert_judgement(SourceRecord(source="opencaselaw", source_id="2", docket="8C_2/2025",
                                       decision_date=date(2025, 10, 3), text=None))
    s = repo.parse_summary()
    assert s["status"] == {"ok": 1} and s["unparsed"] == 1
