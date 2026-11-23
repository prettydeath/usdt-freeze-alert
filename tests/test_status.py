import json

from freezewatch import livecheck as LC
from freezewatch import status, tokenevents
from freezewatch.db import connect, meta_set


def mk(tmp_path):
    return connect(tmp_path / "t.sqlite")


def test_sanitize_strips_keys_urls_and_tokens():
    s = status.sanitize("HTTP 403 https://api.etherscan.io/v2/api?apikey=ABCDEF123&x=1 token=abc123 "
                        "0123456789abcdef0123456789abcdef0123456789")
    assert "ABCDEF123" not in s and "abc123" not in s and "?" not in s
    assert "0123456789abcdef0123456789abcdef0123456789" not in s
    assert len(status.sanitize("x" * 500)) <= 160
    assert status.sanitize(None) == ""
    assert "0x" + "ab" * 20 in status.sanitize("target 0x" + "ab" * 20)  # addresses are kept


def test_record_streak_and_status(tmp_path):
    c = mk(tmp_path)
    status.record(c, "eth", True, rows_added=3)
    r = dict(c.execute("SELECT * FROM source_health WHERE source='eth'").fetchone())
    assert r["consec_fail"] == 0 and r["rows_added"] == 3 and r["last_ok"]
    for i in range(3):
        status.record(c, "eth", False, error=f"boom {i} https://x.io/a?key=SECRET")
    r = dict(c.execute("SELECT * FROM source_health WHERE source='eth'").fetchone())
    assert r["consec_fail"] == 3 and "SECRET" not in r["last_error"] and r["last_ok"]  # last success is kept
    assert status.status_of(3, None, False) == "failing" and status.status_of(1, None, False) == "degraded"
    assert status.status_of(0, 4, False) == "degraded" and status.status_of(0, 3, False) == "ok"
    status.record(c, "eth", True)
    assert c.execute("SELECT consec_fail FROM source_health WHERE source='eth'").fetchone()[0] == 0
    status.record(c, "eth", True, soft_error="token events: timeout")  # partial failure counts as a failed run
    assert c.execute("SELECT consec_fail FROM source_health WHERE source='eth'").fetchone()[0] == 1


def test_build_overall_and_coverage(tmp_path):
    c = mk(tmp_path)
    status.record(c, "xlayer", True)
    for k, v in {"first": 100, "lo": 600, "hi": 1000, "latest": 1000}.items():
        meta_set(c, f"xlayer_scan_{k}", v)
    for i in range(3):
        status.record(c, "sol_usdt", False, error="rpc slow")
    out = status.build(c, {"eth": {"gap": 5, "onchain_count": 10, "max_id": 4, "stale": True}}, {"xlayer": "USDT0 - X Layer"})
    by = {s["source"]: s for s in out["sources"]}
    assert out["overall"] == "failing" and "sol_usdt" in out["bad"]
    assert by["xlayer"]["partial"] and "log scan" in by["xlayer"]["coverage"] and by["xlayer"]["status"] == "ok"
    assert by["xlayer"]["note"]
    json.dumps(out)


def test_plan_windows_descending_and_clamped():
    assert tokenevents.plan_windows(1000, 100, 300, 2) == [(700, 999), (400, 699)]
    assert tokenevents.plan_windows(450, 100, 300, 5) == [(150, 449), (100, 149)]
    assert tokenevents.plan_windows(100, 100, 300, 5) == []


def test_rpcscan_resumes_and_stops_on_error(tmp_path, monkeypatch):
    c = mk(tmp_path)
    calls = []

    def fake(calls_in, chunk=50, url=None, retries=6):
        out = []
        for m, p in calls_in:
            if m == "eth_blockNumber":
                out.append(hex(1000))
            elif m == "eth_getCode":
                out.append("0x60" if int(p[1], 16) >= 100 else "0x")
            else:
                f = int(p[0]["fromBlock"], 16)
                calls.append(f)
                if f < 400:
                    raise RuntimeError("RPC error: boom")
                out.append([])
        return out
    monkeypatch.setattr(tokenevents.eth, "rpc_batch", fake)
    monkeypatch.setattr(tokenevents.evmmsw.eth, "rpc_batch", fake)
    cfg = {"label": "T", "rpc": "http://x", "token": "0x" + "11" * 20}
    rows, st = tokenevents._rpcscan(c, cfg, {"step": 100, "batch": 2, "budget": 30}, "t")
    assert st["hi"] == 1000 and st["lo"] == 401 and st["first"] <= 100 and "boom" in st["error"]
    assert rows == []
    tokenevents.save_scan(c, "t", st)
    assert tokenevents.scan_state(c, "t")["lo"] == 401
    # a second run starts from the saved cursors, not from scratch
    calls.clear()
    st2 = tokenevents._rpcscan(c, cfg, {"step": 100, "batch": 2, "budget": 30}, "t")[1]
    assert min(calls) < 401 and st2["lo"] == 401 and st2["hi"] == 1000


def test_live_status_decoders():
    assert LC.near_status_blocked("Banned") is True and LC.near_status_blocked("Allowable") is False
    assert LC.near_status_blocked("weird") is None
    assert LC.aptos_blocked([True, True]) is True and LC.aptos_blocked([False, False]) is False
    assert LC.aptos_blocked([False, True]) is True and LC.aptos_blocked({"a": 1}) is None and LC.aptos_blocked([]) is None
    assert LC.ton_status_blocked(1) is True and LC.ton_status_blocked(0) is False and LC.ton_status_blocked(7) is None
    t = LC.targets()
    assert t["near"]["method"] == "get_blacklist_status" and t["aptos"]["function"].endswith("::usdt::is_frozen")
    assert "tonapi.io" in t["providers"] and "rpc.mainnet.near.org" in t["providers"]
