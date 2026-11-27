import json
import re
from datetime import date

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from suva.fetch import fetch
from suva.http import PoliteClient
from suva.windows import Window

COLS = ("decision_id", "docket_number", "decision_date", "publication_date", "language",
        "chamber", "source_url", "full_text", "has_full_text", "scraped_at")


def parquet(raw, rows):
    p = raw / "opencaselaw" / "bger.parquet"
    p.parent.mkdir(parents=True)
    full = [dict(decision_id=f"bger_{d}", docket_number=d, decision_date=dt, publication_date=None,
                 language="de", chamber=None, source_url=None, full_text="Text " + d, has_full_text=True,
                 scraped_at=None) for d, dt in rows]
    pq.write_table(pa.table({k: [r[k] for r in full] for k in COLS}), p)


def es_client(hits_by_day, html_calls):
    def handler(req):
        if req.url.path.endswith(".html"):
            html_calls.append(str(req.url))
            # realistic page: docket in the header, a reasoning section
            m = re.search(r"_(\d+C)-(\d+)-(\d{4})_", req.url.path)
            docket = f"{m[1]}_{m[2]}/{m[3]}"
            return httpx.Response(200, text=(f"<div class='para'>{docket}</div><div class='para'>Erwägungen:</div>"
                                             "<div class='para'>1.</div><div class='para'>Gründe.</div>"))
        rng = json.loads(req.content)["query"]["bool"]["filter"][1]["range"]["date"]
        hits = [h for d, h in hits_by_day if rng["gte"] <= d <= rng["lte"]]
        return httpx.Response(200, json={"hits": {"total": {"value": len(hits)}, "hits": hits}})
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def es_hit(docket, d):
    sid = f"CH_BGer_008_{docket.replace('_', '-').replace('/', '-')}_{d}"
    return d, {"_id": sid, "_source": {"date": d, "reference": [docket], "language": "de",
               "attachment": {"content_url": f"https://entscheidsuche.ch/docs/CH_BGer/{sid}.html"}}}


def test_fetch_window_picks_gaps_and_html(tmp_path, repo):
    raw = tmp_path / "raw"
    # OCL: two decisions in window + one old pick outside it
    parquet(raw, [("8C_1/2025", "2025-10-02"), ("8C_2/2025", "2025-10-03"), ("8C_9/2024", "2024-05-05")])
    repo.upsert_known_positive(dict(ref="8C_9/2024", docket="8C_9/2024", court="bger", in_xlsx=0, in_exports=1))
    # entscheidsuche: has 8C_1 and a decision OCL lacks (gap), plus the old pick
    html_calls = []
    client = es_client([es_hit("8C_1/2025", "2025-10-02"), es_hit("8C_3/2025", "2025-10-04"),
                        es_hit("8C_9/2024", "2024-05-05")], html_calls)

    rep = fetch(repo, raw, Window("dev", date(2025, 10, 1), date(2025, 10, 7)), client=client)

    assert (rep.opencaselaw, rep.entscheidsuche) == (2, 2)
    assert (rep.picks_requested, rep.picks_found, rep.picks_missing) == (1, 1, [])
    assert rep.new_judgements == 4 and rep.html_fetched == 1 and rep.errors == []
    gap = repo.get_judgement("8C_3/2025@2025-10-04")
    # listed by entscheidsuche only: text taken from its HTML instead of being left empty
    assert rep.text_fallback == ["8C_3/2025@2025-10-04"] and rep.still_without_text == []
    assert gap["text_source"] == "entscheidsuche" and "Erwägungen:\n\n1.\n\nGründe." in gap["text"]
    pick = repo.get_judgement("8C_9/2024@2024-05-05")
    assert pick["text"] and pick["html_path"]
    assert len(html_calls) == 2

    assert repo.list_audit(kind="fetch_run")[0]["payload"]["window"] == "dev"


def test_fetch_without_entscheidsuche(tmp_path, repo):
    raw = tmp_path / "raw"
    parquet(raw, [("8C_1/2025", "2025-10-02")])
    rep = fetch(repo, raw, Window("dev", date(2025, 10, 1), date(2025, 10, 7)), use_entscheidsuche=False)
    assert rep.opencaselaw == 1 and rep.entscheidsuche == 0


