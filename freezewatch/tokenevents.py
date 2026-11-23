"""USDT0 token events as the source of truth for executed freezes / unfreezes / burns.

The Safe Transaction Service is not a complete source (signers may execute without publishing). The token itself always
emits BlockPlaced / BlockReleased / DestroyedBlockedFunds, so those events are collected directly and then:
- linked to the Safe transaction that executed them (same transactionHash and target) -> real proposal time and delay;
- otherwise stored as an executed action with "no public proposal" (delay n/a, counted separately).
Event sources per chain (registry.EVENT_SOURCES): Etherscan V2, Blockscout / Etherscan-compatible APIs, or eth_getLogs
in adaptive block chunks. Cursors are stored in `meta`; nothing is rewritten when no new events appear."""
import logging
import time

from . import eth, evmmsw
from .constants import (TOPIC_BLOCK_PLACED, TOPIC_BLOCK_RELEASED, TOPIC_DESTROYED_BLOCKED, USDT_DECIMALS)
from .db import ensure_proposal, meta_get, meta_set, update_proposal
from .registry import EVENT_SOURCES, evm_cfg
from .util import http_json, iso, parse_iso

log = logging.getLogger("freezewatch.tokenevents")
KIND_OF = {TOPIC_BLOCK_PLACED: "placed", TOPIC_BLOCK_RELEASED: "released", TOPIC_DESTROYED_BLOCKED: "destroyed"}
FUNC_OF = {"placed": "addToBlockedList", "released": "removeFromBlockedList", "destroyed": "destroyBlockedFunds"}
TOPICS = list(KIND_OF)
EVENT_SOURCES_KEYS = list(EVENT_SOURCES)  # chains with a configured event source
EVENT_ID_BASE = 10 ** 12  # tx_id of event-only rows: EVENT_ID_BASE + block * 1000 + log index


def event_tx_id(block, log_index):
    return EVENT_ID_BASE + int(block) * 1000 + int(log_index) % 1000


def parse_log(lg):
    """Etherscan-style log -> dict(kind, address, amount, ts, block, tx, log_index) or None."""
    t = [x.lower() for x in (lg.get("topics") or [])]
    if not t or t[0] not in KIND_OF or len(t) < 2:
        return None
    data = (lg.get("data") or "0x").removeprefix("0x")
    amount = int(data[-64:], 16) / 10 ** USDT_DECIMALS if len(data) >= 64 else None
    return {"kind": KIND_OF[t[0]], "address": "0x" + t[1][-40:], "amount": amount if KIND_OF[t[0]] == "destroyed" else None,
            "ts": eth._h(lg.get("timeStamp", 0)), "block": eth._h(lg.get("blockNumber", 0)),
            "tx": (lg.get("transactionHash") or "").lower(), "log_index": eth._h(lg.get("logIndex", 0))}


# ---------------- sources ----------------
def _v2(cfg, chainid, from_block):
    rows = []
    for topic in TOPICS:
        rows += eth.get_logs(from_block, "latest", cfg["token"], topic, chainid)
    return rows


def _blockscout(cfg, url, from_block):
    """Blockscout / Etherscan-compatible API: one topic0 at a time, paged by 1000. 'No logs found' = empty."""
    rows = []
    for topic in TOPICS:
        page = 1
        while True:
            o = http_json("GET", url, interval=0.5, retries=2, timeout=25,
                          params={"module": "logs", "action": "getLogs", "address": cfg["token"], "topic0": topic,
                                  "fromBlock": from_block, "toBlock": "latest", "page": page, "offset": 1000},
                          headers={"User-Agent": "usdt-freeze-alert"})
            res = o.get("result") if isinstance(o.get("result"), list) else []
            rows += res
            if len(res) < 1000:
                break
            page += 1
    return rows


