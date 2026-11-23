from freezewatch import health, tron
from freezewatch.constants import TRON_USDT
from freezewatch.db import connect
from freezewatch.util import iso, tron_from_hex20

USDT_HEX = "a614f803b6fd780986a42c78ec9c7f77e6ded13c"
BASE = 1_795_000_000  # seconds


def hx(i):
    return f"{i:040x}"


def calldata(i):
    return "0ecb93c0" + "0" * 24 + hx(i)


def call(conn, h, ts, signer, method, tx_id=None, dest=None, data=None):
    conn.execute("INSERT INTO tron_calls(tx_hash,ts_ms,block,signer,method,tx_id,destination,data) VALUES(?,?,?,?,?,?,?,?)",
                 (h, ts * 1000, 1, signer, method, tx_id, dest, data))


def fake_chain(onchain, monkeypatch):
    monkeypatch.setattr(tron, "read_count", lambda: max(onchain) + 1)
    monkeypatch.setattr(tron, "read_required", lambda: 2)
    monkeypatch.setattr(tron, "read_transaction", lambda tid: onchain[tid])
    monkeypatch.setattr(tron, "read_conf_count", lambda tid: 1)


def test_assembly_survives_submit_list_drift(tmp_path, monkeypatch):
    """Regression (Nov 2026): Tronscan's submit list had a different length than transactionCount, the old global
    offset mapping matched 16% and the whole assembly was skipped. Now every id must be ingested from the chain."""
    conn = connect(tmp_path / "t.sqlite")
    ids = list(range(100, 112))
    onchain = {i: {"destination_hex": USDT_HEX, "value": 0, "data": calldata(i % 3 + 1 if i in (104, 108) else i), "executed": i <= 109}
               for i in ids}
    fake_chain(onchain, monkeypatch)
    # submissions: only some ids have a Tronscan submit; extra submits exist that never became ids (drift)
    for i in (100, 101, 103, 106):
        call(conn, f"s{i}", BASE + i * 100, "TSubmitter", "submit", dest=TRON_USDT, data=calldata(i))
    call(conn, "sx1", BASE + 5, "TSubmitter", "submit", dest=TRON_USDT, data=calldata(9999))
    call(conn, "sx2", BASE + 6, "TSubmitter", "submit", dest=TRON_USDT, data=calldata(8888))
    # two ids with identical data (re-freeze of the same address): matched per id by first-confirmation time
    for i in (104, 108):
        onchain[i]["data"] = calldata(7)
    call(conn, "sa", BASE + 104 * 100 - 50, "TSubmitter", "submit", dest=TRON_USDT, data=calldata(7))
    call(conn, "sb", BASE + 108 * 100 - 50, "TSubmitter", "submit", dest=TRON_USDT, data=calldata(7))
    # explicit-id confirmations (the execution confirmation reaches required=2 together with the submitter)
    for i in range(100, 110):
        call(conn, f"c{i}", BASE + i * 100 + 30, "TConfirmer", "confirm", tx_id=i)
    tron.assemble(conn, None, 7, None, None, first_id=100)
    rows = {r["tx_id"]: r for r in conn.execute("SELECT * FROM proposals WHERE chain='tron'")}
    assert set(rows) == set(ids)                                   # nothing skipped
    for i in ids:
        assert rows[i]["executed"] == int(i <= 109) and rows[i]["chain_verified"] == 1
        assert rows[i]["target"] == tron_from_hex20(hx(7 if i in (104, 108) else i)) and rows[i]["function"] == "addBlackList"
    for i in (100, 101, 103, 106):                                  # matched per id
        assert rows[i]["submit_tx"] == f"s{i}"
    assert rows[104]["submit_tx"] == "sa" and rows[108]["submit_tx"] == "sb"      # identical data, resolved by confirmation time
    assert rows[102]["submit_tx"] is None and rows[102]["submitted_at"] == iso(BASE + 102 * 100 + 30)  # fallback, flagged
    assert rows[101]["delay_seconds"] == 30 and rows[104]["delay_seconds"] == 80 and rows[108]["delay_seconds"] == 80
    assert rows[101]["executed_at"] == iso(BASE + 101 * 100 + 30)                  # = the confirmation reaching required
    assert rows[110]["submitted_at"] is not None and rows[110]["submit_tx"] is None and rows[110]["status"] == "pending"
    assert rows[110]["status"] in ("pending", "stale") and rows[110]["executed"] == 0 and rows[110]["executed_at"] is None


def test_open_ids_are_refreshed_every_run(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.sqlite")
    onchain = {i: {"destination_hex": USDT_HEX, "value": 0, "data": calldata(i), "executed": False} for i in (200, 201)}
    fake_chain(onchain, monkeypatch)
    tron.assemble(conn, None, 7, None, None, first_id=200)
    assert [r["executed"] for r in conn.execute("SELECT executed FROM proposals WHERE chain='tron' ORDER BY tx_id")] == [0, 0]
    onchain[200]["executed"] = True                                  # executed on-chain between runs
    onchain[202] = {"destination_hex": USDT_HEX, "value": 0, "data": calldata(202), "executed": True}  # a new id
    call(conn, "c200", BASE, "TConfirmer", "confirm", tx_id=200)
    tron.assemble(conn, None, 7, None, None)
    rows = {r["tx_id"]: r for r in conn.execute("SELECT * FROM proposals WHERE chain='tron'")}
    assert rows[200]["executed"] == 1 and rows[200]["status"] == "executed" and 202 in rows and rows[201]["executed"] == 0


def test_health_check_flags_gap_and_is_stored(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    for tid in (5, 6, 7):
        conn.execute("INSERT INTO proposals(chain,tx_id,destination,first_seen_at,updated_at) VALUES('tron',?, 'x','a','a')", (tid,))
    conn.execute("INSERT INTO proposals(chain,tx_id,destination,first_seen_at,updated_at) VALUES('eth',9,'x','a','a')")
    out = health.check(conn, readers={"tron": lambda: 20, "eth": lambda: 10})
    assert out["tron"] == {"onchain_count": 20, "max_id": 7, "gap": 12, "stale": True}
    assert out["eth"]["stale"] is False and out["eth"]["gap"] == 0
    assert health.load(conn)["tron"]["stale"] is True
    bad = health.check(conn, readers={"tron": lambda: (_ for _ in ()).throw(RuntimeError("rpc down")), "eth": lambda: 10})
    assert bad["tron"] == {"error": "count unreadable"}
