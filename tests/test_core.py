import pytest

from freezewatch import abi, constants as C
from freezewatch.db import connect, ensure_proposal, add_event, update_proposal
from freezewatch.derive import compute_status, derive_all, infer_exec, rebuild_blacklist
from freezewatch.stats import delay_stats, histogram, percentile
from freezewatch.tron import parse_call
from freezewatch.util import b58check_decode, tron_from_hex20, tron_to_hex20

TRON_ADDRS = [
    "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "TBPxhVAsuzoFnKyXtc1o2UySEydPHgATto",
    "TYn9kGVaNbXCsdqDCvKu4ubDvj3ZSxmyN1", "TKHuVq1oKVruCGLvqVexFs6dawKv6fQgFs",
    "TRLi4KskHxfACnxKzLrf2wNHauWUQpwziN", "TTAgiPybj1tdtPe49Z1zubnB78AS4ZhXHD",
    "TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd", "TJ8tvVijjKrxdnKQWaivnLLNPXnuMZv63p",
]


def w(n):
    return f"{n:064x}"


def test_decode_transactions_result():
    data = "0ecb93c0" + "0" * 24 + "bca5d8e9fc4e31c72631a144e7f1563736f09061"
    res = ("0" * 24 + "a614f803b6fd780986a42c78ec9c7f77e6ded13c" + w(0) + w(0x80) + w(1) + w(len(data) // 2)
           + data.ljust(128, "0"))
    d = abi.decode_transactions_result("0x" + res)
    assert d["destination_hex"] == "a614f803b6fd780986a42c78ec9c7f77e6ded13c"
    assert d["value"] == 0 and d["executed"] is True and d["data"] == data
    dec = abi.decode_calldata(d["data"], True, tron_from_hex20)
    assert dec["function"] == "addBlackList"
    assert dec["target"] == tron_from_hex20("bca5d8e9fc4e31c72631a144e7f1563736f09061")


def test_decode_calldata_variants():
    f = lambda h: "0x" + h
    assert abi.decode_calldata("cc872b66" + w(5_000_000), True, f)["amount"] == 5.0
    assert abi.decode_calldata("db006a75" + w(1_500_000), True, f)["function"] == "redeem"
    assert abi.decode_calldata("8456cb59", True, f)["function"] == "pause"
    assert abi.decode_calldata("deadbeef" + w(1), True, f)["function"] == "0xdeadbeef"
    assert abi.decode_calldata("0ecb93c0" + w(1), False, f)["function"] == "other_contract"


def test_selectors_and_topics():
    eu = pytest.importorskip("eth_utils")
    for sel, (name, _) in C.USDT_SELECTORS.items():
        assert eu.keccak(text=C.SIG[name])[:4].hex() == sel, name
    for sig, sel in [("isBlackListed", C.SEL_IS_BLACKLISTED), ("balanceOf", C.SEL_BALANCE_OF),
                     ("transactions", C.SEL_TRANSACTIONS), ("getConfirmationCount", C.SEL_CONF_COUNT),
                     ("required", C.SEL_REQUIRED), ("transactionCount", C.SEL_TX_COUNT),
                     ("submitTransaction", C.SEL_SUBMIT), ("confirmTransaction", C.SEL_CONFIRM),
                     ("revokeConfirmation", C.SEL_REVOKE), ("executeTransaction", C.SEL_EXECUTE)]:
        assert eu.keccak(text=C.SIG[sig])[:4].hex() == sel, sig
    for topic, sig in C.TOPIC_SIGS.items():
        assert "0x" + eu.keccak(text=sig).hex() == topic, sig


def test_base58_roundtrip():
    for a in TRON_ADDRS:
        h = tron_to_hex20(a)
        assert len(h) == 40 and tron_from_hex20(h) == a
        assert b58check_decode(a)[0] == 0x41
    assert tron_to_hex20("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t") == "a614f803b6fd780986a42c78ec9c7f77e6ded13c"
    with pytest.raises(ValueError):
        tron_to_hex20("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u")


EV_9391 = [
    {"signer": "TYn9kGVaNbXCsdqDCvKu4ubDvj3ZSxmyN1", "kind": "submit", "ts": "2026-05-27T12:22:42Z",
     "tx_hash": "a066969542f7104ef28eb7e1d2e0f67acc695355d5d3ab1bcaf74beefc77ae34"},
    {"signer": "TRLi4KskHxfACnxKzLrf2wNHauWUQpwziN", "kind": "confirm", "ts": "2026-05-27T14:12:06Z",
     "tx_hash": "9830c04b5c02cb5279b3c4f855a528b4c619f8ced34dbdbb8678b97645595d60"},
    {"signer": "TKHuVq1oKVruCGLvqVexFs6dawKv6fQgFs", "kind": "confirm", "ts": "2026-05-27T15:00:00Z",
     "tx_hash": "ff" * 32},  # пізніше дублююче підтвердження - no-op
]


def test_tron_exec_inference_9391():
    ex = infer_exec(EV_9391, 2)
    assert ex["ts"] == "2026-05-27T14:12:06Z" and ex["signer"].startswith("TRLi4K")
    assert ex["tx_hash"].startswith("9830c04b")


def test_tron_exec_with_revoke():
    ev = [
        {"signer": "A", "kind": "submit", "ts": "2026-01-01T00:00:00Z", "tx_hash": "1"},
        {"signer": "A", "kind": "revoke", "ts": "2026-01-01T00:10:00Z", "tx_hash": "2"},
        {"signer": "B", "kind": "confirm", "ts": "2026-01-01T00:20:00Z", "tx_hash": "3"},
        {"signer": "A", "kind": "confirm", "ts": "2026-01-01T00:30:00Z", "tx_hash": "4"},
    ]
    assert infer_exec(ev, 2)["tx_hash"] == "4"
    assert infer_exec(ev[:3], 2) is None


def test_derive_9391_delay_6564(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    ensure_proposal(conn, "tron", 9391)
    update_proposal(conn, "tron", 9391, destination=C.TRON_USDT, function="addBlackList",
                    target="TTAgiPybj1tdtPe49Z1zubnB78AS4ZhXHD", executed=1, required=2)
    for e in EV_9391:
        add_event(conn, "tron", 9391, e["signer"], e["kind"], e["ts"], e["tx_hash"])
    derive_all(conn, "tron")
    r = conn.execute("SELECT * FROM proposals WHERE tx_id=9391").fetchone()
    assert r["delay_seconds"] == 6564 == 1 * 3600 + 49 * 60 + 24
    assert r["status"] == "executed" and r["submitter"].startswith("TYn9kG")
    rebuild_blacklist(conn)
    b = conn.execute("SELECT * FROM blacklist").fetchone()
    assert b["address"] == "TTAgiPybj1tdtPe49Z1zubnB78AS4ZhXHD" and b["status"] == "blocked"


def test_eth_same_block_delay_zero(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    ensure_proposal(conn, "eth", 1)
    update_proposal(conn, "eth", 1, destination=C.ETH_USDT, function="addBlackList", target="0xabc", executed=1,
                    required=3)
    add_event(conn, "eth", 1, "0xa", "submit", "2026-01-01T00:00:00Z", "h1")
    add_event(conn, "eth", 1, "0xb", "confirm", "2026-01-01T00:00:00Z", "h1")
    add_event(conn, "eth", 1, "", "execute", "2026-01-01T00:00:00Z", "h1")
    derive_all(conn, "eth")
    assert conn.execute("SELECT delay_seconds FROM proposals").fetchone()[0] == 0


def test_status():
    def f(**k):
        base = {"executed": 0, "had_failure": False, "confirmations": 1, "had_revoke": False,
                "submitted_at": "2026-01-01T00:00:00Z", "stale_days": 7, "now": 1767225600 + 3600}
        base.update(k)
        return compute_status(**base)
    assert f() == "pending"
    assert f(now=1767225600 + 8 * 86400) == "stale"
    assert f(confirmations=0, had_revoke=True) == "revoked"
    assert f(had_failure=True) == "failed"
    assert f(executed=1) == "executed"


def test_percentiles():
    v = [0, 10, 20, 30, 40]
    assert percentile(v, 50) == 20 and percentile(v, 25) == 10 and percentile(v, 90) == 36
    assert percentile([], 50) is None and percentile([7], 90) == 7
    s = delay_stats([0, 0, 60, 4000, 100000])
    assert s["n"] == 5 and s["median"] == 60 and s["share_same_block"] == 0.4
    assert s["share_gt_24h"] == 0.2 and s["share_lt_1h"] == 0.6 and s["max"] == 100000
    h = histogram([0, 30, 100, 5000, 90000])
    assert sum(b["count"] for b in h) == 5 and h[0]["count"] == 1


def test_parse_call():
    base = {"hash": "h", "timestamp": 1779891126000, "ownerAddress": "TRLi", "toAddress": C.TRON_MULTISIG,
            "contractRet": "SUCCESS", "revert": False,
            "trigger_info": {"methodName": "confirmTransaction", "parameter": {"transactionId": "9391"}}}
    assert parse_call(base)["tx_id"] == 9391
    assert parse_call({**base, "revert": True}) is None
    assert parse_call({**base, "contractRet": "REVERT"}) is None
    assert parse_call({**base, "toAddress": "Tother"}) is None
    sub = {**base, "trigger_info": {"methodName": "submitTransaction",
                                    "parameter": {"destination": C.TRON_USDT, "data": "0ecb93c0aa", "value": "0"}}}
    c = parse_call(sub)
    assert c["method"] == "submit" and c["destination"] == C.TRON_USDT and c["data"] == "0ecb93c0aa"
    assert parse_call({**base, "trigger_info": {"methodName": "addOwner", "parameter": {}}}) is None
