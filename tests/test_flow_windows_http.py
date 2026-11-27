"""Edges of the date windows, polite HTTP client, entscheidsuche listing and small helpers."""

import json
from datetime import date, timedelta

import httpx
import pytest

from suva.hashing import sha256
from suva.http import Blocked, PoliteClient
from suva.sources.base import SourceRecord, decision_id, parse_iso_date
from suva.sources.entscheidsuche import SourceResponseError, list_bger, week_ranges
from suva.windows import DEV_END, DEV_START, Window, challenge_window, dev_window, months_back
from test_entscheidsuche import hit, make_client

# --- windows -----------------------------------------------------------------------------------

def test_publication_window_is_inclusive_at_both_ends():
    w = challenge_window(date(2026, 11, 27))
    assert w.published_from == date(2026, 8, 27) and w.end == date(2026, 11, 27)
    assert w.includes_publication(w.published_from) and w.includes_publication(w.end)
    assert not w.includes_publication(w.published_from - timedelta(days=1))
    assert not w.includes_publication(w.end + timedelta(days=1))


def test_unknown_publication_is_always_included():
    assert challenge_window(date(2026, 11, 27)).includes_publication(None)
    assert dev_window().includes_publication(date(1990, 1, 1))  # no publication filter at all


def test_challenge_window_widens_decision_dates_by_75_days():
    w = challenge_window(date(2026, 11, 27))
    assert w.start == date(2026, 8, 27) - timedelta(days=75)


def test_dev_window_edges():
    w = dev_window()
    assert (w.start, w.end, w.published_from) == (DEV_START, DEV_END, None)


def test_single_day_window_is_allowed():
    w = Window("one", date(2026, 1, 1), date(2026, 1, 1))
    assert str(w) == "one: decisions 2026-01-01 .. 2026-01-01"


@pytest.mark.parametrize("d, months, expected", [
    (date(2024, 5, 31), 3, date(2024, 2, 29)),  # leap year: clamps to the 29th
    (date(2025, 5, 31), 3, date(2025, 2, 28)),
    (date(2026, 1, 15), 1, date(2025, 12, 15)),  # crosses the year
    (date(2026, 3, 10), 0, date(2026, 3, 10)),
    (date(2026, 3, 10), 12, date(2025, 3, 10)),
])
def test_months_back(d, months, expected):
    assert months_back(d, months) == expected


def test_challenge_window_at_month_end_clamps_the_day():
    assert challenge_window(date(2026, 5, 31)).published_from == date(2026, 2, 28)


# --- week_ranges -------------------------------------------------------------------------------

@pytest.mark.parametrize("days, expected", [
    (1, [(0, 0)]),
    (7, [(0, 6)]),
    (8, [(0, 6), (7, 7)]),
    (15, [(0, 6), (7, 13), (14, 14)]),
])
def test_week_ranges_cover_every_day_once(days, expected):
    start = date(2025, 10, 1)
    got = list(week_ranges(start, start + timedelta(days=days - 1)))
    assert got == [(start + timedelta(a), start + timedelta(b)) for a, b in expected]


def test_week_ranges_with_end_before_start_is_empty():
    assert list(week_ranges(date(2025, 10, 2), date(2025, 10, 1))) == []


# --- PoliteClient ------------------------------------------------------------------------------

def test_retry_status_exhausted_raises_http_error():
    calls, sleeps = [], []
    c = PoliteClient(transport=httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(503)),
                     sleep=sleeps.append, retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        c.get("https://example.test/x")
    assert len(calls) == 3  # first try + 2 retries
    assert [s for s in sleeps if s >= 2] == [2, 4]


def test_429_with_large_retry_after_waits_that_long():
    responses = iter([httpx.Response(429, headers={"Retry-After": "30"}), httpx.Response(200, json={"ok": 1})])
    sleeps = []
    c = PoliteClient(transport=httpx.MockTransport(lambda r: next(responses)), sleep=sleeps.append)
    assert c.get("https://example.test/x").json() == {"ok": 1}
    assert 30 in sleeps


def test_retry_after_naive_http_date_falls_back_to_backoff():
    # A Retry-After date without a timezone is ignored; the normal backoff applies.
    responses = iter([httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00"}),
                      httpx.Response(200)])
    sleeps = []
    c = PoliteClient(transport=httpx.MockTransport(lambda r: next(responses)), sleep=sleeps.append)
    c.get("https://example.test/x")
    assert 2.0 in sleeps and max(sleeps) < 60


def test_retries_zero_means_a_single_attempt():
    calls = []
    c = PoliteClient(transport=httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(500)),
                     sleep=lambda s: None, retries=0)
    with pytest.raises(httpx.HTTPStatusError):
        c.get("https://example.test/x")
    assert calls == [1]


