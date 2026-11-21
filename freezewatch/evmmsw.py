"""Gnosis MultiSigWallet на інших EVM-мережах (Avalanche, Celo, Kaia, Kava): той самий механізм, що й ETH.
Події - через Etherscan-сумісний API (Routescan/Blockscout), стан - через публічний RPC.
Якщо логи недоступні - резервний режим опитування transactions(i) з часом першого побачення (first_seen_poll)."""
import logging
import time

from . import abi, eth
from .constants import SEL_CONF_COUNT, SEL_REQUIRED, SEL_TRANSACTIONS, SEL_TX_COUNT, SEL_BALANCE_OF, USDT_DECIMALS
from .db import ensure_proposal, meta_get, meta_set, update_proposal
from .derive import derive_all
from .registry import MSW_CHAINS
from .util import http_json, iso, now_ts

log = logging.getLogger("freezewatch.evmmsw")


def _batch(cfg, calls):
    """JSON-RPC: пачкою, а якщо вузол не підтримує - по одному."""
    try:
        return eth.rpc_batch(calls, chunk=25, url=cfg["rpc"])
    except Exception as e:  # noqa: BLE001
        log.info("%s: батч RPC не пройшов (%s), по одному", cfg["label"], str(e)[:80])
        return [eth.rpc_batch([c], url=cfg["rpc"])[0] for c in calls]


def _ec(cfg, to_msw, data):
    return _batch(cfg, [("eth_call", [{"to": to_msw, "data": data}, "latest"])])[0]


def read_state(cfg, ids):
    calls = []
    for i in ids:
        calls.append(("eth_call", [{"to": cfg["msw"], "data": "0x" + SEL_TRANSACTIONS + f"{i:064x}"}, "latest"]))
        calls.append(("eth_call", [{"to": cfg["msw"], "data": "0x" + SEL_CONF_COUNT + f"{i:064x}"}, "latest"]))
    res = _batch(cfg, calls)
    out = {}
    for n, i in enumerate(ids):
        out[i] = (abi.decode_transactions_result(res[2 * n]), int(res[2 * n + 1], 16))
    return out


def fetch_logs(cfg, from_block):
    """Усі логи MSW через Etherscan-сумісний API: сторінками по 1000 (без фільтра topic0)."""
    rows, page = [], 1
    while True:
        o = http_json("GET", cfg["logs"], interval=0.3, retries=4,
                      params={"module": "logs", "action": "getLogs", "address": cfg["msw"], "fromBlock": from_block,
                              "toBlock": "latest", "page": page, "offset": 1000},
                      validate=lambda x: x.get("status") == "1" or x.get("message") == "No records found"
                      or isinstance(x.get("result"), list))
        res = o["result"] if isinstance(o.get("result"), list) else []
        rows += res
        if len(res) < 1000:
            return rows
        page += 1


def sync_chain(conn, chain, stale_days=7):
    cfg = MSW_CHAINS[chain]
    required = int(_ec(cfg, cfg["msw"], "0x" + SEL_REQUIRED), 16)
    count = int(_ec(cfg, cfg["msw"], "0x" + SEL_TX_COUNT), 16)
    meta_set(conn, f"{chain}_required", required)
    meta_set(conn, f"{chain}_tx_count", count)
    mode = "logs"
    last_fail = float(meta_get(conn, f"{chain}_logs_fail_ts", 0) or 0)
    try:
        if time.time() - last_fail < 6 * 3600:  # індексатор логів недавно не відповідав - не гаємо час
            raise RuntimeError("пропуск: недавня відмова індексатора логів")
        cur = int(meta_get(conn, f"{chain}_next_block", 0) or 0)
        logs = fetch_logs(cfg, max(0, cur - 10))
        eth.ingest_logs(conn, logs, chain)
        if logs:
            meta_set(conn, f"{chain}_next_block", max(eth._h(x["blockNumber"]) for x in logs) + 1)
    except Exception as e:  # noqa: BLE001
        mode = "first_seen_poll"
        if "пропуск" not in str(e):
            meta_set(conn, f"{chain}_logs_fail_ts", time.time())
        log.warning("%s: логи недоступні (%s) - режим first_seen_poll", cfg["label"], str(e)[:100])
    meta_set(conn, f"{chain}_mode", mode)
    baseline = meta_get(conn, f"{chain}_baseline_done") != "1"  # перше опитування: історичних часів немає
    for i in range(count):
        ensure_proposal(conn, chain, i)
    prev_open = {r["tx_id"] for r in conn.execute(
        "SELECT tx_id FROM proposals WHERE chain=? AND destination IS NOT NULL AND executed=0", (chain,))}
    missing = [r["tx_id"] for r in conn.execute(
        "SELECT tx_id FROM proposals WHERE chain=? AND destination IS NULL ORDER BY tx_id", (chain,))]
    refresh = sorted(set(missing) | prev_open)
    override = {}
    executed_now = set()
    for i in range(0, len(refresh), 50):
        part = refresh[i:i + 50]
        st = read_state(cfg, part)
        for tid, (d, cnt) in st.items():
            dest = "0x" + d["destination_hex"]
            dec = abi.decode_calldata(d["data"], dest == cfg["token"], lambda h: "0x" + h)
            vals = dict(destination=dest, function=dec["function"], target=dec["target"], amount=dec["amount"],
                        raw_data="0x" + d["data"], executed=int(d["executed"]), required=required, chain_verified=1)
            update_proposal(conn, chain, tid, **vals)
            if not d["executed"]:
                override[tid] = cnt
            elif tid in prev_open:
                executed_now.add(tid)
        conn.commit()
    derive_all(conn, chain, stale_days=stale_days, conf_override=override)
    if mode == "first_seen_poll":
        # часу подій немає: подання = момент першого побачення; виконання = момент, коли побачили прапор
        now = iso(now_ts())
        for r in conn.execute("SELECT tx_id, first_seen_at, executed, executed_at, submitted_at FROM proposals "
                              "WHERE chain=?", (chain,)).fetchall():
            vals = {}
            if r["submitted_at"] is None and r["tx_id"] in prev_open | set(missing) | executed_now \
                    and not baseline:
                vals["submitted_at"] = r["first_seen_at"]
            if r["executed"] and r["executed_at"] is None and r["tx_id"] in executed_now and not baseline:
                vals["executed_at"] = now
                sub = vals.get("submitted_at") or r["submitted_at"]
                if sub:
                    from .util import parse_iso
                    vals["delay_seconds"] = max(0, parse_iso(now) - parse_iso(sub))
            if vals:
                update_proposal(conn, chain, r["tx_id"], **vals)
    meta_set(conn, f"{chain}_baseline_done", "1")
    conn.commit()


def balance_of(chain, addr):
    cfg = MSW_CHAINS[chain]
    r = eth.rpc_batch([("eth_call", [{"to": cfg["token"], "data": "0x" + SEL_BALANCE_OF + "0" * 24 + addr[2:]},
                                     "latest"])], url=cfg["rpc"])[0]
    return int(r, 16) / 10 ** USDT_DECIMALS
