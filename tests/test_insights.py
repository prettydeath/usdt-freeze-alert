import json
import time

from freezewatch import alerts, clusters, labels, outflows, signals
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import iso, tron_from_hex20

NOW = int(time.time())
A1 = "0x" + "11" * 20
A2 = "0x" + "22" * 20
A3 = "0x" + "33" * 20


def mk(conn, chain, tid, target, executed_at=None, submitted_at=None, status="executed", fn="addBlackList"):
    ensure_proposal(conn, chain, tid)
    update_proposal(conn, chain, tid, destination="x", function=fn, target=target, executed=int(executed_at is not None),
                    executed_at=iso(executed_at) if executed_at else None, submitted_at=iso(submitted_at or executed_at),
                    status=status, confirmations=2, required=3, exec_tx=f"0x{tid:064x}")


# ---------------- labels ----------------
def test_label_key_normalisation_tron_and_evm():
    hx = "0x" + "ab" * 20
    b58 = tron_from_hex20("ab" * 20)
    assert labels.key_of("tron", b58) == hx           # TRON base58 -> 20-byte hex key
    assert labels.key_of("ethereum", hx.upper().replace("0X", "0x")) == hx
    assert labels.key_of("ethereum", "") is None and labels.key_of("tron", "Tnotanaddress") == "Tnotanaddress"
    assert labels.key_of("aptos", "0xABC") == "0xabc"


CSV = ("address,network,entity,label,category,source,source_url,confidence,last_updated,categories,tags,method,first_seen\n"
       "0x" + "aa" * 20 + ",ethereum,Binance,Binance 14,exchange,defillama-cex,https://x/1,high,2026-11-20,exchange,,official,2026-10-01\n"
       "0x" + "aa" * 20 + ",ethereum,Other,Weak,entity,eth-labels,https://x/2,low,2026-11-20,entity,,scraped,2026-10-01\n"
       "0x" + "bb" * 20 + ",ethereum,OFAC,SDN,sanctioned,ofac,https://x/3,high,2026-11-20,sanctioned;frozen;entity,,official,2026-10-01\n"
       "0x" + "cc" * 20 + ",ethereum,,Frozen,frozen,tether,https://x/4,high,2026-11-20,frozen,,official,2026-10-01\n")


