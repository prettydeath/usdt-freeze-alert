import time

from freezewatch import alerts, analytics, inflows
from freezewatch.db import connect
from freezewatch.util import iso

NOW = int(time.time())
DAY = 86400
A, B = "0x" + "aa" * 20, "0x" + "bb" * 20
SENDER = "0x" + "11" * 20


def test_blocked_intervals_ignore_destroy_and_handle_refreeze():
    ev = [(100, "eth", A, "freeze", None), (150, "eth", A, "destroy", 5.0), (200, "eth", A, "unfreeze", None),
          (300, "eth", A, "freeze", None), (310, "eth", A, "freeze", None), (120, "eth", B, "freeze", None)]
    iv = inflows.blocked_intervals(ev)
    assert iv[("eth", A)] == [(100, 200), (300, None)]  # destroy does not end the block; duplicate freeze ignored
    assert iv[("eth", B)] == [(120, None)]
    assert inflows.in_blocked(150, iv[("eth", A)]) and not inflows.in_blocked(250, iv[("eth", A)])
    assert inflows.in_blocked(10_000, iv[("eth", A)]) and not inflows.in_blocked(100, iv[("eth", A)])  # strictly after


def test_parse_logs():
    log = {"topics": [inflows.TRANSFER_TOPIC, "0x" + "0" * 24 + "11" * 20, "0x" + "0" * 24 + "aa" * 20],
           "data": hex(2_500_000_000), "timeStamp": hex(1_700_000_000), "transactionHash": "0xabc", "logIndex": "0x5"}
    assert inflows.parse_evm_log(log) == (SENDER, 2500.0, 1_700_000_000, "0xabc", 5)
    assert inflows.parse_evm_log({**log, "topics": ["0xdead"]}) is None
    row = {"from": "TSender", "value": "1234500000", "block_timestamp": 1_700_000_000_000, "transaction_id": "ff" * 32}
    assert inflows.parse_tron_transfer(row) == ("TSender", 1234.5, 1_700_000_000, "ff" * 32, 0)
    assert inflows.parse_tron_transfer({"from": "x"}) is None


