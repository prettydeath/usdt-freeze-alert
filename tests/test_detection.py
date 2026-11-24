import time

from freezewatch import alerts, evasion, exposure, flight, outflows, txinfo
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import iso

NOW = int(time.time())
F = "0x" + "11" * 20          # frozen address
A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
C = "0x" + "cc" * 20
POOL = "0x000000000004444c5dc75cb358380d2e3de08a90"


def lab(entity, cat, cats=None):
    return {"entity": entity, "label": entity, "category": cat, "categories": cats or cat, "source": "s", "source_url": "u", "confidence": "high"}


# ---------------- watchlist, exit classes ----------------
def test_watchlist_loads_verified_entries_only(tmp_path):
    w = evasion.load_watchlist()                                  # the shipped file
    assert w and all(e["evidence"] for e in w.values())
    assert any(e["kind"] == "fee_collector" for e in w.values()) and any(e["kind"] == "sweeper" for e in w.values())
    f = tmp_path / "w.yaml"
    f.write_text("- address: 0x" + "12" * 20 + "\n  kind: sweeper\n  name: x\n- address: 0x" + "13" * 20 + "\n  kind: sweeper\n  name: y\n"
                 "  evidence: tx 0xabc\n- address: 0x" + "14" * 20 + "\n  kind: nonsense\n  name: z\n  evidence: e\n", encoding="utf-8")
    assert len(evasion.load_watchlist(f)) == 1                    # no evidence / unknown kind -> rejected


def test_exit_classes_use_watchlist_then_labels():
    w = evasion.load_watchlist()
    assert evasion.classify_exit("eth", POOL, {}, w) == ("dex", "Uniswap v4 PoolManager")
    assert evasion.classify_exit("tron", "TJ4KeiGTvTX4wRUgLARiwr8rGLaurmVkdZ", {}, w)[0] == "bridge"
    labels = {("ethereum", A): lab("Gate.io", "exchange"), ("ethereum", B): lab("Some Bridge", "bridge"), ("ethereum", C): lab("Mixer", "mixer")}
    assert evasion.classify_exit("eth", A, labels, w) == ("cex", "Gate.io")
    assert evasion.classify_exit("eth", B, labels, w)[0] == "bridge" and evasion.classify_exit("eth", C, labels, w)[0] == "mixer"
    assert evasion.classify_exit("eth", "0x" + "dd" * 20, labels, w) == ("unlabelled", None)
    rows = [{"chain": "eth", "recipient": A, "amount": 100.0, "internal": False}, {"chain": "eth", "recipient": A, "amount": 0.5, "internal": False},
            {"chain": "eth", "recipient": B, "amount": 50.0, "internal": True}]
    out = evasion.exit_breakdown(rows, labels, w)
    assert out["cex"] == {"usd": 100.0, "n": 1} and out["bridge"]["n"] == 0        # dust and internal moves are not counted


# ---------------- outcome classes, lead times ----------------
def test_outcome_classes():
    assert evasion.outcome_class(0, 0, 1000)[0] == "clean" and evasion.outcome_class(5, 0, 1000)[0] == "clean"
    assert evasion.outcome_class(300, 300, 700)[0] == "racing"                      # 30 % moved between proposal and execution
    assert evasion.outcome_class(900, 0, 100)[0] == "weak"                          # 90 % gone before the proposal
    assert evasion.outcome_class(200, 0, 800)[0] == "partial"
    assert evasion.outcome_class(10, 1, None) == (None, None)                       # frozen amount unknown: no class
    assert evasion.outcome_class(0, 0, 0)[0] == "clean"


