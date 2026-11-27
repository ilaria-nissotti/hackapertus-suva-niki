from datetime import date

from suva.sources.base import SourceRecord


def rec(source="opencaselaw", text="kurz", pub=None, d=date(2025, 11, 11), **kw):
    return SourceRecord(source=source, source_id=f"{source}-1", docket="8C_129/2025", decision_date=d,
                        language="fr", text=text, publication_date=pub, **kw)


def test_new_then_merge_keeps_best_data(repo):
    assert repo.upsert_judgement(rec(text="kurz")) is True
    assert repo.upsert_judgement(rec(source="entscheidsuche", text=None, html_url="https://e/x.html")) is False
    j = repo.get_judgement("8C_129/2025#2025-11-11")
    assert j["text"] == "kurz" and j["text_source"] == "opencaselaw" and j["chamber_prefix"] == "8C"
    assert [s["source"] for s in repo.judgement_sources(j["key"])] == ["entscheidsuche", "opencaselaw"]


def test_text_only_replaced_by_longer(repo):
    repo.upsert_judgement(rec(text="a much longer text"))
    repo.upsert_judgement(rec(text="short"))
    assert repo.get_judgement("8C_129/2025#2025-11-11")["text"] == "a much longer text"


def test_publication_date_fills_unknown_only(repo):
    repo.upsert_judgement(rec(pub=None))
    repo.upsert_judgement(rec(pub=date(2025, 12, 1)))
    repo.upsert_judgement(rec(pub=date(2026, 1, 1)))
    j = repo.get_judgement("8C_129/2025#2025-11-11")
    assert j["publication_date"] == "2025-12-01" and j["publication_date_source"] == "opencaselaw"


def test_same_docket_other_date_is_separate(repo):
    repo.upsert_judgement(rec())
    repo.upsert_judgement(rec(d=date(2026, 1, 5)))
    assert len(repo.list_judgements()) == 2


def test_list_filters_and_no_text_column(repo):
    repo.upsert_judgement(rec())
    repo.upsert_judgement(rec(d=date(2026, 6, 1)))
    rows = repo.list_judgements(start="2025-10-01", end="2026-03-31")
    assert len(rows) == 1 and "text" not in rows[0] and rows[0]["text_length"] == 4
    assert repo.list_judgements(dockets={"9C_1/2025"}) == []


def test_set_html_path(repo):
    repo.upsert_judgement(rec())
    repo.set_html_path("8C_129/2025#2025-11-11", "data/raw/x.html")
    assert repo.get_judgement("8C_129/2025#2025-11-11")["html_path"] == "data/raw/x.html"