def test_save_filters_interval_dust_selfsend_and_dedupes(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    iv = [(1000, 2000)]
    rows = [(SENDER, 500.0, 1500, "0x1", 0),     # inside the block: kept
            (SENDER, 500.0, 900, "0x2", 0),      # before the freeze: dropped
            (SENDER, 500.0, 2500, "0x3", 0),     # after unfreeze: dropped
            (A, 500.0, 1500, "0x4", 0),          # self-send: dropped
            (SENDER, 0.0, 1500, "0x5", 0)]       # zero amount: dropped
    assert inflows.save(conn, "eth", "USDT", A, rows, iv) == 1
    assert inflows.save(conn, "eth", "USDT", A, rows, iv) == 0  # idempotent
    assert conn.execute("SELECT COUNT(*) FROM inflows_after_freeze").fetchone()[0] == 1


def test_inflow_stats_distinct_burned_and_sinks(tmp_path):
    from freezewatch.db import ensure_proposal, update_proposal
    conn = connect(tmp_path / "a.sqlite")

    def add(chain, frozen, sender, amt, ts, tx):
        conn.execute("INSERT INTO inflows_after_freeze(chain,token,frozen_address,sender,amount,ts,tx_hash,first_seen_at) "
                     "VALUES(?,?,?,?,?,?,?,?)", (chain, "USDT", frozen, sender, amt, iso(ts), tx, iso(NOW)))

    def act(tid, fn, target, ts):
        ensure_proposal(conn, "eth", tid)
        update_proposal(conn, "eth", tid, destination="x", function=fn, target=target, executed=1, status="executed",
                        executed_at=iso(ts), submitted_at=iso(ts - 60))
    add("eth", A, SENDER, 1000, NOW - 10 * DAY, "t1")
    add("eth", A, SENDER, 2000, NOW - 5 * DAY, "t2")       # same pair: one sender, one address, 2 transfers
    add("eth", B, "0x" + "22" * 20, 500, NOW - 200 * DAY, "t3")
    add("tron", "TFrozen", "TSend", 10_000, NOW - 400 * DAY, "t4")
    add("eth", analytics.EVM_ZERO, SENDER, 9_999_999, NOW - 3 * DAY, "sink")   # mistaken send: excluded
    act(1, "addBlackList", A, NOW - 20 * DAY)
    act(2, "destroyBlackFunds", A, NOW - 7 * DAY)            # burns the t1 funds only (t2 came after the destroy)
    conn.commit()
    st = analytics.inflow_stats(conn, NOW)
    assert st["periods"]["30d"] == {"eth": [1, 1, 2, 3000.0, 1, 1000.0]}
    assert st["periods"]["12m"]["eth"] == [2, 2, 3, 3500.0, 1, 1000.0] and "tron" not in st["periods"]["12m"]
    assert st["periods"]["all"]["tron"] == [1, 1, 1, 10000.0, 0, 0.0]
    an = {"inflow": st}
    assert analytics.inflow_summary(an, "all") == [3, 3, 4, 13500.0, 1, 1000.0]
    assert analytics.inflow_summary(an, "all", {"eth"}) == [2, 2, 3, 3500.0, 1, 1000.0]
    assert st["senders"]["all"][0][:4] == ["tron", "TSend", 1, 10000.0]  # ranked by amount
    assert st["receivers"]["30d"][0][1] == A


def test_purge_sinks_keeps_total(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    for i, (frozen, amt) in enumerate([(analytics.EVM_ZERO, 100.0), ("0xdac17f958d2ee523a2206206994597c13d831ec7", 50.0), (A, 7.0)]):
        conn.execute("INSERT INTO inflows_after_freeze(chain,token,frozen_address,sender,amount,ts,tx_hash,first_seen_at) "
                     "VALUES('eth','USDT',?,?,?,?,?,?)", (frozen, SENDER, amt, iso(NOW), f"t{i}", iso(NOW)))
    conn.execute("INSERT INTO inflow_cursor(chain,address,cursor,checked_at) VALUES('eth',?, '1', 'x')", (analytics.EVM_ZERO,))
    conn.commit()
    assert inflows.purge_sinks(conn) == (2, 150.0)
    assert conn.execute("SELECT COUNT(*) FROM inflows_after_freeze").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM inflow_cursor").fetchone()[0] == 0
    assert inflows.purge_sinks(conn) == (0, 0.0)  # idempotent; running total kept in meta
    import json
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key='inflow_excluded'").fetchone()[0]) == {"n": 2, "usd": 150.0}


def test_inflow_alert_rules_and_format(tmp_path):
    conn = connect(tmp_path / "a.sqlite")

    def add(amt, ts, tx):
        conn.execute("INSERT INTO inflows_after_freeze(chain,token,frozen_address,sender,amount,ts,tx_hash,first_seen_at) "
                     "VALUES('tron','USDT','TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd','TSender',?,?,?,?)", (amt, iso(ts), tx, iso(NOW)))
    add(500.0, NOW - 60, "small")           # below the $1,000 default
    add(48250.0, NOW - 60, "ab" * 32)       # fresh and large: alert
    add(9000.0, NOW - 5 * DAY, "old")       # not fresh
    conn.commit()
    nobal = lambda a, h: {}
    ev = alerts.collect_events(conn, {("x", 1): (0, 0, 2, "f", "p")}, 0, set(), now=NOW, balances=nobal, max_inflow_rowid=0)
    inf = [e for e in ev if e["kind"] == "inflow"]
    assert len(inf) == 1 and inf[0]["key_kind"] == "inflow"
    txt = inf[0]["text"]
    assert txt.startswith("💸 <b>SENT TO A FROZEN ADDRESS</b> · TRON") and "<b>48,250 USDT</b>" in txt
    assert "from <code>TSender</code> → to <code>TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd</code>" in txt
    assert "🔒 funds are now locked" in txt and f"<blockquote>tx {'ab' * 32}</blockquote>" in txt and "#TRON #lockedfunds" in txt
    assert alerts.collect_events(conn, {("x", 1): (0, 0, 2, "f", "p")}, 0, set(), now=NOW, balances=nobal,
                                 max_inflow_rowid=3) == []             # only rows newer than the pre-update cursor
    sent = []
    assert alerts.dispatch(conn, inf, sent.append) == 1 and alerts.dispatch(conn, inf, sent.append) == 0  # deduped


def test_covered_chains_documented():
    assert set(inflows.COVERED) == {"eth", "usdc_eth", "celo", "avax", "tron"}