def test_lead_times_in_window_only():
    rows = [{"ts": 1000, "amount": 5.0, "tx": "a"}, {"ts": 1050, "amount": 9.0, "tx": "b"}, {"ts": 400, "amount": 1.0, "tx": "c"},
            {"ts": 1300, "amount": 2.0, "tx": "d"}]
    lt = evasion.lead_times(rows, 1000, 1200)
    assert [(x["after_submit_s"], x["before_execute_s"]) for x in lt] == [(0, 200), (50, 150)]
    s = evasion.lead_summary(lt)
    assert s["n"] == 2 and s["first_after_submit_s"] == 0 and s["median_before_execute_s"] == 175
    assert evasion.lead_summary([]) is None


def test_priority_fee_anomaly():
    def tx(h, tip, idx=0, fee_cap=100):
        return {"hash": h, "maxPriorityFeePerGas": hex(int(tip * 1e9)), "maxFeePerGas": hex(int(fee_cap * 1e9)), "transactionIndex": hex(idx)}
    block = {"baseFeePerGas": hex(int(10e9)), "transactions": [tx(f"0x{i}", 1.0, i) for i in range(1, 30)] + [tx("0xme", 50.0, 0)]}
    r = evasion.fee_anomaly(block["transactions"][-1], block)
    assert r["flagged"] and r["tip_gwei"] == 50.0 and r["median_gwei"] == 1.0 and r["ratio"] == 50.0 and r["index"] == 0
    assert not evasion.fee_anomaly(block["transactions"][0], block)["flagged"]
    legacy = {"hash": "0xl", "gasPrice": hex(int(70e9)), "transactionIndex": "0x1"}                 # legacy tx: tip = gasPrice - baseFee
    assert evasion.effective_tip(legacy, int(10e9)) == int(60e9)
    capped = tx("0xc", 50.0, fee_cap=12)                                                              # maxFee limits the tip to 2 gwei
    assert evasion.effective_tip(capped, int(10e9)) == int(2e9)


# ---------------- third-party pulls, sweepers, false-positive filters ----------------
def test_third_party_and_sweeper_candidates():
    assert evasion.third_party("0xBOT", F) and not evasion.third_party(F.upper().replace("0X", "0x"), F) and not evasion.third_party(None, F)
    pulls = [("0xsweep", F, "0xbot"), ("0xsweep", A, "0xbot"), ("0xother", F, "0xbot2"), ("0xsweep", F, "0xbot")]
    out = evasion.sweeper_candidates(pulls)
    assert list(out) == ["0xsweep"] and out["0xsweep"]["victims"] == 2 and out["0xsweep"]["callers"] == ["0xbot"]


def test_fee_collectors_and_repeat_recipients_ignore_noise():
    rows = [("tron", f"T{i}", "TFEE") for i in range(60)] + [("tron", "T1", "TREAL"), ("tron", "T2", "TREAL"), ("tron", "T3", "TONCE")]
    coll = evasion.fee_collectors(rows)
    assert ("tron", "TFEE") in coll and ("tron", "TREAL") not in coll
    usd = [("tron", f"T{i}", "TFEE", 5.0) for i in range(60)] + [("tron", "T1", "TREAL", 900.0), ("tron", "T2", "TREAL", 1100.0),
                                                                  ("tron", "T1", "TDUST", 0.1), ("tron", "T2", "TDUST", 0.2)]
    rep = evasion.repeat_recipients(usd, coll)
    assert rep == [("tron", "TREAL", 2, 2000.0)]                                                      # no fee collector, no dust
    w = {("tron", "0x" + "ab" * 20): {"kind": "fee_collector"}}
    assert ("tron", "0x" + "ab" * 20) in evasion.fee_collectors([], w)


# ---------------- hop-ahead watch (the 2026-10-29 pattern) ----------------
def _prop(conn, chain, tid, target, executed_at=None, submitted_at=None, status="executed"):
    ensure_proposal(conn, chain, tid)
    update_proposal(conn, chain, tid, destination="x", function="addBlackList", target=target, executed=int(executed_at is not None),
                    executed_at=iso(executed_at) if executed_at else None, submitted_at=iso(submitted_at or executed_at), status=status,
                    confirmations=1, required=3)


