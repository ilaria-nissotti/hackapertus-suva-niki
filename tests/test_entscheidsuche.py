import json
from datetime import date

import httpx
import pytest

from suva.http import PoliteClient
from suva.sources.entscheidsuche import load_judgement, list_bger


def hit(i, d, refs=None):
    return {"_id": f"CH_BGer_008_8C-{i}-2025_{d}", "_source": {
        "date": d, "reference": refs or [f"8C_{i}/2025"], "language": "fr",
        "title": {"de": f"Bundesgericht ... {d} 8C_{i}/2025"},
        "attachment": {"content_url": f"https://entscheidsuche.ch/docs/CH_BGer/CH_BGer_008_8C-{i}-2025_{d}.html"}}}


def make_client(handler):
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_weekly_windows_and_fields():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body["query"]["bool"]["filter"][1]["range"]["date"])
        g = body["query"]["bool"]["filter"][1]["range"]["date"]["gte"]
        return httpx.Response(200, json={"hits": {"total": {"value": 1}, "hits": [hit(len(bodies), g)]}})

    recs = list(list_bger(make_client(handler), date(2025, 10, 1), date(2025, 10, 14)))
    assert [b["gte"] for b in bodies] == ["2025-10-01", "2025-10-08"]
    assert bodies[1]["lte"] == "2025-10-14"
    r = recs[0]
    assert (r.source, r.docket, r.decision_date, r.language) == ("entscheidsuche", "8C_1/2025", date(2025, 10, 1), "fr")
    assert r.html_url.endswith(".html")


def test_splits_when_over_page_size():
    def handler(req):
        rng = json.loads(req.content)["query"]["bool"]["filter"][1]["range"]["date"]
        if rng["gte"] != rng["lte"]:
            return httpx.Response(200, json={"hits": {"total": {"value": 5}, "hits": []}})
        return httpx.Response(200, json={"hits": {"total": {"value": 1}, "hits": [hit(1, rng["gte"])]}})

    recs = list(list_bger(make_client(handler), date(2025, 10, 1), date(2025, 10, 4), size=2))
    assert sorted(r.decision_date.day for r in recs) == [1, 2, 3, 4]


def test_joined_cases_yield_one_record_per_docket():
    def handler(req):
        return httpx.Response(200, json={"hits": {"total": {"value": 1}, "hits": [
            hit(1, "2025-10-01", refs=["8C_1/2025", "8C_2/2025"])]}})

    recs = list(list_bger(make_client(handler), date(2025, 10, 1), date(2025, 10, 1)))
    assert [r.docket for r in recs] == ["8C_1/2025", "8C_2/2025"]


def test_api_error_raises():
    c = make_client(lambda r: httpx.Response(200, json={"error": {"reason": "bad"}}))
    with pytest.raises(RuntimeError, match="search error"):
        list(list_bger(c, date(2025, 10, 1), date(2025, 10, 1)))


def test_load_judgement_cached(tmp_path):
    calls = []
    c = make_client(lambda r: calls.append(1) or httpx.Response(200, text="<b>8C_1/2025</b><div class='para'>Erwägungen:</div>"))
    rec = next(iter(list_bger(make_client(lambda r: httpx.Response(200, json={
        "hits": {"total": {"value": 1}, "hits": [hit(1, "2025-10-01")]}})), date(2025, 10, 1), date(2025, 10, 1))))
    assert load_judgement(c, rec, tmp_path) == load_judgement(c, rec, tmp_path)
    assert len(calls) == 1


def test_invalid_html_is_never_cached(tmp_path):
    from suva.sources.base import SourceRecord

    rec = SourceRecord("entscheidsuche", "sample", "8C_1/2025", date(2025, 10, 1),
                       html_url="https://entscheidsuche.ch/sample.html")
    client = make_client(lambda _: httpx.Response(200, text="<p>Service unavailable</p>"))
    with pytest.raises(ValueError, match="expected judgement"):
        load_judgement(client, rec, tmp_path)
    assert not list(tmp_path.iterdir())


def test_incomplete_listing_is_reported():
    from suva.sources.entscheidsuche import SourceResponseError

    http = make_client(lambda _: httpx.Response(200, json={
        "hits": {"total": {"value": 2}, "hits": [hit(1, "2025-10-01")]},
    }))
    with pytest.raises(SourceResponseError, match="Incomplete listing"):
        list(list_bger(http, date(2025, 10, 1), date(2025, 10, 1)))


def test_valid_short_order_replaces_invalid_cache(tmp_path):
    from suva.sources.base import SourceRecord

    rec = SourceRecord("entscheidsuche", "sample", "8C_1/2025", date(2025, 10, 1),
                       html_url="https://entscheidsuche.ch/sample.html")
    path = tmp_path / "sample.html"
    path.write_text("<p>Service unavailable</p>")
    html = ("<div class='para'>8C_1/2025</div><div class='para'>In Erwägung,</div>"
            "<div class='para'>dass die Beschwerde unzulässig ist,</div>"
            "<div class='para'>erkennt die Präsidentin:</div>")
    http = make_client(lambda _: httpx.Response(200, text=html))
    document = load_judgement(http, rec, tmp_path)
    assert document.path == path and path.read_text() == html
    assert "In Erwägung," in document.text


def test_attachment_url_and_redirect_are_restricted(tmp_path):
    from dataclasses import replace

    from suva.sources.base import SourceRecord

    requests = []

    def redirect(request):
        requests.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://other.test/redirect"})

    rec = SourceRecord("entscheidsuche", "sample", "8C_1/2025", date(2025, 10, 1),
                       html_url="https://entscheidsuche.ch/sample.html")
    http = make_client(redirect)
    with pytest.raises(ValueError, match="HTTPS entscheidsuche"):
        load_judgement(http, replace(rec, html_url="https://other.test/sample.html"), tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        load_judgement(http, rec, tmp_path)
    assert requests == ["entscheidsuche.ch"]


def test_listing_rejects_string_instead_of_reference_list():
    http = make_client(lambda _: httpx.Response(200, json={
        "hits": {"total": {"value": 1}, "hits": [hit(1, "2025-10-01", refs="8C_1/2025")]},
    }))
    with pytest.raises(RuntimeError, match="Invalid decision"):
        list(list_bger(http, date(2025, 10, 1), date(2025, 10, 1)))
