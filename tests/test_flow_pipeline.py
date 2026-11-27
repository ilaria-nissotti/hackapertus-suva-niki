"""Integration: fetch -> parse -> score -> review records on one scratch KB, plus pipeline edge cases."""

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import review_kb
from suva.coverage import coverage_by_week, publication_coverage
from suva.fetch import fetch
from suva.ingest.importer import import_supplied
from suva.parse.run import parse_all
from suva.parse.segment import PARSER_VERSION
from suva.scoring.dataset import match_labels
from suva.scoring.rules import load_rules
from suva.scoring.store import rules_hash, score_keys, score_window
from suva.sources.base import SourceRecord
from suva.storage.base import AuditEntry
from suva.windows import Window
from test_fetch import COLS, es_client, es_hit
from test_review_app import SUVA_CASE

DEV = Window("dev", date(2025, 10, 1), date(2025, 10, 7))


def write_parquet(raw, rows):
    """Like test_fetch.parquet, but with real judgement text and an optional publication date."""
    p = raw / "opencaselaw" / "bger.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    full = [dict(decision_id=f"bger_{d}_{dt}", docket_number=d, decision_date=dt, publication_date=pub,
                 language="de", chamber=None, source_url=f"https://www.bger.ch/{d}", full_text=text,
                 has_full_text=bool(text), scraped_at=None) for d, dt, pub, text in rows]
    pq.write_table(pa.table({k: [r[k] for r in full] for k in COLS}), p)


def test_fetch_parse_score_review_end_to_end(tmp_path, repo):
    raw = tmp_path / "raw"
    write_parquet(raw, [("8C_1/2025", "2025-10-02", None, SUVA_CASE)])
    client = es_client([es_hit("8C_1/2025", "2025-10-02"), es_hit("8C_3/2025", "2025-10-04")], [])

    rep = fetch(repo, raw, DEV, client=client)
    assert rep.errors == [] and rep.new_judgements == 2
    assert set(rep.sightings) == {"8C_1/2025@2025-10-02", "8C_3/2025@2025-10-04"}

    parsed = parse_all(repo)
    assert parsed.judgements == 2 and parsed.parsed == 2 and parsed.passages > 0
    passages = repo.list_passages("8C_1/2025@2025-10-02")
    text = repo.get_judgement("8C_1/2025@2025-10-02")["text"]
    assert all(text[p["start"]:p["end"]] == p["text"] for p in passages)  # verbatim spans

    ruleset = load_rules()
    scored = score_window(repo, DEV, ruleset)
    assert scored.scored == 2 and scored.not_parsed == 0

    records = {r["key"]: r for r in review_kb.window_records(repo, DEV, rules_digest=rules_hash(ruleset))}
    case = records["8C_1/2025@2025-10-02"]
    assert case["score_current"] and case["status"] in {"Relevant", "Review"} and case["fired"]
    assert records["8C_3/2025@2025-10-04"]["status"] in {"Relevant", "Review", "Low relevance"}

    # A second run is idempotent: nothing new, nothing re-parsed.
    again = fetch(repo, raw, DEV, client=es_client([es_hit("8C_1/2025", "2025-10-02")], []))
    assert again.new_judgements == 0
    assert parse_all(repo).skipped_unchanged == 2


def test_changed_text_invalidates_parse_and_marks_score_stale(repo):
    key = "8C_1/2025@2025-10-02"
    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2), text=SUVA_CASE))
    parse_all(repo)
    rs = load_rules()
    score_keys(repo, [key], rs)
    assert review_kb.get_record(repo, key, rules_hash(rs))["score_current"]

    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2),
                                       text=SUVA_CASE + "\n\nNachtrag mit weiterem Text."))
    assert repo.get_parse(key) is None  # longer text replaced the old one and dropped its parse
    assert review_kb.get_record(repo, key, rules_hash(rs))["status"] == "Stale"
    assert parse_all(repo).parsed == 1
    score_keys(repo, [key], rs)
    assert review_kb.get_record(repo, key, rules_hash(rs))["score_current"]