def test_flight_watch_flags_a_move_before_the_recipients_own_proposal(tmp_path):
    conn = connect(tmp_path / "f.sqlite")
    fz = NOW - 3600
    _prop(conn, "eth", 1, F, executed_at=fz, submitted_at=fz - 120)
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,1,?)", (F, A, 50_000.0, iso(fz - 600), "0xt1", iso(fz)))      # hop 1: A got $50k
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, "0x" + "00" * 20, 9_000.0, iso(fz - 500), "0xt2", iso(fz)))  # sink
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, B, 100.0, iso(fz - 500), "0xt3", iso(fz)))       # too small
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, C, 80_000.0, iso(fz - 400), "0xt4", iso(fz)))     # an exchange
    labels = {("ethereum", C): lab("Gate.io", "exchange")}
    assert flight.refresh_watch(conn, labels, watch={}, now=NOW) == 1
    assert [r["address"] for r in conn.execute("SELECT address FROM watched")] == [A]                 # exchange, sink and dust are not watched

    moved = {"ts": NOW - 120}

    def fetch(chain, addr, since, now, stop):   # A forwards $48k to D two minutes ago - nobody has proposed to freeze A
        return [(D := "0x" + "dd" * 20, 48_000.0, moved["ts"], "0x" + "e1" * 32, 0)] if addr == A else []
    assert flight.scan(conn, labels, watch={}, now=NOW, fetch_out=fetch) == 1
    m = conn.execute("SELECT * FROM watch_moves").fetchone()
    assert m["own_proposal"] == "none" and m["amount"] == 48_000.0
    assert conn.execute("SELECT hop FROM watched WHERE address=?", ("0x" + "dd" * 20,)).fetchone()[0] == 2   # the next hop is watched too
    ev = flight.events(conn, NOW)
    assert len(ev) == 1 and "PRE-PROPOSAL FLIGHT" in ev[0]["text"] and A in ev[0]["text"] and "no freeze proposal on it yet" in ev[0]["text"]
    assert flight.events(conn, NOW + 4 * 3600) == []                                                  # fresh only
    # later A gets its own proposal: the flight is confirmed after the fact and no longer alerts
    _prop(conn, "eth", 2, A, submitted_at=NOW, status="pending")
    flight.scan(conn, labels, watch={}, now=NOW + 400, fetch_out=lambda *a: [])
    assert conn.execute("SELECT own_proposal FROM watch_moves").fetchone()[0] == "after"
    st = flight.stats(conn)
    assert st["confirmed"] == 1 and st["confirmed_usd"] == 48_000.0 and st["hop1"] == 1 and st["hop2"] == 1
    assert flight.events(conn, NOW) == []


def test_flight_ignores_connectors_by_watchlist_and_already_frozen_recipients(tmp_path):
    conn = connect(tmp_path / "f.sqlite")
    fz = NOW - 3600
    _prop(conn, "eth", 1, F, executed_at=fz)
    _prop(conn, "eth", 2, A, executed_at=fz + 10)                                                      # A is itself frozen already
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, A, 50_000.0, iso(fz - 600), "0xt1", iso(fz)))
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, POOL, 90_000.0, iso(fz - 600), "0xt2", iso(fz)))
    assert flight.refresh_watch(conn, {}, watch=evasion.load_watchlist(), now=NOW) == 0


# ---------------- allowance / EIP-7702 ----------------
def test_exposure_eth_allowances_permit2_and_7702():
    code = "0xef0100" + "ab" * 20
    out = exposure.eth_exposure(A, get_code=lambda a: code, spenders=lambda a: [B, C],
                                allowance=lambda o, s: {B: 25_000.0, C: 0, exposure.PERMIT2: exposure.UNLIMITED}.get(s, 0))
    kinds = {(k, s): amt for k, s, amt, _n in out}
    assert ("eip7702", "0x" + "ab" * 20) in kinds
    assert kinds[("allowance", B)] == 25_000.0 and ("allowance", C) not in kinds                         # zero allowances are dropped
    assert kinds[("allowance", exposure.PERMIT2)] == exposure.UNLIMITED
    assert exposure.delegate_of("0x") is None and exposure.delegate_of("0x6080") is None
    assert exposure.eth_exposure(A, get_code=lambda a: "0x", spenders=lambda a: [], allowance=lambda o, s: 0) == []


