import json
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
            return httpx.Response(200, text="<b>8C_9/2024</b><div class='para'>Erwägungen</div>")
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
    assert gap["text"] is None  # listed by entscheidsuche only
    pick = repo.get_judgement("8C_9/2024@2024-05-05")
    assert pick["text"] and pick["html_path"]
    assert len(html_calls) == 1

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
    listed = fetch(repo, raw, window, html_for_picks=False, client=client)
    assert listed.picks_listed == 1 and listed.picks_found == 0
    assert listed.picks_without_text == [ref]


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