def test_challenge_uses_publication_dates_and_separates_unknown(tmp_path, repo):
    raw = tmp_path / "raw"
    parquet(raw, [("8C_1/2025", "2025-01-01"), ("8C_2/2025", "2025-07-20"), ("8C_3/2025", "2025-07-21")])
    path = raw / "opencaselaw" / "bger.parquet"
    table = pq.read_table(path)
    table = table.set_column(table.schema.get_field_index("publication_date"), "publication_date",
                             pa.array(["2025-10-01", "2025-07-25", None]))
    pq.write_table(table, path)
    window = Window("challenge", date(2025, 7, 1), date(2025, 10, 10), date(2025, 8, 1))
    rep = fetch(repo, raw, window, use_entscheidsuche=False, include_picks=False)
    assert not rep.errors
    assert set(rep.sightings) == {"8C_1/2025@2025-01-01", "8C_3/2025@2025-07-21"}
    assert rep.publication_unknown == 1


def test_pick_uses_exact_date_and_queries_missing_pick(tmp_path, repo):
    raw = tmp_path / "raw"
    docket = "8C_1/2025"
    parquet(raw, [(docket, "2025-02-02")])
    ref = f"{docket}@2025-02-03"
    repo.upsert_known_positive(dict(ref=ref, docket=docket, court="bger", in_xlsx=1, in_exports=0))
    window = Window("dev", date(2025, 10, 1), date(2025, 10, 7))
    absent = fetch(repo, raw, window, use_entscheidsuche=False)
    assert absent.picks_missing == [ref]
    client = es_client([es_hit(docket, "2025-02-03")], [])
    listed = fetch(repo, raw, window, html_for_picks=False, text_fallback=False, client=client)
    assert listed.picks_listed == 1 and listed.picks_found == 0
    assert listed.picks_without_text == [ref]
    # with the fallback on, the pick gets its text from entscheidsuche instead
    filled = fetch(repo, raw, window, html_for_picks=False, client=es_client([es_hit(docket, "2025-02-03")], []))
    assert filled.text_fallback == [ref] and filled.picks_found == 1 and filled.picks_without_text == []


def test_block_during_listing_prevents_html_requests(tmp_path, repo):
    raw = tmp_path / "raw"
    docket = "8C_1/2025"
    parquet(raw, [(docket, "2025-10-02")])
    repo.upsert_known_positive(dict(ref=docket, docket=docket, court="bger", in_xlsx=1, in_exports=0))
    window = Window("dev", date(2025, 10, 1), date(2025, 10, 14))
    calls = []

    def handler(request):
        calls.append(request.method)
        if len(calls) == 1:
            _, hit = es_hit(docket, "2025-10-02")
            return httpx.Response(200, json={"hits": {"total": {"value": 1}, "hits": [hit]}})
        return httpx.Response(403)

    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)
    rep = fetch(repo, raw, window, client=client)
    assert calls == ["POST", "POST"]
    assert rep.source_runs["entscheidsuche"]["status"] == "blocked"


def test_error_page_text_replaced_by_entscheidsuche(tmp_path, repo):
    raw = tmp_path / "raw"
    p = raw / "opencaselaw" / "bger.parquet"
    p.parent.mkdir(parents=True)
    error_page = "menu\n\nListe der Neuheiten\n\nDokument nicht gefunden Dieser AZA-Entscheid ist in elektronischer Form nicht verfügbar."
    row = dict(decision_id="bger_8C_5", docket_number="8C_5/2026", decision_date="2026-10-01", publication_date=None,
               language="fr", chamber=None, source_url=None, full_text=error_page, has_full_text=True, scraped_at=None)
    pq.write_table(pa.table({k: [row[k]] for k in COLS}), p)
    # a previous run stored the error page as text
    from suva.sources.base import SourceRecord
    repo.upsert_judgement(SourceRecord(source="opencaselaw", source_id="old", docket="8C_5/2026",
                                       decision_date=date(2026, 10, 1), text=error_page))
    with repo.conn:
        repo.conn.execute("UPDATE judgements SET text = ? WHERE key = ?", (error_page, "8C_5/2026@2026-10-01"))
    client = es_client([es_hit("8C_5/2026", "2026-10-01")], [])

    rep = fetch(repo, raw, Window("dev", date(2026, 10, 1), date(2026, 10, 7)), client=client, include_picks=False)

    j = repo.get_judgement("8C_5/2026@2026-10-01")
    assert rep.unusable_text_cleared == ["8C_5/2026@2026-10-01"]
    assert rep.text_fallback == ["8C_5/2026@2026-10-01"]
    assert j["text_source"] == "entscheidsuche" and "Dokument nicht gefunden" not in j["text"]