def test_exposure_refresh_lines_and_alert(tmp_path):
    conn = connect(tmp_path / "x.sqlite")
    _prop(conn, "eth", 7, A, submitted_at=NOW - 60, status="pending")
    n = exposure.refresh(conn, now=NOW, eth_fn=lambda a: [("eip7702", B, None, "d"), ("allowance", exposure.PERMIT2, exposure.UNLIMITED, "unlimited"),
                                                          ("allowance", C, 1500.0, None)])
    assert n == 1 and exposure.targets(conn, now=NOW + 10) == []                                          # rechecked only after RECHECK_SEC
    ls = exposure.lines(conn, "eth", A)
    assert any("EIP-7702 delegated to " + B in x for x in ls) and any("allowance (unlimited) to Permit2" in x for x in ls)
    assert any("allowance ($1,500) to " + C in x for x in ls)
    r = conn.execute("SELECT * FROM proposals WHERE chain='eth' AND tx_id=7").fetchone()
    txt = alerts.fmt_new(r, None, "12:00:00", exposure=ls)
    assert "FREEZE INCOMING" in txt and "EIP-7702 delegated" in txt and "Permit2" in txt


# ---------------- caller / third-party pull / fee lookup ----------------
def test_txinfo_resolves_callers_and_flags_pulls(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    fz = NOW - 600
    for tx, amt, inw in (("0xself", 5_000.0, 0), ("0xpull", 143_000.0, 1)):
        conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,?,?)", (F, A, amt, iso(fz - 100), tx, inw, iso(fz)))
    conn.execute("INSERT INTO outflows VALUES('tron',?,?,?,?,?,0,0,?)", ("TFROZEN", "TREC", 70_000.0, iso(fz - 100), "tpull", iso(fz)))

    def eth_tx(h):
        return {"from": F if h == "0xself" else "0xBOT", "to": A if h == "0xself" else "0xSWEEP", "blockNumber": "0x64", "transactionIndex": "0x2",
                "hash": h, "maxPriorityFeePerGas": hex(int(40e9)), "maxFeePerGas": hex(int(100e9))}
    block = {"baseFeePerGas": hex(int(10e9)), "transactions": [{"hash": f"0x{i}", "maxPriorityFeePerGas": hex(int(1e9)), "maxFeePerGas": hex(int(50e9)),
                                                                 "transactionIndex": hex(i)} for i in range(20)]}
    n = txinfo.resolve(conn, 30, eth_tx=eth_tx, eth_block=lambda b: block,
                       tron_tx=lambda t: {"caller": "TBOT", "to": "TSWEEP"}, now=NOW)
    assert n == 3
    pulls = txinfo.third_party_pulls(conn)
    assert {(p[0], p[1]) for p in pulls} == {("0xsweep", F), ("TSWEEP", "TFROZEN")}                      # the self-sent transfer is not a pull
    row = conn.execute("SELECT * FROM outflow_tx WHERE tx_hash='0xpull'").fetchone()
    assert row["caller"] == "0xbot" and row["flagged"] == 1 and row["tip_ratio"] == 40.0 and row["tx_index"] == 2   # in-window and >= $50k: fee checked
    assert conn.execute("SELECT flagged FROM outflow_tx WHERE tx_hash='0xself'").fetchone()[0] is None   # small / outside the window: not checked
    assert txinfo.pending(conn, now=NOW) == []                                                              # everything resolved once