def test_label_match_prefers_severe_skips_frozen_only_and_marks_checked(tmp_path):
    conn = connect(tmp_path / "l.sqlite")
    rows = labels.parse_rows(CSV, "ethereum")
    n = labels.match(conn, "ethereum", {"0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20, "0x" + "dd" * 20}, rows)
    assert n == 2  # aa, bb; cc is only a restatement of the blacklist, dd is unknown
    assert conn.execute("SELECT entity FROM address_labels WHERE key=?", ("0x" + "aa" * 20,)).fetchone()[0] == "Binance"  # higher confidence
    assert conn.execute("SELECT category FROM address_labels WHERE key=?", ("0x" + "bb" * 20,)).fetchone()[0] == "sanctioned"
    assert conn.execute("SELECT COUNT(*) FROM label_checked").fetchone()[0] == 4
    assert labels.lookup(conn, "eth", ("0x" + "AA" * 20))["entity"] == "Binance"  # case-insensitive, chain -> network
    assert labels.lookup(conn, "tron", "0x" + "aa" * 20) is None  # labels are network-scoped
    lab = labels.load_map(conn)
    assert labels.is_connector(lab[("ethereum", "0x" + "aa" * 20)]) and not labels.is_connector(lab[("ethereum", "0x" + "bb" * 20)])


# ---------------- outflows ----------------
def test_outflow_windowing_and_sinks():
    fz = 1_000_000
    rows = [("0xr1", 500.0, fz - 100, "t1", 0),                 # inside the 72h window, after submission -> in-window
            ("0xr2", 200.0, fz - 3 * 3600, "t2", 0),            # inside 72h but before submission
            ("0xr3", 50.0, fz - 80 * 3600, "t3", 0),            # older than 72h -> dropped
            ("0xr4", 70.0, fz + 5, "t4", 0),                    # after the freeze -> dropped
            ("0x" + "00" * 20, 9.0, fz - 10, "t5", 0),          # sink
            (A1, 8.0, fz - 10, "t6", 0),                        # self-transfer
            ("0xr7", 0.001, fz - 10, "t7", 0)]                  # dust
    out = outflows.window_rows(rows, A1, fz, fz - 600, sinks={"0x" + "00" * 20})
    assert [(r[0], r[5]) for r in out] == [("0xr1", 1), ("0xr2", 0)]
    assert outflows.window_rows(rows, A1, fz, None, sinks={"0x" + "00" * 20})[0][5] == 0  # unknown submission: never in-window


def test_outflow_parsers():
    lg = {"topics": [outflows.TRANSFER_TOPIC, "0x" + "0" * 24 + "11" * 20, "0x" + "0" * 24 + "22" * 20], "data": hex(1_500_000),
          "timeStamp": hex(1234), "transactionHash": "0xabc", "logIndex": "0x2"}
    assert outflows.parse_out_log(lg) == ("0x" + "22" * 20, 1.5, 1234, "0xabc", 2)
    assert outflows.parse_out_log({**lg, "topics": ["0xdead"]}) is None
    assert outflows.parse_tron_out({"type": "Approval", "value": "1", "to": "T", "block_timestamp": 1, "transaction_id": "x"}) is None
    assert outflows.parse_tron_out({"type": "Transfer", "value": "2000000", "to": "Tto", "block_timestamp": 5000, "transaction_id": "x"}) == ("Tto", 2.0, 5, "x", 0)


def test_outflow_stats_frontrun_and_entities(tmp_path):
    conn = connect(tmp_path / "o.sqlite")
    fz = NOW - 86400
    f_iso = iso(fz)
    rows = [("eth", A1, "0x" + "aa" * 20, 9000.0, iso(fz - 60), "t1", 0, 1, f_iso),
            ("eth", A1, "0x" + "ee" * 20, 500.0, iso(fz - 7200), "t2", 0, 0, f_iso)]
    conn.executemany("INSERT INTO outflows VALUES(?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO outflow_done VALUES('eth',?,?,?,?,?)", (A1, f_iso, f_iso, 9500.0, 9000.0))
    lab = {("ethereum", "0x" + "aa" * 20): {"entity": "Binance", "label": "Binance 14", "category": "exchange", "source_url": "https://x/1",
                                            "source": "s", "confidence": "high", "categories": "exchange"}}
    st = outflows.stats(conn, lab, frozen_usd={("eth", A1, f_iso): 1000.0})
    assert st["moved_12m"] == 9500.0 and st["window_12m"] == 9000.0 and st["frozen_12m"] == 1000.0
    assert st["top_entities"][0]["entity"] == "Binance" and st["top_entities"][0]["usd"] == 9000.0  # unlabelled recipient not listed
    assert st["n_frontrun"] == 1 and st["frontrun"][0]["share"] == 0.9
    pa = outflows.per_address(conn, lab)[("eth", A1)][0]
    assert pa["total"] == 9500.0 and pa["window"] == 9000.0 and pa["recipients"][0]["label"]["entity"] == "Binance"


# ---------------- signals ----------------
def fake_checker(table):
    def chk(chain, addr):
        if (chain, addr) not in table:
            raise RuntimeError("down")
        return table[(chain, addr)]
    return chk


def test_signal_detection_dedupe_and_threshold(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    mk(conn, "eth", 1, A1, executed_at=NOW - 600)
    chk = fake_checker({("pol", A1): (False, 25000.0), ("arb1", A1): (False, 500.0), ("avax", A1): (True, 90000.0),
                        ("tron", A1): (False, 1500.0)})
    n = signals.detect(conn, checker=chk, now=NOW, chains=["eth", "pol", "arb1", "avax", "tron", "celo"])
    assert n == 1
    s = signals.payload(conn)["open"][0]
    assert s["frozen_on"] == "eth" and {e[0] for e in s["at_risk"]} == {"pol", "tron"}  # arb1 below $1000, avax already blocked, celo unreachable
    assert signals.detect(conn, checker=chk, now=NOW, chains=["pol"]) == 0   # trigger already checked
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1


def test_signal_ignores_old_and_sink_triggers(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    mk(conn, "eth", 1, A1, executed_at=NOW - 5 * 86400)                       # stale trigger
    mk(conn, "eth", 2, "0x" + "00" * 20, executed_at=NOW - 60)                # sink
    assert signals.triggers(conn, NOW) == []


def test_signal_resolution_lag_and_moved_before(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    mk(conn, "eth", 1, A2, executed_at=NOW - 7200)
    chk = fake_checker({("pol", A2): (False, 20000.0), ("arb1", A2): (False, 10000.0)})
    signals.detect(conn, checker=chk, now=NOW - 7000, chains=["pol", "arb1"])
    # later: pol froze it (balance intact), arb1 froze it after most funds left
    mk(conn, "pol", 7, A2, executed_at=NOW - 1000)
    chk2 = fake_checker({("pol", A2): (True, 20000.0), ("arb1", A2): (True, 800.0)})
    assert signals.resolve(conn, checker=chk2, now=NOW) == 2
    s = signals.payload(conn)["history"][0]
    by = {x["chain"]: x for x in s["resolutions"]}
    assert s["status"] == "resolved"
    assert by["pol"]["moved_before"] is False and 6000 <= by["pol"]["lag_s"] <= 6300  # from our own execution time
    assert by["arb1"]["moved_before"] is True
    st = signals.payload(conn)["stats"]
    assert st["resolved_chains"] == 2 and st["moved_before_pct"] == 50.0 and st["median_lag_s"] is not None


def test_signal_expires_and_alert_format(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    mk(conn, "tron", 5, tron_from_hex20("33" * 20), executed_at=NOW - 600)
    signals.detect(conn, checker=fake_checker({("pol", A3): (False, 12500.0), ("arb1", A3): (False, 3100.0)}), now=NOW, chains=["pol", "arb1"])
    ev = signals.events(conn, NOW)
    assert len(ev) == 1 and ev[0]["key_kind"] == "xsignal"
    txt = ev[0]["text"]
    assert "🔗 <b>CROSS-CHAIN SIGNAL</b>" in txt and "<code>" + tron_from_hex20("33" * 20) + "</code>" in txt  # TRON shown as base58
    assert "$12,500</b> USDT0 on POL" in txt and "$3,100</b> USDT0 on ARB" in txt and "not blocked there" in txt
    assert signals.events(conn, NOW + 4 * 3600) == []  # no longer fresh
    signals.resolve(conn, checker=fake_checker({("pol", A3): (False, 12500.0), ("arb1", A3): (False, 3100.0)}), now=NOW + 15 * 86400)
    assert signals.payload(conn)["history"][0]["status"] == "expired"
    assert alerts.fmt_signal({"address": A3, "frozen_on": "eth", "stage": "proposed", "frozen_at": iso(NOW),
                              "at_risk": json.dumps([["pol", "USDT0", 5000.0, 5000.0]])}).count("freeze proposed on") == 1


# ---------------- clusters ----------------
def test_batch_clusters_gap_and_size():
    rows = [("tron", f"T{i}", 1000 + i * 30, "tx") for i in range(5)] + [("tron", "Tlate", 5000, "tx"), ("tron", "Tlate2", 5100, "tx"),
                                                                           ("eth", "0xsolo", 1000, "tx")]
    cl = clusters.batch_clusters(rows)
    assert sorted(len(c["members"]) for c in cl) == [2, 5] and all(c["kind"] == "batch" and c["chains"] == ["tron"] for c in cl)


def test_crosschain_clusters():
    cl = clusters.crosschain_clusters([("eth", A1, 10), ("pol", A1, 20), ("eth", A2, 30)])
    assert len(cl) == 1 and cl[0]["chains"] == ["eth", "pol"] and cl[0]["first"] == 10 and cl[0]["last"] == 20


def test_counterparty_clusters_exclude_connectors_and_hubs():
    edges = [("eth", "F1", "S1"), ("eth", "F2", "S1"),            # shared private sender -> cluster {F1, F2}
             ("eth", "F2", "S2"), ("eth", "F3", "S2"),            # chains F3 in via F2
             ("eth", "F4", "EXCH"), ("eth", "F5", "EXCH")]        # shared labelled exchange: must NOT link F4 and F5
    conn_fn = lambda chain, cp: cp == "EXCH"  # noqa: E731
    cl = clusters.counterparty_clusters(edges, conn_fn)
    assert len(cl) == 1 and {m[1] for m in cl[0]["members"]} == {"F1", "F2", "F3"}
    hub = [("eth", f"H{i}", "HUB") for i in range(clusters.MAX_HUB + 3)]
    assert clusters.counterparty_clusters(hub) == []              # an unlabelled hub touching too many addresses is ignored


def test_cluster_build_uses_labels_as_connectors(tmp_path):
    conn = connect(tmp_path / "c.sqlite")
    mk(conn, "eth", 1, A1, executed_at=NOW - 100)
    mk(conn, "eth", 2, A2, executed_at=NOW - 90000)
    for fa in (A1, A2):
        conn.execute("INSERT INTO inflows_after_freeze(chain,token,frozen_address,sender,amount,ts,tx_hash,log_index,first_seen_at) "
                     "VALUES('eth','USDT',?,?,10,?,?,0,?)", (fa, "0x" + "ee" * 20, iso(NOW), "0x" + fa[2:6], iso(NOW)))
    cl, _ = clusters.build(conn, {})
    assert any(c["kind"] == "counterparty" and c["size"] == 2 for c in cl)
    exch = {("ethereum", "0x" + "ee" * 20): {"entity": "Binance", "category": "exchange", "categories": "exchange"}}
    cl2, idx = clusters.build(conn, exch)
    assert not any(c["kind"] == "counterparty" for c in cl2)
    assert clusters.save(conn, cl2) in (True, False)


def test_near_executions_matched_by_confirm_tx():
    from freezewatch import near
    cr = [{"ts": 1, "function": "mint", "target": None}, {"ts": 2, "function": "addBlackList", "target": None}]
    cf = [{"ts": 5, "tx": "H2", "signer": "s", "request_id": 1}, {"ts": 6, "tx": "H0", "signer": "s", "request_id": 0}]
    ex = [{"ts": 5, "tx": "H2", "function": "add_to_blacklist", "target": None}, {"ts": 6, "tx": "H0", "function": "mint", "target": None}]
    out = near.executions_by_confirm(cr, cf, ex)
    assert out[1]["tx"] == "H2" and out[0]["tx"] == "H0"   # exact request ids, in spite of the swapped order
