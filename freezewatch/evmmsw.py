"""Gnosis MultiSigWallet on other EVM chains (Avalanche, Celo, Kaia, Kava): same mechanism as Ethereum.
Events come from one of three sources (see registry `logs`): an Etherscan-compatible API, Etherscan V2 with a chainid,
or plain eth_getLogs in adaptive block chunks plus block timestamps. State is read via public RPC.
If events are unavailable, a fallback polls transactions(i) and uses first-seen time (first_seen_poll)."""
import logging
import time

from . import abi, eth
from .constants import SEL_BALANCE_OF, SEL_CONF_COUNT, SEL_REQUIRED, SEL_TRANSACTIONS, SEL_TX_COUNT, USDT_DECIMALS
from .db import ensure_proposal, meta_get, meta_set, update_proposal
from .derive import derive_all
from .registry import MSW_CHAINS
from .util import http_json, iso, now_ts, parse_iso

log = logging.getLogger("freezewatch.evmmsw")


def _batch(cfg, calls, retries=6):
    """JSON-RPC in batches; falls back to one call at a time if the node does not support batching."""
    try:
        return eth.rpc_batch(calls, chunk=25, url=cfg["rpc"], retries=retries)
    except Exception as e:  # noqa: BLE001
        log.info("%s: RPC batch failed (%s), one by one", cfg["label"], str(e)[:80])
        return [eth.rpc_batch([c], url=cfg["rpc"], retries=retries)[0] for c in calls]


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


def fetch_logs_compat(cfg, from_block):
    """All MSW logs via an Etherscan-compatible API, 1000 per page (no topic filter)."""
    rows, page = [], 1
    while True:
        o = http_json("GET", cfg["logs"]["url"], interval=0.3, retries=4,
                      params={"module": "logs", "action": "getLogs", "address": cfg["msw"], "fromBlock": from_block,
                              "toBlock": "latest", "page": page, "offset": 1000},
                      validate=lambda x: x.get("status") == "1" or x.get("message") == "No records found"
                      or isinstance(x.get("result"), list))
        res = o["result"] if isinstance(o.get("result"), list) else []
        rows += res
        if len(res) < 1000:
            return rows
        page += 1


def fetch_logs_v2(cfg, from_block):
    """Etherscan V2 (needs ETHERSCAN_API_KEY) for chains it supports, e.g. Celo (42220)."""
    return eth.get_logs(from_block, "latest", cfg["msw"], None, cfg["logs"]["chainid"])


def creation_block(cfg, latest):
    """Binary search for the first block where the MSW has code (needs archive eth_getCode); 0 if unavailable."""
    def has_code(b):
        r = eth.rpc_batch([("eth_getCode", [cfg["msw"], hex(b)])], url=cfg["rpc"], retries=2)[0]
        return r not in ("0x", "0x0", None)
    try:
        if not has_code(latest):
            return 0
        lo, hi = 0, latest
        while lo < hi:
            mid = (lo + hi) // 2
            lo, hi = (lo, mid) if has_code(mid) else (mid + 1, hi)
        log.info("%s: MSW creation block %s", cfg["label"], lo)
        return max(0, lo - 10)
    except Exception as e:  # noqa: BLE001
        log.info("%s: creation block search failed (%s), scanning from 0", cfg["label"], str(e)[:60])
        return 0