def test_error_page_without_fallback_is_reported(tmp_path, repo):
    raw = tmp_path / "raw"
    parquet(raw, [("8C_1/2025", "2025-10-02")])
    rep = fetch(repo, raw, Window("dev", date(2025, 10, 1), date(2025, 10, 7)),
                use_entscheidsuche=False, include_picks=False)
    assert rep.still_without_text == []  # OCL text was fine


def test_pick_attachment_is_read_once_when_it_also_provides_text(tmp_path, repo):
    raw = tmp_path / "raw"
    parquet(raw, [("8C_1/2025", "2025-10-02")])
    docket = "8C_2/2025"
    repo.upsert_known_positive(dict(ref=docket, docket=docket, court="bger", in_xlsx=1, in_exports=0))
    calls = []
    client = es_client([es_hit(docket, "2025-10-02")], calls)
    report = fetch(repo, raw, Window("dev", date(2025, 10, 1), date(2025, 10, 7)), client=client)
    assert len(calls) == report.html_fetched == report.picks_found == 1
    assert report.text_fallback == ["8C_2/2025@2025-10-02"]
    assert not report.errors


def test_source_failure_can_still_complete_other_listing(tmp_path, repo):
    bad = tmp_path / "raw" / "opencaselaw" / "bger.parquet"
    bad.parent.mkdir(parents=True)
    bad.write_text("not parquet")
    client = es_client([es_hit("8C_2/2025", "2025-10-02")], [])
    report = fetch(repo, tmp_path / "raw", Window("dev", date(2025, 10, 1), date(2025, 10, 7)), client=client)
    assert report.source_runs["opencaselaw"]["status"] == "failed"
    assert report.source_runs["entscheidsuche"]["status"] == "complete"
    assert report.entscheidsuche == 1 and len(report.errors) == 1


def test_fetch_cli_requires_both_custom_dates(capsys):
    import pytest

    from suva.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["fetch", "--start", "2025-10-01"])
    assert exc.value.code == 2
    assert "requires both" in capsys.readouterr().err


def test_docket_only_pick_with_two_decisions_is_ambiguous(tmp_path, repo):
    docket = "8C_1/2025"
    raw = tmp_path / "raw"
    parquet(raw, [(docket, "2025-10-02"), (docket, "2025-10-03")])
    repo.upsert_known_positive(dict(ref=docket, docket=docket, court="bger", in_xlsx=1, in_exports=0))
    report = fetch(repo, raw, Window("dev", date(2025, 10, 1), date(2025, 10, 7)), use_entscheidsuche=False)
    assert report.picks_ambiguous == [docket]
    assert report.picks_found == report.picks_listed == 0


def test_attachment_refusal_preserves_listings_and_stops_attachment_fetches(tmp_path, repo):
    raw = tmp_path / "raw"
    parquet(raw, [("8C_1/2025", "2025-10-02")])
    hits = [es_hit(f"8C_{i}/2025", "2025-10-02")[1] for i in (2, 3)]
    html_requests = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"hits": {"total": {"value": 2}, "hits": hits}})
        html_requests.append(request)
        return httpx.Response(403)

    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)
    report = fetch(repo, raw, Window("dev", date(2025, 10, 1), date(2025, 10, 7)), client=client)
    assert len(html_requests) == 1
    assert report.entscheidsuche == 2
    assert report.still_without_text == ["8C_2/2025@2025-10-02", "8C_3/2025@2025-10-02"]
    assert report.source_runs["entscheidsuche"]["status"] == "blocked"
