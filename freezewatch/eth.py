"""Ethereum: події мультисига через Etherscan V2, стан через публічний RPC (eth_call)."""
import logging

from . import abi
from .constants import (ETH_MULTISIG, ETH_USDT, SEL_BALANCE_OF, SEL_CONF_COUNT, SEL_REQUIRED, SEL_TRANSACTIONS,
                        SEL_TX_COUNT, TOPIC_CONFIRMATION, TOPIC_EXEC_FAILURE, TOPIC_EXECUTION, TOPIC_REVOCATION,
                        TOPIC_SUBMISSION, USDT_DECIMALS)
from .db import add_event, ensure_proposal, meta_get, meta_set, update_proposal
from .derive import derive_all
from .util import HttpError, env, http_json, iso

log = logging.getLogger("freezewatch.eth")
CHAIN = "eth"
ETHERSCAN = "https://api.etherscan.io/v2/api"


def rpc_url():
    return env("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")


def rpc_batch(calls, chunk=50, url=None):
    """calls: [(method, params)] -> [result]. JSON-RPC batching з перевіркою id."""
    out = []
    for i in range(0, len(calls), chunk):
        part = calls[i:i + chunk]
        payload = [{"jsonrpc": "2.0", "id": j, "method": m, "params": p} for j, (m, p) in enumerate(part)]
        resp = http_json("POST", url or rpc_url(), json=payload, interval=0.1,
                         validate=lambda o: isinstance(o, list) and len(o) == len(payload))
        by_id = {x["id"]: x for x in resp}
        for j in range(len(part)):
            x = by_id[j]
            if "error" in x:
                raise HttpError(f"RPC error: {x['error']}")
            out.append(x["result"])
    return out


def eth_call(to, data):
    return rpc_batch([("eth_call", [{"to": to, "data": data}, "latest"])])[0]


def pad_int(n):
    return f"{n:064x}"


def latest_block():
    return int(rpc_batch([("eth_blockNumber", [])])[0], 16)


def read_required():
    return int(eth_call(ETH_MULTISIG, "0x" + SEL_REQUIRED), 16)


def read_count():
    return int(eth_call(ETH_MULTISIG, "0x" + SEL_TX_COUNT), 16)


def read_transactions(ids):
    ids = list(ids)
    res = rpc_batch([("eth_call", [{"to": ETH_MULTISIG, "data": "0x" + SEL_TRANSACTIONS + pad_int(i)}, "latest"])
                     for i in ids], chunk=50)
    return {i: abi.decode_transactions_result(r) for i, r in zip(ids, res)}


def read_conf_counts(ids):
    ids = list(ids)
    res = rpc_batch([("eth_call", [{"to": ETH_MULTISIG, "data": "0x" + SEL_CONF_COUNT + pad_int(i)}, "latest"])
                     for i in ids], chunk=50)
    return {i: int(r, 16) for i, r in zip(ids, res)}


def balance_of(addr):
    r = eth_call(ETH_USDT, "0x" + SEL_BALANCE_OF + "0" * 24 + addr.lower().removeprefix("0x"))
    return int(r, 16) / 10 ** USDT_DECIMALS


# ---------- Etherscan ----------
def _es(params):
    key = env("ETHERSCAN_API_KEY")
    if not key:
        raise HttpError("ETHERSCAN_API_KEY не задано (потрібен для історії ETH)")
    p = {"chainid": 1, "apikey": key, **params}
    return http_json("GET", ETHERSCAN, params=p, interval=0.25,
                     validate=lambda o: o.get("status") == "1" or o.get("message") == "No records found")


def get_logs(frm, to, address=ETH_MULTISIG, topic0=None):
    """Усі логи мультисига у [frm,to]; при переповненні ліміту 10000 ділить діапазон навпіл."""
    rows = []
    for page in range(1, 11):
        params = {"module": "logs", "action": "getLogs", "address": address, "fromBlock": frm,
                  "toBlock": to, "page": page, "offset": 1000, "sort": "asc"}
        if topic0:
            params["topic0"] = topic0
        o = _es(params)
        res = o["result"] if o.get("status") == "1" else []
        rows += res
        if len(res) < 1000:
            return rows
    if to <= frm:
        return rows
    mid = (frm + to) // 2
    log.info("діапазон %s-%s переповнений, ділю", frm, to)
    return get_logs(frm, mid, address, topic0) + get_logs(mid + 1, to, address, topic0)


def block_by_time(ts):
    o = _es({"module": "block", "action": "getblocknobytime", "timestamp": ts, "closest": "before"})
    return int(o["result"])


def first_block(address=ETH_MULTISIG, topic0=None):
    params = {"module": "logs", "action": "getLogs", "address": address, "fromBlock": 0,
              "toBlock": "latest", "page": 1, "offset": 1, "sort": "asc"}
    if topic0:
        params["topic0"] = topic0
    o = _es(params)
    return int(o["result"][0]["blockNumber"], 16)


def _h(x):
    if isinstance(x, int):
        return x
    if isinstance(x, str) and x.startswith("0x") and len(x) > 2:
        return int(x, 16)
    return int(x) if isinstance(x, str) and x.isdigit() else 0