def fetch_logs_rpc(cfg, from_block, step):
    """eth_getLogs in adaptive chunks (halves on node errors / range limits), then block timestamps."""
    cfg = {**cfg, "rpc": cfg["logs"].get("url") or cfg["rpc"]}  # archive-capable endpoint for history
    latest = int(eth.rpc_batch([("eth_blockNumber", [])], url=cfg["rpc"])[0], 16)
    if from_block <= 0:
        from_block = creation_block(cfg, latest)
    rows, cur = [], from_block
    par = cfg["logs"].get("parallel")
    if par:  # fixed small windows (node limit), fetched concurrently
        from concurrent.futures import ThreadPoolExecutor
        rng = [(a, min(a + step - 1, latest)) for a in range(from_block, latest + 1, step)]

        def one(r):
            return eth.rpc_batch([("eth_getLogs", [{"fromBlock": hex(r[0]), "toBlock": hex(r[1]),
                                                    "address": cfg["msw"]}])], url=cfg["rpc"], retries=4)[0]
        with ThreadPoolExecutor(max_workers=par) as ex:
            for res in ex.map(one, rng):
                rows += res
        cur = latest + 1
    while cur <= latest:
        to = min(cur + step - 1, latest)
        try:
            res = eth.rpc_batch([("eth_getLogs", [{"fromBlock": hex(cur), "toBlock": hex(to), "address": cfg["msw"]}])],
                                url=cfg["rpc"], retries=2)[0]
        except Exception as e:  # noqa: BLE001
            if step <= 1000:
                raise
            step = max(1000, step // 2)
            log.info("%s: range error (%s), chunk -> %s", cfg["label"], str(e)[:60], step)
            continue
        rows += res
        cur = to + 1
    blocks = sorted({eth._h(x["blockNumber"]) for x in rows})
    ts = {}
    for i in range(0, len(blocks), 25):
        part = blocks[i:i + 25]
        for b, r in zip(part, _batch(cfg, [("eth_getBlockByNumber", [hex(b), False]) for b in part])):
            ts[b] = int(r["timestamp"], 16)
    for x in rows:
        x["timeStamp"] = hex(ts[eth._h(x["blockNumber"])])
    return rows, latest


def get_logs(cfg, from_block):
    """-> (logs, scanned_up_to_block or None)."""
    kind = cfg["logs"]["kind"]
    if kind == "compat":
        return fetch_logs_compat(cfg, from_block), None
    if kind == "etherscan_v2":
        return fetch_logs_v2(cfg, from_block), None
    return fetch_logs_rpc(cfg, from_block, cfg["logs"].get("chunk", 50_000))


def sync_chain(conn, chain, stale_days=7):
    cfg = MSW_CHAINS[chain]
    required = int(_ec(cfg, cfg["msw"], "0x" + SEL_REQUIRED), 16)
    count = int(_ec(cfg, cfg["msw"], "0x" + SEL_TX_COUNT), 16)
    meta_set(conn, f"{chain}_required", required)
    meta_set(conn, f"{chain}_tx_count", count)
    mode = "logs"
    last_fail = float(meta_get(conn, f"{chain}_logs_fail_ts", 0) or 0)
    try:
        if time.time() - last_fail < 6 * 3600:  # the log source failed recently: do not waste time
            raise RuntimeError("skipped: log source failed recently")
        cur = int(meta_get(conn, f"{chain}_next_block", 0) or 0)
        logs, scanned = get_logs(cfg, max(0, cur - 10))
        eth.ingest_logs(conn, logs, chain)
        # advance the cursor only on new events or after a long quiet stretch (keeps the DB file stable)
        if logs:
            meta_set(conn, f"{chain}_next_block", max(eth._h(x["blockNumber"]) for x in logs) + 1)
        elif scanned and scanned - cur > 500_000:
            meta_set(conn, f"{chain}_next_block", scanned - 100_000)
    except Exception as e:  # noqa: BLE001
        mode = "first_seen_poll"
        if "skipped" not in str(e):
            meta_set(conn, f"{chain}_logs_fail_ts", time.time())
        log.warning("%s: logs unavailable (%s) - first_seen_poll mode", cfg["label"], str(e)[:100])
    meta_set(conn, f"{chain}_mode", mode)
    baseline = meta_get(conn, f"{chain}_baseline_done") != "1"  # first poll: no historical times exist
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
        # no event times: submit = first-seen moment; execution = moment the executed flag was first seen
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
