import httpx
import pytest

from suva.http import USER_AGENT, Blocked, PoliteClient


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def client(handler, **kw):
    t = FakeTime()
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=t.sleep, clock=t.clock, **kw), t


def test_honest_user_agent():
    seen = []
    c, _ = client(lambda r: seen.append(r.headers["user-agent"]) or httpx.Response(200))
    c.get("https://a.test/x")
    assert seen == [USER_AGENT] and "Mozilla" not in USER_AGENT


def test_min_interval_per_host():
    c, t = client(lambda r: httpx.Response(200))
    c.get("https://a.test/1")
    c.get("https://a.test/2")
    c.get("https://b.test/1")  # other host: no wait
    assert t.sleeps == [2.0]


def test_retry_on_503_then_success():
    calls = iter([httpx.Response(503), httpx.Response(200, text="ok")])
    c, t = client(lambda r: next(calls))
    assert c.get("https://a.test/").text == "ok"
    assert len(t.sleeps) >= 1


def test_403_raises_blocked_without_retry():
    n = []
    c, _ = client(lambda r: n.append(1) or httpx.Response(403))
    with pytest.raises(Blocked):
        c.get("https://a.test/")
    assert len(n) == 1


def test_404_raises():
    c, _ = client(lambda r: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        c.get("https://a.test/")
