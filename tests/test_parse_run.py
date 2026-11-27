from datetime import date

from suva.parse.run import parse_all
from suva.sources.base import SourceRecord

GOOD = ("Bundesgericht\n\nGegenstand\n\nUnfall\n\nErwägungen:\n\n1.\n\nArt. 6 UVG.\n\n"
        "Demnach erkennt das Bundesgericht:\n\n1.\n\nAbgewiesen.") + " x" * 600
ERROR = "menu\n\nListe der Neuheiten\n\nDokument nicht gefunden Dieser AZA-Entscheid ist in elektronischer Form nicht verfügbar."


def add(repo, docket, text):
    repo.upsert_judgement(SourceRecord(source="opencaselaw", source_id=docket, docket=docket,
                                       decision_date=date(2025, 10, 2), text=text))


def test_parse_all_reports_and_skips_unchanged(repo):
    add(repo, "8C_1/2025", GOOD)
    add(repo, "8C_2/2025", None)
    repo.upsert_known_positive(dict(ref="8C_2/2025", docket="8C_2/2025", court="bger", in_xlsx=1, in_exports=0))
    rep = parse_all(repo)
    assert rep.parsed == 2 and rep.status == {"ok": 1, "failed": 1} and rep.citations == 1
    assert rep.picks_not_ok == ["8C_2/2025@2025-10-02 (no_text)"]
    assert rep.watched_not_ok == ["8C_2/2025@2025-10-02 (no_text)"]

    again = parse_all(repo)
    assert again.parsed == 0 and again.skipped_unchanged == 2 and again.status == rep.status

    assert parse_all(repo, force=True).parsed == 2
    assert repo.list_audit(kind="parse_run")[0]["payload"]["parsed"] == 2


def test_text_change_triggers_reparse(repo):
    add(repo, "8C_1/2025", ERROR)
    assert repo.get_judgement("8C_1/2025@2025-10-02")["text"] is None
    with repo.conn:
        repo.conn.execute("UPDATE judgements SET text = ? WHERE key = ?", (ERROR, "8C_1/2025@2025-10-02"))
    assert parse_all(repo).issues == {"source_error_page": 1}
    add(repo, "8C_1/2025", GOOD)  # real text arrives later (fallback)
    rep = parse_all(repo)
    assert rep.parsed == 1 and rep.status == {"ok": 1}


def test_parse_pick_report_uses_exact_decision(repo):
    docket = "8C_1/2025"
    add(repo, docket, GOOD)
    repo.upsert_judgement(SourceRecord(
        source="opencaselaw", source_id="other", docket=docket, decision_date=date(2025, 10, 3),
    ))
    repo.upsert_known_positive(dict(
        ref=f"{docket}@2025-10-02", docket=docket, court="bger", in_xlsx=1, in_exports=0,
    ))
    report = parse_all(repo)
    assert report.watched_not_ok == [f"{docket}@2025-10-03 (no_text)"]
    assert not report.picks_not_ok