def test_post_json_403_raises_blocked_without_retry():
    calls = []
    c = PoliteClient(transport=httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(403)),
                     sleep=lambda s: None)
    with pytest.raises(Blocked, match="403 from example.test"):
        c.post_json("https://example.test/search", {"q": 1})
    assert calls == [1]


def test_post_json_with_non_json_body_raises():
    c = make_client(lambda r: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(json.JSONDecodeError):
        c.post_json("https://example.test/search", {})


# --- entscheidsuche listing -------------------------------------------------------------------

def listing(total, hits, relation="eq"):
    return httpx.Response(200, json={"hits": {"total": {"value": total, "relation": relation}, "hits": hits}})


def test_total_equal_to_page_size_does_not_split():
    queries = []

    def handler(req):
        queries.append(json.loads(req.content))
        return listing(2, [hit(1, "2025-10-01"), hit(2, "2025-10-02")])

    recs = list(list_bger(make_client(handler), date(2025, 10, 1), date(2025, 10, 7), size=2))
    assert len(queries) == 1 and len(recs) == 2


def test_relation_gte_forces_a_split():
    ranges = []

    def handler(req):
        r = json.loads(req.content)["query"]["bool"]["filter"][1]["range"]["date"]
        ranges.append((r["gte"], r["lte"]))
        if len(ranges) == 1:
            return listing(1, [hit(1, "2025-10-01")], relation="gte")
        return listing(0, [])

    list(list_bger(make_client(handler), date(2025, 10, 1), date(2025, 10, 2)))
    assert ranges == [("2025-10-01", "2025-10-02"), ("2025-10-01", "2025-10-01"), ("2025-10-02", "2025-10-02")]


def test_single_day_over_page_size_fails_loudly():
    c = make_client(lambda r: listing(3, [hit(1, "2025-10-01")]))
    with pytest.raises(SourceResponseError, match="exceeds one page"):
        list(list_bger(c, date(2025, 10, 1), date(2025, 10, 1), size=2))


def test_empty_listing_yields_nothing():
    assert list(list_bger(make_client(lambda r: listing(0, [])), date(2025, 10, 1), date(2025, 10, 1))) == []


@pytest.mark.parametrize("body", [{}, {"hits": {"total": {"value": -1}, "hits": []}},
                                  {"hits": {"total": {"value": "3"}, "hits": []}}, [1, 2]])
def test_malformed_listing_is_a_source_error(body):
    c = make_client(lambda r: httpx.Response(200, json=body))
    with pytest.raises(SourceResponseError):
        list(list_bger(c, date(2025, 10, 1), date(2025, 10, 1)))


def test_hit_without_attachment_has_no_html_url():
    h = hit(1, "2025-10-01")
    del h["_source"]["attachment"]
    rec, = list_bger(make_client(lambda r: listing(1, [h])), date(2025, 10, 1), date(2025, 10, 1))
    assert rec.html_url is None and rec.key == "8C_1/2025@2025-10-01"


@pytest.mark.parametrize("change", [{"date": None}, {"reference": []}, {"reference": [" "]}])
def test_hit_without_date_or_docket_is_rejected(change):
    h = hit(1, "2025-10-01")
    h["_source"].update(change)
    with pytest.raises(SourceResponseError, match="Invalid decision"):
        list(list_bger(make_client(lambda r: listing(1, [h])), date(2025, 10, 1), date(2025, 10, 1)))


# --- identity and hashing helpers -------------------------------------------------------------

def test_decision_id_formats():
    assert decision_id("8C_1/2025", date(2025, 10, 2)) == "8C_1/2025@2025-10-02"
    assert decision_id("8C_1/2025", "2025-10-02") == "8C_1/2025@2025-10-02"
    assert decision_id("8C_1/2025", None) == "8C_1/2025"
    assert decision_id("8C_1/2025", "") == "8C_1/2025"


def test_record_key_without_a_decision_date_is_the_bare_docket():
    assert SourceRecord("opencaselaw", "x", "8C_1/2025", None).key == "8C_1/2025"


@pytest.mark.parametrize("value, expected", [
    ("2025-10-02", date(2025, 10, 2)),
    ("2025-10-02T13:00:00Z", date(2025, 10, 2)),
    ("", None), (None, None), ("02.10.2025", None), ("2025-13-01", None),
])
def test_parse_iso_date(value, expected):
    assert parse_iso_date(value) == expected


def test_sha256_is_stable_and_key_order_independent():
    assert sha256({"a": 1, "b": [1, "ä"]}) == sha256({"b": [1, "ä"], "a": 1})
    assert sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256("") != sha256([]) != sha256(None)
