from freezewatch import abi, alerts, constants as C, tokenevents as TE
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.registry import chain_meta

TOKEN = "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"
A1, A2, A3 = "0x" + "a1" * 20, "0x" + "a2" * 20, "0x" + "a3" * 20


def lg(topic, addr, tx, block, idx, ts, amount=None):
    return {"topics": [topic, "0x" + "0" * 24 + addr[2:]], "data": "0x" + (f"{amount:064x}" if amount is not None else ""),
            "timeStamp": hex(ts), "blockNumber": hex(block), "transactionHash": tx, "logIndex": hex(idx)}


def test_parse_log_kinds_and_amount():
    e = TE.parse_log(lg(C.TOPIC_BLOCK_PLACED, A1, "0xAB", 100, 3, 1_700_000_000))
    assert e == {"kind": "placed", "address": A1, "amount": None, "ts": 1_700_000_000, "block": 100, "tx": "0xab", "log_index": 3}
    d = TE.parse_log(lg(C.TOPIC_DESTROYED_BLOCKED, A1, "0xcd", 101, 0, 1_700_000_100, 5_000_000 * 10 ** 6))
    assert d["kind"] == "destroyed" and d["amount"] == 5_000_000.0
    assert TE.parse_log(lg(C.TOPIC_BLOCK_RELEASED, A1, "0xef", 5, 1, 1))["kind"] == "released"
    assert TE.parse_log({"topics": ["0xdead"], "data": "0x"}) is None


def test_event_id_is_unique_and_separate_from_safe_ids():
    assert TE.event_tx_id(100, 3) != TE.event_tx_id(100, 4) != TE.event_tx_id(101, 3)
    assert TE.event_tx_id(1, 0) > 18500  # Safe ids are nonce*100+index, far below


def test_link_to_safe_and_no_public_proposal(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    # a Safe transaction that executed addToBlockedList(A1) in tx 0xaa (proposal time known) ...
    ensure_proposal(conn, "arb1", 18500)
    update_proposal(conn, "arb1", 18500, destination=TOKEN.lower(), function="addToBlockedList", target=A1.upper().replace("0X", "0x"),
                    executed=1, executed_at="2026-11-09T01:35:56Z", submitted_at="2026-11-09T00:44:48Z", exec_tx="0xaa",
                    status="executed", delay_seconds=3068, public_proposal=1)
    events = [TE.parse_log(lg(C.TOPIC_BLOCK_PLACED, A1, "0xAA", 500, 1, 1_794_000_000)),          # linked by tx+target
              TE.parse_log(lg(C.TOPIC_BLOCK_PLACED, A2, "0xbb", 501, 2, 1_794_000_100)),          # nothing published
              TE.parse_log(lg(C.TOPIC_DESTROYED_BLOCKED, A2, "0xcc", 502, 0, 1_794_000_200, 777 * 10 ** 6))]
    assert TE.store(conn, "arb1", events) == 3 and TE.store(conn, "arb1", events) == 0  # idempotent
    # the Safe row's chain is arb1 and registry knows it: apply
    created, linked = TE.apply_events(conn, "arb1")
    assert (created, linked) == (2, 1)
    assert TE.apply_events(conn, "arb1") == (0, 0)
    safe = conn.execute("SELECT * FROM proposals WHERE tx_id=18500").fetchone()
    assert safe["delay_seconds"] == 3068 and safe["public_proposal"] == 1
    ev_rows = conn.execute("SELECT * FROM proposals WHERE chain='arb1' AND public_proposal=0 ORDER BY executed_at").fetchall()
    assert [r["function"] for r in ev_rows] == ["addToBlockedList", "destroyBlockedFunds"]
    assert ev_rows[0]["target"] == A2 and ev_rows[0]["delay_seconds"] is None and ev_rows[0]["status"] == "executed"
    assert ev_rows[1]["amount"] == 777.0 and ev_rows[1]["submitted_at"] is None
    assert conn.execute("SELECT linked FROM token_events WHERE tx_hash='0xaa'").fetchone()[0] == 1


def test_relink_after_a_fuller_safe_sync(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    TE.store(conn, "arb1", [TE.parse_log(lg(C.TOPIC_BLOCK_PLACED, A3, "0xdd", 700, 0, 1_794_100_000))])
    assert TE.apply_events(conn, "arb1") == (1, 0)
    # later the Safe history is fetched completely and contains the proposal
    ensure_proposal(conn, "arb1", 17000)
    update_proposal(conn, "arb1", 17000, destination=TOKEN.lower(), function="addToBlockedList", target=A3, executed=1,
                    executed_at="2026-11-10T00:00:00Z", submitted_at="2026-11-09T22:00:00Z", exec_tx="0xdd",
                    status="executed", delay_seconds=7200, public_proposal=1)
    assert TE.relink(conn, "arb1") == 1
    rows = conn.execute("SELECT tx_id, public_proposal FROM proposals WHERE chain='arb1'").fetchall()
    assert [tuple(r) for r in rows] == [(17000, 1)]


def test_registry_and_selectors():
    m = chain_meta()
    assert m["arb1"]["family"] == "usdt0" and m["xlayer"]["mech"] == "executed_only" and m["xlayer"]["family"] == "usdt0"
    assert set(TE.EVENT_SOURCES_KEYS) >= {"arb1", "xlayer", "oeth"}
    b = abi.decode_calldata("9dc29fac" + "0" * 24 + "ab" * 20 + f"{12 * 10**6:064x}", True, lambda h: "0x" + h)
    assert b == {"function": "burn", "target": "0x" + "ab" * 20, "amount": 12.0}
    assert abi.decode_calldata("8040a2c7" + "0" * 24 + "cd" * 20, True, lambda h: "0x" + h)["function"] == "setOFTContract"
    assert abi.decode_calldata("f32a12ac" + "0" * 24 + "cd" * 20 + "0" * 63 + "1", True, lambda h: "0x" + h)["function"] == "setTrusted"


def test_frozen_alert_says_no_public_proposal(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    ensure_proposal(conn, "arb1", TE.event_tx_id(5, 1))
    update_proposal(conn, "arb1", TE.event_tx_id(5, 1), function="addToBlockedList", target=A1, executed=1,
                    executed_at="2026-11-09T01:35:56Z", exec_tx="0x" + "ab" * 32, status="executed", public_proposal=0,
                    confirmations=0)
    r = conn.execute("SELECT * FROM proposals WHERE chain='arb1'").fetchone()
    txt = alerts.fmt_frozen(r)
    assert txt.startswith("🧊 <b>FROZEN</b> · ARB") and "⚡ <b>no public proposal</b>" in txt
    assert "signed" not in txt and "no advance notice" not in txt and f"tx {'0x' + 'ab' * 32}" in txt