def test_outflow_stats_expose_evasion_numbers(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    fz = NOW - 3600
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,1,?)", (F, A, 900.0, iso(fz - 60), "0xp", iso(fz)))
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, B, 0.2, iso(fz - 60), "0xd", iso(fz)))             # dust
    conn.execute("INSERT INTO outflow_done(chain,frozen_address,freeze_ts,checked_at,n_transfers,truncated) VALUES('eth',?,?,?,2,0)", (F, iso(fz), iso(fz)))
    conn.execute("INSERT INTO outflow_tx(chain,tx_hash,caller,to_addr,checked_at) VALUES('eth','0xp','0xbot','0xsweep',?)", (iso(NOW),))
    _prop(conn, "eth", 1, F, executed_at=fz, submitted_at=fz - 300)
    labels = {("ethereum", A): lab("Binance", "exchange")}
    st = outflows.stats(conn, labels, now=NOW, frozen_usd={("eth", F, iso(fz)): 100.0}, watch={})
    assert st["exits"]["cex"] == {"usd": 900.0, "n": 1} and st["exits"]["unlabelled"]["n"] == 0                    # dust ignored
    assert st["classes"]["racing"]["n"] == 1                                                                     # 900 of 1000 moved in the window
    assert st["third_party"]["n"] == 1 and st["third_party"]["usd"] == 900.0
    assert st["lead"]["n"] == 1 and st["lead"]["first_after_submit_s"] == 240
    pa = outflows.per_address(conn, labels)[("eth", F)][0]
    assert any(r["pulled_by"] == "0xbot" for r in pa["recipients"])


# ---------------- destroy-amounts step (SQL placeholders must follow DESTROY_FUNCS) ----------------
def test_enrich_destroy_amounts_covers_every_destroy_function(tmp_path, monkeypatch):
    from freezewatch import analytics, constants
    conn = connect(tmp_path / "d.sqlite")
    for i, fn in enumerate(constants.DESTROY_FUNCS):       # destroyBlackFunds, destroyBlockedFunds, burn_from ...
        ensure_proposal(conn, "eth", 100 + i)
        update_proposal(conn, "eth", 100 + i, destination="x", function=fn, target=F, executed=1, executed_at=iso(NOW - 100),
                        exec_tx=f"0x{i:064x}", status="executed")
    log = []
    topics = ["0x" + "00" * 32]

    def fake_batch(calls, chunk=25, url=None, retries=3):
        log.extend(c[1][0] for c in calls)
        return [{"logs": []} for _ in calls]
    monkeypatch.setattr(analytics.eth, "rpc_batch", fake_batch)
    done = analytics.enrich_destroy_amounts(conn, 30)          # used to raise "Incorrect number of bindings supplied"
    assert done == len(constants.DESTROY_FUNCS) and len(log) == len(constants.DESTROY_FUNCS)
    assert conn.execute("SELECT COUNT(*) FROM proposals WHERE function='burn_from' AND amount IS NOT NULL").fetchone()[0] == 1


def test_configured_fee_collectors_never_count_as_exits_or_repeat_recipients():
    w = evasion.load_watchlist()
    fee = "TDii6vao7xyWg2rKPbCPWVRpSmne8xcqYx"                     # configured fee collector (TRON base58 in the data)
    rows = [{"chain": "tron", "recipient": fee, "amount": 3.0, "internal": False}, {"chain": "tron", "recipient": "TOther", "amount": 40.0, "internal": False}]
    out = evasion.exit_breakdown(rows, {}, w)
    assert out["watchlist"]["n"] == 0 and out["unlabelled"]["n"] == 1
    coll = evasion.fee_collectors([], w)
    assert ("tron", fee) in coll
    usd = [("tron", "T1", fee, 20.0), ("tron", "T2", fee, 25.0), ("tron", "T1", "TX", 500.0), ("tron", "T2", "TX", 600.0)]
    assert [r[1] for r in evasion.repeat_recipients(usd, coll)] == ["TX"]
