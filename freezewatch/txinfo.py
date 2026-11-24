"""Who sent the transaction behind an outflow, and was it paid for like a race?

For escaped outflows of recent freezes the transaction is looked up once (table `outflow_tx`):
- caller: Ethereum `tx.from` / TRON `owner_address`; a caller different from the frozen address means a third party (a bot or a
  contract) pulled the funds ("pulled by third party"); contracts that pulled from >= 2 later-frozen addresses are sweeper
  candidates (evasion.sweeper_candidates)
- Ethereum in-window transfers of >= FEE_MIN_USD: priority tip vs the median tip of the same block, transaction index and block
  (evasion.fee_anomaly). Direct coinbase payments need traces and are not covered.
Budgeted, resumable (a row per transaction), bounded HTTP; network access goes through injectable fetchers for the tests."""
import logging
import time

from . import analytics, deadline, eth, evasion, trongrid
from .util import HttpError, iso, now_ts, tron_from_hex20

log = logging.getLogger("freezewatch.txinfo")
MIN_USD = 1000.0
FEE_MIN_USD = 50_000.0
MAX_PER_RUN = 60
LOOKBACK_DAYS = 120


def pending(conn, limit=MAX_PER_RUN, now=None):
    """[(chain, tx_hash, frozen_address, amount, in_window)] still to look up: in-window and big transfers first."""
    cut = iso((now or now_ts()) - LOOKBACK_DAYS * 86400)
    rows = conn.execute(
        "SELECT o.chain, o.tx_hash, o.frozen_address, MAX(o.amount) amount, MAX(o.in_window) in_window FROM outflows o "
        "LEFT JOIN outflow_tx t ON t.chain=o.chain AND t.tx_hash=o.tx_hash "
        "WHERE t.tx_hash IS NULL AND o.amount>=? AND o.freeze_ts>=? AND o.chain IN ('eth','tron') "
        "GROUP BY o.chain, o.tx_hash, o.frozen_address ORDER BY in_window DESC, amount DESC LIMIT ?", (MIN_USD, cut, limit)).fetchall()
    return [tuple(r) for r in rows]


def fetch_eth_tx(tx_hash):
    t = eth.rpc_batch([("eth_getTransactionByHash", [tx_hash])], retries=2)[0]
    if not t:
        raise HttpError("tx not found")
    return t


def fetch_eth_block(number_hex):
    return eth.rpc_batch([("eth_getBlockByNumber", [number_hex, True])], retries=2)[0]


def fetch_tron_tx(txid):
    o = trongrid.request("POST", "/wallet/gettransactionbyid", json={"value": txid, "visible": True}, max_sec=20)
    c = ((o.get("raw_data") or {}).get("contract") or [{}])[0].get("parameter", {}).get("value", {})
    if not c:
        raise HttpError("tx not available yet")
    return {"caller": c.get("owner_address"), "to": c.get("contract_address") or c.get("to_address")}


def resolve(conn, budget_sec=60, eth_tx=fetch_eth_tx, eth_block=fetch_eth_block, tron_tx=fetch_tron_tx, now=None):
    """Look up pending transactions within the budget. -> number resolved."""
    t0 = time.time()
    n = 0
    for chain, txh, frozen, amount, inw in pending(conn, now=now):
        if time.time() - t0 > budget_sec or deadline.http_stop():
            break
        try:
            if chain == "tron":
                d = tron_tx(txh)
                row = (d["caller"], d["to"], None, None, None, None, None, None)
            else:
                tx = eth_tx(txh)
                tip = med = ratio = flagged = idx = None
                if inw and amount >= FEE_MIN_USD:
                    res = evasion.fee_anomaly(tx, eth_block(tx["blockNumber"]))
                    tip, med, ratio, flagged, idx = res["tip_gwei"], res["median_gwei"], res["ratio"], int(res["flagged"]), res["index"]
                row = ((tx.get("from") or "").lower(), (tx.get("to") or "").lower(), int(tx["blockNumber"], 16),
                       int(tx["transactionIndex"], 16) if tx.get("transactionIndex") else idx, tip, med, ratio, flagged)
        except Exception as e:  # noqa: BLE001
            log.debug("txinfo %s %s: %s", chain, txh[:10], str(e)[:60])
            continue
        conn.execute("INSERT OR REPLACE INTO outflow_tx(chain,tx_hash,caller,to_addr,block,tx_index,tip_gwei,median_gwei,tip_ratio,flagged,"
                     "checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (chain, txh, *row, iso(now_ts())))
        n += 1
        if n % 20 == 0:
            conn.commit()
    conn.commit()
    if n:
        log.info("txinfo: %s transactions looked up", n)
    return n


def callers(conn):
    """{(chain, tx_hash): (caller, to_addr, flagged, tip_ratio)}"""
    return {(r["chain"], r["tx_hash"]): (r["caller"], r["to_addr"], r["flagged"], r["tip_ratio"])
            for r in conn.execute("SELECT chain, tx_hash, caller, to_addr, flagged, tip_ratio FROM outflow_tx")}


def third_party_pulls(conn):
    """[(contract, frozen_address, caller, chain, usd)] - transfers whose caller is not the frozen address."""
    out = []
    for r in conn.execute("SELECT o.chain, o.tx_hash, o.frozen_address, SUM(o.amount) usd, t.caller, t.to_addr FROM outflows o "
                          "JOIN outflow_tx t ON t.chain=o.chain AND t.tx_hash=o.tx_hash GROUP BY o.chain, o.tx_hash, o.frozen_address"):
        if evasion.third_party(r["caller"], r["frozen_address"]):
            out.append((r["to_addr"], r["frozen_address"], r["caller"], r["chain"], r["usd"]))
    return out