def ingest_logs(conn, logs, chain=CHAIN):
    """Логи -> confirmations. Confirmation у тій же tx, що й Submission => kind=submit."""
    by_tx = {}
    for lg in logs:
        by_tx.setdefault(lg["transactionHash"], []).append(lg)
    touched = set()
    for txh, group in by_tx.items():
        subs = {int(l["topics"][1], 16) for l in group if l["topics"][0] == TOPIC_SUBMISSION}
        used_sub = set()
        for lg in group:
            t = lg["topics"]
            ts = iso(_h(lg["timeStamp"]))
            if t[0] == TOPIC_CONFIRMATION:
                tid = int(t[2], 16)
                signer = "0x" + t[1][-40:]
                kind = "submit" if tid in subs else "confirm"
                if kind == "submit":
                    used_sub.add(tid)
                ensure_proposal(conn, chain, tid)
                add_event(conn, chain, tid, signer, kind, ts, txh)
            elif t[0] == TOPIC_REVOCATION:
                tid = int(t[2], 16)
                ensure_proposal(conn, chain, tid)
                add_event(conn, chain, tid, "0x" + t[1][-40:], "revoke", ts, txh)
            elif t[0] == TOPIC_EXECUTION:
                tid = int(t[1], 16)
                ensure_proposal(conn, chain, tid)
                add_event(conn, chain, tid, "", "execute", ts, txh)
                update_proposal(conn, chain, tid, executed=1)
            elif t[0] == TOPIC_EXEC_FAILURE:
                tid = int(t[1], 16)
                ensure_proposal(conn, chain, tid)
                add_event(conn, chain, tid, "", "fail", ts, txh)
            elif t[0] != TOPIC_SUBMISSION:
                continue
            else:
                tid = int(t[1], 16)
            touched.add(tid)
        for tid in subs - used_sub:  # Submission без Confirmation у tx (теоретично)
            ts = iso(_h(group[0]["timeStamp"]))
            ensure_proposal(conn, chain, tid)
            add_event(conn, chain, tid, "", "submit", ts, txh)
            touched.add(tid)
    return touched


def sync_logs(conn, since_ts=None, step=500_000):
    """Тягне логи від курсора до поточного блоку; курсор зберігається після кожного вікна."""
    latest = latest_block()
    cur = meta_get(conn, "eth_next_block")
    if cur is None:
        cur = block_by_time(since_ts) if since_ts else first_block()
        log.info("ETH: початок з блоку %s", cur)
    else:
        cur = max(0, int(cur) - 10)  # невеликий відступ на реорги; вставки ідемпотентні
    cur = int(cur)
    touched = set()
    while cur <= latest:
        to = min(cur + step - 1, latest)
        logs = get_logs(cur, to)
        touched |= ingest_logs(conn, logs)
        if logs:  # курсор рухаємо лише коли є нові події (щоб БД не змінювалась щоразу)
            meta_set(conn, "eth_next_block", int(logs[-1]["blockNumber"], 16) + 1)
        elif to < latest:
            meta_set(conn, "eth_next_block", to + 1)
        conn.commit()
        log.info("ETH логи %s-%s: %s шт.", cur, to, len(logs))
        cur = to + 1
    return touched


def sync(conn, since_ts=None, stale_days=7):
    """Повний цикл ETH: логи -> нові id з RPC -> pending refresh -> derive."""
    touched = sync_logs(conn, since_ts)
    required = read_required()
    meta_set(conn, "eth_required", required)
    meta_set(conn, "eth_tx_count", read_count())
    missing = [r["tx_id"] for r in conn.execute(
        "SELECT tx_id FROM proposals WHERE chain='eth' AND destination IS NULL ORDER BY tx_id")]
    log.info("ETH: читаю on-chain %s пропозицій", len(missing))
    for i in range(0, len(missing), 200):
        part = missing[i:i + 200]
        data = read_transactions(part)
        for tid, d in data.items():
            is_usdt = "0x" + d["destination_hex"] == ETH_USDT
            dec = abi.decode_calldata(d["data"], is_usdt, lambda h: "0x" + h)
            update_proposal(conn, CHAIN, tid, destination="0x" + d["destination_hex"], function=dec["function"],
                            target=dec["target"], amount=dec["amount"], raw_data="0x" + d["data"],
                            executed=int(d["executed"]), required=required, chain_verified=1)
        conn.commit()
    # відкрити (не виконані) - оновити лічильник підтверджень і прапор executed з ланцюга
    open_ids = [r["tx_id"] for r in conn.execute(
        "SELECT tx_id FROM proposals WHERE chain='eth' AND executed=0 AND destination IS NOT NULL")]
    override = {}
    if open_ids:
        # одна пачка: transactions() + getConfirmationCount()
        for i in range(0, len(open_ids), 100):
            part = open_ids[i:i + 100]
            tx = read_transactions(part)
            cnt = read_conf_counts(part)
            for tid in part:
                override[tid] = cnt[tid]
                if tx[tid]["executed"]:
                    update_proposal(conn, CHAIN, tid, executed=1)
                update_proposal(conn, CHAIN, tid, required=required)
    derive_all(conn, CHAIN, stale_days=stale_days, conf_override=override)
    conn.commit()
    return touched