def _rpc(cfg, spec, from_block):
    """eth_getLogs with a topic0 OR-filter in adaptive chunks, then block timestamps."""
    c = {"label": cfg["label"], "rpc": cfg["rpc"], "msw": cfg["token"], "logs": {}}
    latest = int(eth.rpc_batch([("eth_blockNumber", [])], url=cfg["rpc"])[0], 16)
    start = from_block if from_block > 0 else evmmsw.creation_block(c, latest)
    step = spec.get("chunk", 100_000)
    rows, cur = [], start
    while cur <= latest:
        to = min(cur + step - 1, latest)
        try:
            res = eth.rpc_batch([("eth_getLogs", [{"fromBlock": hex(cur), "toBlock": hex(to), "address": cfg["token"],
                                                   "topics": [TOPICS]}])], url=cfg["rpc"], retries=2)[0]
        except Exception as e:  # noqa: BLE001
            if step <= 500:
                raise
            step = max(500, step // 2)
            log.info("%s: range error (%s), chunk -> %s", cfg["label"], str(e)[:50], step)
            continue
        rows += res
        cur = to + 1
    blocks = sorted({eth._h(x["blockNumber"]) for x in rows})
    ts = {}
    for i in range(0, len(blocks), 25):
        part = blocks[i:i + 25]
        for b, r in zip(part, evmmsw._batch(c, [("eth_getBlockByNumber", [hex(b), False]) for b in part])):
            ts[b] = int(r["timestamp"], 16)
    for x in rows:
        x["timeStamp"] = hex(ts[eth._h(x["blockNumber"])])
    return rows, latest


def fetch(chain, from_block):
    """Try the configured sources in order; the first that answers (even with zero events) wins."""
    cfg = evm_cfg(chain)
    last = None
    for src in EVENT_SOURCES[chain]:
        kind, arg = src[0], src[1]
        try:
            if kind == "v2":
                return _v2(cfg, arg, from_block), kind, None
            if kind == "blockscout":
                return _blockscout(cfg, arg, from_block), kind, None
            if kind == "rpc":
                rows, scanned = _rpc(cfg, arg, from_block)
                return rows, kind, scanned
        except Exception as e:  # noqa: BLE001
            last = e
            log.info("%s: source %s failed (%s)", chain, kind, str(e)[:80])
    raise RuntimeError(f"no event source worked for {chain}: {str(last)[:100]}")


# ---------------- storage and linking ----------------
def store(conn, chain, events):
    n = 0
    for e in events:
        n += conn.execute(
            "INSERT OR IGNORE INTO token_events(chain,tx_hash,log_index,kind,address,amount,ts,block) VALUES(?,?,?,?,?,?,?,?)",
            (chain, e["tx"], e["log_index"], e["kind"], e["address"], e["amount"], iso(e["ts"]), e["block"])).rowcount
    return n


def apply_events(conn, chain):
    """Link events to Safe proposals (same tx hash + target) or create event-only rows. Idempotent."""
    cfg = evm_cfg(chain)
    created = linked = 0
    for ev in conn.execute("SELECT * FROM token_events WHERE chain=? AND linked IS NULL ORDER BY ts", (chain,)).fetchall():
        fn = FUNC_OF[ev["kind"]]
        row = conn.execute(
            "SELECT tx_id, amount FROM proposals WHERE chain=? AND lower(exec_tx)=? AND lower(target)=? AND function=? "
            "AND public_proposal=1", (chain, ev["tx_hash"], ev["address"].lower(), fn)).fetchone()
        if row:
            if ev["kind"] == "destroyed" and ev["amount"] is not None:
                update_proposal(conn, chain, row["tx_id"], amount=ev["amount"])
            conn.execute("UPDATE token_events SET linked=1 WHERE chain=? AND tx_hash=? AND log_index=?",
                         (chain, ev["tx_hash"], ev["log_index"]))
            linked += 1
            continue
        tid = event_tx_id(ev["block"], ev["log_index"])
        ensure_proposal(conn, chain, tid)
        update_proposal(conn, chain, tid, destination=cfg["token"].lower(), function=fn, target=ev["address"],
                        amount=ev["amount"], executed=1, executed_at=ev["ts"], exec_tx=ev["tx_hash"], status="executed",
                        confirmations=0, required=None, chain_verified=1, public_proposal=0)
        conn.execute("UPDATE token_events SET linked=0 WHERE chain=? AND tx_hash=? AND log_index=?",
                     (chain, ev["tx_hash"], ev["log_index"]))
        created += 1
    conn.commit()
    return created, linked


def relink(conn, chain):
    """After a fuller Safe sync, event-only rows may now match a Safe transaction: merge them."""
    fixed = 0
    for r in conn.execute("SELECT tx_id, exec_tx, target, function, amount FROM proposals WHERE chain=? AND public_proposal=0",
                          (chain,)).fetchall():
        m = conn.execute("SELECT tx_id FROM proposals WHERE chain=? AND lower(exec_tx)=? AND lower(target)=? AND function=? "
                         "AND public_proposal=1", (chain, (r["exec_tx"] or "").lower(), (r["target"] or "").lower(),
                                                   r["function"])).fetchone()
        if m:
            if r["amount"] is not None:
                update_proposal(conn, chain, m["tx_id"], amount=r["amount"])
            conn.execute("DELETE FROM proposals WHERE chain=? AND tx_id=?", (chain, r["tx_id"]))
            conn.execute("UPDATE token_events SET linked=1 WHERE chain=? AND tx_hash=? AND lower(address)=?",
                         (chain, (r["exec_tx"] or "").lower(), (r["target"] or "").lower()))
            fixed += 1
    conn.commit()
    return fixed


def sync_chain(conn, chain, stale_days=7):
    if not EVENT_SOURCES.get(chain):
        log.info("%s: no free event source configured, skipped", chain)
        return
    cur = int(meta_get(conn, f"{chain}_ev_next", 0) or 0)
    raw, kind, scanned = fetch(chain, max(0, cur - 5))
    events = [e for e in (parse_log(x) for x in raw) if e]
    n = store(conn, chain, events)
    if events:
        meta_set(conn, f"{chain}_ev_next", max(e["block"] for e in events) + 1)
    elif scanned and scanned - cur > 500_000:  # long quiet scan: remember it, but rarely (keeps the DB file stable)
        meta_set(conn, f"{chain}_ev_next", scanned - 100_000)
    meta_set(conn, f"{chain}_ev_source", kind)
    created, linked = apply_events(conn, chain)
    fixed = relink(conn, chain)
    conn.commit()
    log.info("%s: %s events via %s (%s new), linked to Safe %s, event-only %s, merged %s", chain, len(events), kind, n,
             linked, created, fixed)
