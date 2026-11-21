import pytest

from freezewatch import eth
from freezewatch.util import HttpError, KeyPool, key_pool, looks_rate_limited


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_round_robin_and_single_key():
    p = KeyPool("a, b ,c")
    assert [p.next() for _ in range(5)] == ["a", "b", "c", "a", "b"]
    s = KeyPool("only")
    assert [s.next() for _ in range(3)] == ["only"] * 3
    assert KeyPool("").next() is None and len(KeyPool(None)) == 0


def test_cooldown_skips_and_recovers():
    c = Clock()
    p = KeyPool("a,b,c", cooldown=60, clock=c)
    assert p.next() == "a"
    p.mark_bad("b")
    assert [p.next() for _ in range(3)] == ["c", "a", "c"]  # b is skipped while cooling
    assert p.cooling() == 1
    c.t += 61
    assert p.cooling() == 0 and "b" in [p.next() for _ in range(3)]


def test_all_cooling_returns_soonest():
    c = Clock()
    p = KeyPool("a,b", clock=c)
    p.mark_bad("a", 30)
    c.t += 5
    p.mark_bad("b", 100)
    assert p.next() == "a"  # recovers first


def test_labels_never_expose_values():
    p = KeyPool("secret-1,secret-2")
    assert p.label("secret-2") == "key#2" and "secret" not in p.label("secret-1")
    assert p.label("unknown") == "no-key"


def test_env_pool_reload(monkeypatch):
    monkeypatch.setenv("X_TEST_KEYS", "k1,k2")
    assert len(key_pool("X_TEST_KEYS")) == 2
    monkeypatch.setenv("X_TEST_KEYS", "k1")
    assert len(key_pool("X_TEST_KEYS")) == 1


def test_rate_limit_detection():
    assert looks_rate_limited("Max rate limit reached, please use API Key for higher rate limit")
    assert looks_rate_limited("Invalid API Key") and looks_rate_limited({"Error": "HTTP 429"})
    assert not looks_rate_limited("OK")


def test_etherscan_rotates_on_rate_limit(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "AAA,BBB,CCC")
    monkeypatch.setattr(eth.time, "sleep", lambda s: None)
    used = []

    def fake_http(method, url, params=None, **kw):
        used.append(params["apikey"])
        if params["apikey"] == "AAA":
            return {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
        return {"status": "1", "message": "OK", "result": [1]}
    monkeypatch.setattr(eth, "http_json", fake_http)
    assert eth._es({"module": "logs"})["result"] == [1]
    assert used == ["AAA", "BBB"]
    assert key_pool("ETHERSCAN_API_KEY").cooling() == 1
    used.clear()
    eth._es({"module": "logs"})
    assert "AAA" not in used  # still cooling


def test_etherscan_missing_key_and_exhaustion(monkeypatch):
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    with pytest.raises(HttpError):
        eth._es({})
    monkeypatch.setenv("ETHERSCAN_API_KEY", "ONE")
    monkeypatch.setattr(eth.time, "sleep", lambda s: None)
    monkeypatch.setattr(eth, "http_json", lambda *a, **k: {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"})
    with pytest.raises(HttpError):
        eth._es({})