def test_new_rules_make_stored_scores_stale(repo):
    key = "8C_1/2025@2025-10-02"
    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2), text=SUVA_CASE))
    parse_all(repo)
    score_keys(repo, [key], load_rules())
    assert review_kb.get_record(repo, key, "some-other-rules-hash")["status"] == "Stale"


def test_parser_version_stored_with_the_score(repo):
    key = "8C_1/2025@2025-10-02"
    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2), text=SUVA_CASE))
    parse_all(repo)
    score_keys(repo, [key], load_rules())
    assert repo.get_score(key)["parser_version"] == PARSER_VERSION


def test_dev_window_keeps_opencaselaw_publication_date_when_entscheidsuche_lists_it(tmp_path, repo):
    raw = tmp_path / "raw"
    write_parquet(raw, [("8C_1/2025", "2025-10-02", "2025-10-20", SUVA_CASE)])
    client = es_client([es_hit("8C_1/2025", "2025-10-02")], [])

    rep = fetch(repo, raw, DEV, client=client)
    key = "8C_1/2025@2025-10-02"
    assert repo.get_judgement(key)["publication_date"] == "2025-10-20"
    # entscheidsuche has no publication date; it must not wipe the one OpenCaseLaw gave.
    assert rep.sightings[key]["publication_date"] == "2025-10-20"
    assert rep.sightings[key]["sources"] == ["entscheidsuche", "opencaselaw"]
    assert publication_coverage(rep.as_dict())["known_publication_date"]["total"] == 1


def test_entscheidsuche_only_decision_still_has_unknown_publication(tmp_path, repo):
    raw = tmp_path / "raw"
    write_parquet(raw, [("8C_1/2025", "2025-10-02", None, SUVA_CASE)])
    rep = fetch(repo, raw, DEV, client=es_client([es_hit("8C_3/2025", "2025-10-04")], []))
    assert rep.sightings["8C_3/2025@2025-10-04"]["publication_date"] is None


def test_dev_window_keeps_publication_date_without_entscheidsuche(tmp_path, repo):
    raw = tmp_path / "raw"
    write_parquet(raw, [("8C_1/2025", "2025-10-02", "2025-10-20", SUVA_CASE)])
    rep = fetch(repo, raw, DEV, use_entscheidsuche=False)
    assert rep.sightings["8C_1/2025@2025-10-02"]["publication_date"] == "2025-10-20"


def test_missing_snapshot_is_a_visible_source_failure(tmp_path, repo, monkeypatch):
    import suva.sources.opencaselaw as ocl

    def refuse(*a, **kw):
        raise OSError("offline")

    monkeypatch.setattr(ocl, "download_bger", refuse)
    rep = fetch(repo, tmp_path / "raw", DEV, use_entscheidsuche=False)
    assert rep.source_runs["opencaselaw"]["status"] == "failed"
    assert rep.errors == ["opencaselaw: offline"] and rep.sightings == {}


def test_coverage_uses_only_the_latest_fetch_run(repo):
    first = {"source_runs": {"opencaselaw": {"status": "complete", "ranges": [["2025-10-06", "2025-10-12"]]}},
             "sightings": {"8C_1/2025@2025-10-07": {"decision_date": "2025-10-07", "publication_date": None,
                                                    "sources": ["opencaselaw"], "has_text": True}}}
    second = {"source_runs": {"opencaselaw": {"status": "complete", "ranges": [["2025-10-13", "2025-10-19"]]}},
              "sightings": {}}
    repo.append_audit(AuditEntry("fetch_run", "pipeline", first))
    repo.append_audit(AuditEntry("fetch_run", "pipeline", second))
    weeks = coverage_by_week(repo, "2025-10-06", "2025-10-19")
    # Documented behaviour ("use the latest run ledger"): the week fetched by the earlier run now
    # shows as unqueried with no sightings, although the KB holds the decision.
    assert weeks[0] == {"week": "2025-10-06", "queries": {"opencaselaw": "unqueried"}}
    assert weeks[1]["queries"] == {"opencaselaw": "queried"}


