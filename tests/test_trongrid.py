import json
import time

import pytest

from freezewatch import signals, status, trongrid
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import HttpError, iso, tron_from_hex20

NOW = int(time.time())


class FakeResp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b, self.text = code, body if body is not None else {"ok": True}, "x"

    def json(self):
        return self._b


class FakeSession:
    """Records the TRON-PRO-API-KEY of every request; `script` = list of status codes (cycled) per call."""

    def __init__(self, script=(200,)):
        self.keys, self.script, self.n = [], list(script), 0

    def request(self, method, url, **kw):
        self.keys.append(kw["headers"].get("TRON-PRO-API-KEY"))
        code = self.script[self.n % len(self.script)]
        self.n += 1
        return FakeResp(code)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("TRONGRID_API_KEY", "aaaa,bbbb,cccc")
    monkeypatch.setattr(trongrid, "throttle", lambda *a, **k: None)
    trongrid.reset_usage()
    from freezewatch import util
    util._pools.clear()   # fresh pools (cooldowns of one test must not leak into the next)


def test_rotation_spreads_requests_over_all_keys_and_never_goes_keyless():
    s = FakeSession()
    for _ in range(30):
        trongrid.request("GET", "/x", session=s)
    assert None not in s.keys                                   # keys exist: never a keyless request
    counts = {k: s.keys.count(k) for k in set(s.keys)}
    assert set(counts) == {"aaaa", "bbbb", "cccc"} and set(counts.values()) == {10}
    rep = trongrid.usage_report()
    assert "key#1 10 req" in rep and "key#2 10 req" in rep and "key#3 10 req" in rep and "aaaa" not in rep   # counts, never values


def test_429_puts_the_key_on_cooldown_and_the_next_key_is_used():
    s = FakeSession([429, 200])
    sleeps = []
    o = trongrid.request("GET", "/x", session=s, sleep=lambda x: sleeps.append(x))
    assert o == {"ok": True} and s.keys[0] != s.keys[1]         # the 429 key is skipped for the retry
    assert "(1 x 429)" in trongrid.usage_report()


def test_all_keys_cooling_waits_instead_of_hammering_and_is_bounded():
    s = FakeSession([429])
    sleeps = []
    with pytest.raises(HttpError, match="retries exhausted"):
        trongrid.request("GET", "/x", session=s, sleep=lambda x: sleeps.append(x), max_sec=30)
    assert s.n <= 6 + 3 * 3 and sleeps                            # bounded attempts, waited between rounds


def test_tronscan_rows_are_normalised_deduped_and_transfer_only():
    A = "TCe9CjSScVDhaTjQm81yGAEhqqU8Wcmqq1"
    rows = [{"transaction_id": "t1", "from_address": A, "to_address": "Tb", "quant": "5000000", "block_ts": 1000, "event_type": "Transfer"},
            {"transaction_id": "t1", "from_address": A, "to_address": "Tb", "quant": "5000000", "block_ts": 1000, "event_type": "Transfer"},
            {"transaction_id": "t2", "from_address": A, "to_address": "Tc", "quant": "7", "block_ts": 2000, "event_type": "Approval"},
            {"transaction_id": "t3", "from_address": "Tz", "to_address": A, "quant": "9000000", "block_ts": 3000, "event_type": "Transfer"}]
    out = trongrid.tronscan_transfers(A, "out", 0, get=lambda p: {"token_transfers": rows})
    assert [(r["transaction_id"], r["to"], r["value"]) for r in out] == [("t1", "Tb", "5000000")]   # dup, Approval and inbound dropped
    inn = trongrid.tronscan_transfers(A, "in", 0, get=lambda p: {"token_transfers": rows})
    assert [r["transaction_id"] for r in inn] == ["t3"]


def test_transfers_fall_back_to_tronscan_only_on_trongrid_failure():
    A = "TCe9CjSScVDhaTjQm81yGAEhqqU8Wcmqq1"
    scan = {"token_transfers": [{"transaction_id": "s1", "from_address": "Tz", "to_address": A, "quant": "1000000", "block_ts": 5,
                                 "event_type": "Transfer"}]}

    def grid_ok(params):
        return {"data": [{"type": "Transfer", "value": "2000000", "from": "Ty", "to": A, "block_timestamp": 9, "transaction_id": "g1"}], "meta": {}}

    def grid_down(params):
        raise HttpError("saturated")
    rows, src = trongrid.transfers(A, "in", 0, grid_get=grid_ok, scan_get=lambda p: scan)
    assert src == "trongrid" and rows[0]["transaction_id"] == "g1"
    rows, src = trongrid.transfers(A, "in", 0, grid_get=grid_down, scan_get=lambda p: scan)
    assert src == "tronscan" and rows[0]["transaction_id"] == "s1"
    with pytest.raises(trongrid.Stopped):                         # a deadline stop is never answered by the fallback source
        trongrid.transfers(A, "in", 0, grid_get=grid_ok, scan_get=lambda p: scan, stop=lambda: True)


def test_signal_check_failures_are_retried_not_skipped(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    addr = "0x" + "44" * 20
    ensure_proposal(conn, "eth", 1)
    update_proposal(conn, "eth", 1, destination="x", function="addBlackList", target=addr, executed=1, executed_at=iso(NOW - 300),
                    submitted_at=iso(NOW - 600), status="executed")
    state = {"tron_up": False}

    def chk(chain, a):
        if chain == "tron" and not state["tron_up"]:
            raise HttpError("TronGrid saturated")
        return (False, 9000.0)
    assert signals.detect(conn, checker=chk, now=NOW, chains=["pol", "tron"]) == 1      # pol is already a signal
    assert signals.pending_checks(conn) == 1                                              # tron is pending, NOT marked checked
    state["tron_up"] = True
    signals.detect(conn, checker=chk, now=NOW + 600, chains=["pol", "tron"])
    assert signals.pending_checks(conn) == 0
    risk = {e[0] for e in signals.payload(conn)["open"][0]["at_risk"]}
    assert risk == {"pol", "tron"}                                                        # the late chain was added to the same signal
    assert signals.detect(conn, checker=chk, now=NOW + 900, chains=["pol", "tron"]) == 0  # and nothing is re-checked afterwards


def test_detect_lag_median_over_recent_proposals(tmp_path):
    conn = connect(tmp_path / "d.sqlite")
    for i, lag in enumerate([30, 60, 90, 120, 600]):
        ensure_proposal(conn, "tron", i)
        update_proposal(conn, "tron", i, submitted_at=iso(NOW - 1000), status="pending")
        conn.execute("UPDATE proposals SET first_seen_at=? WHERE chain='tron' AND tx_id=?", (iso(NOW - 1000 + lag), i))
    d = status.detect_lag(conn)
    assert d["n"] == 5 and d["median_s"] == 90 and d["p90_s"] == 600
    assert status.detect_lag(conn, chain="eth") is None
