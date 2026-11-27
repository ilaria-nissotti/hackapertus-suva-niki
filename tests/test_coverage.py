from datetime import date

from suva.coverage import coverage_by_week
from suva.sources.base import SourceRecord


def add(repo, docket, d, source, text=None):
    repo.upsert_judgement(SourceRecord(source=source, source_id=f"{source}:{docket}", docket=docket,
                                       decision_date=d, text=text))


def test_weekly_counts_and_one_source_gaps(repo):
    add(repo, "8C_1/2025", date(2025, 10, 6), "opencaselaw", "t")
    add(repo, "8C_1/2025", date(2025, 10, 6), "entscheidsuche")
    add(repo, "8C_2/2025", date(2025, 10, 7), "opencaselaw", "t")
    add(repo, "8C_3/2025", date(2025, 10, 8), "entscheidsuche")
    add(repo, "8C_4/2025", date(2025, 10, 13), "opencaselaw", "t")
    weeks = coverage_by_week(repo, "2025-10-01", "2025-10-31")
    assert [w["week"] for w in weeks] == ["2025-10-06", "2025-10-13"]
    w = weeks[0]
    assert (w["total"], w["opencaselaw"], w["entscheidsuche"]) == (3, 2, 2)
    assert (w["only_opencaselaw"], w["only_entscheidsuche"], w["no_text"]) == (1, 1, 1)


def test_latest_run_distinguishes_empty_from_unqueried(repo):
    from suva.storage.base import AuditEntry

    add(repo, "8C_1/2025", date(2025, 10, 6), "entscheidsuche", "old text")
    repo.append_audit(AuditEntry(kind="fetch_run", actor="test", payload={
        "source_runs": {
            "opencaselaw": {"status": "complete", "ranges": [["2025-10-06", "2025-10-12"]]},
            "entscheidsuche": {"status": "disabled", "ranges": []},
        },
        "sightings": {},
    }))
    week, = coverage_by_week(repo, "2025-10-06", "2025-10-12")
    assert week == {"week": "2025-10-06", "queries": {"opencaselaw": "queried", "entscheidsuche": "unqueried"}}


def test_publication_coverage_keeps_late_and_unknown_publications_separate():
    from suva.coverage import publication_coverage

    totals = publication_coverage({"sightings": {
        "late": {"decision_date": "2025-01-01", "publication_date": "2025-10-08",
                 "sources": ["opencaselaw"], "has_text": True},
        "unknown": {"decision_date": "2025-10-06", "publication_date": None,
                    "sources": ["entscheidsuche"], "has_text": False},
    }})
    assert totals["known_publication_date"]["total"] == 1
    assert totals["unknown_publication_date"]["no_text"] == 1