def test_coverage_with_no_run_falls_back_to_the_database(repo):
    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 7), text=SUVA_CASE))
    weeks = coverage_by_week(repo, "2025-10-01", "2025-10-31")
    assert weeks == [{"week": "2025-10-06", "total": 1, "opencaselaw": 1, "only_opencaselaw": 1}]


def test_coverage_rejects_reversed_range(repo):
    repo.append_audit(AuditEntry("fetch_run", "pipeline", {"source_runs": {}, "sightings": {}}))
    with pytest.raises(ValueError, match="on or after"):
        coverage_by_week(repo, "2025-10-19", "2025-10-06")


def test_parse_reports_picks_that_the_dataset_ignores(repo):
    key = "8C_2/2025@2025-10-02"
    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_2/2025", date(2025, 10, 2), text=None))
    repo.upsert_known_positive(dict(ref=key, docket="8C_2/2025", court="bger", in_xlsx=0, in_exports=1))
    # NOTE: parse_all treats every label as a pick; dataset.match_labels only counts in_xlsx labels.
    # Both are "the same dated matching rule", but they disagree on which labels count.
    assert parse_all(repo).picks_not_ok == [f"{key} (no_text)"]
    assert match_labels(repo, repo.list_judgements()).keys == set()


def test_parse_on_empty_kb_reports_zero(repo):
    rep = parse_all(repo)
    assert (rep.judgements, rep.parsed, rep.status) == (0, 0, {})
    assert repo.list_audit(kind="parse_run")[0]["payload"]["judgements"] == 0


def test_parse_single_unknown_key_is_a_no_op(repo):
    assert parse_all(repo, key="8C_404/2025@2025-10-02").judgements == 0


def test_judgement_without_text_parses_as_failed(repo):
    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2), text=None))
    assert parse_all(repo).status == {"failed": 1}


def test_scoring_unparsed_and_unknown_keys(repo):
    key = "8C_1/2025@2025-10-02"
    repo.upsert_judgement(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2), text=SUVA_CASE))
    rep = score_keys(repo, [key, "nope@2025-01-01"], load_rules())
    assert rep.scored == 1 and rep.not_parsed == 1  # unknown key skipped, unparsed one scored on metadata
    assert repo.get_score(key)["parser_version"] is None


def test_score_window_on_empty_kb(repo):
    rep = score_window(repo, DEV, load_rules())
    assert rep.scored == 0 and rep.by_band == {}


def test_import_supplied_needs_both_inputs(tmp_path, repo):
    with pytest.raises(ValueError):
        import_supplied(repo, tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        import_supplied(repo, tmp_path / "empty")


def test_fetch_survives_a_snapshot_row_without_a_docket(tmp_path, repo):
    raw = tmp_path / "raw"
    write_parquet(raw, [(None, "2025-10-02", None, SUVA_CASE), ("8C_1/2025", "2025-10-03", None, SUVA_CASE)])
    rep = fetch(repo, raw, DEV, use_entscheidsuche=False)
    assert rep.errors == [] and list(rep.sightings) == ["8C_1/2025@2025-10-03"]
    assert repo.list_audit(kind="fetch_run")  # the run ledger is written


def test_unusable_text_does_not_count_as_having_text(repo):
    from suva.fetch import _FetchRun
    from test_quality import ERROR

    run = _FetchRun(repo, DEV, include_picks=False)
    run.record(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2), text=ERROR), in_window=True)
    assert repo.get_judgement("8C_1/2025@2025-10-02")["text"] is None  # the KB rejects the error page
    assert run.decisions["8C_1/2025@2025-10-02"].has_text is False  # and so does the run report
    run.record(SourceRecord("opencaselaw", "a", "8C_1/2025", date(2025, 10, 2), text=SUVA_CASE), in_window=True)
    assert run.decisions["8C_1/2025@2025-10-02"].has_text is True
