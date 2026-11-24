"""USDT0 token events as the source of truth for executed freezes / unfreezes / burns.

The Safe Transaction Service is not a complete source (signers may execute without publishing). The token itself always
emits BlockPlaced / BlockReleased / DestroyedBlockedFunds, so those events are collected directly and then:
- linked to the Safe transaction that executed them (same transactionHash and target) -> real proposal time and delay;
- otherwise stored as an executed action with "no public proposal" (delay n/a, counted separately).
Event sources per chain (registry.EVENT_SOURCES): Etherscan V2, Blockscout / Etherscan-compatible APIs, or eth_getLogs
in adaptive block chunks. Cursors are stored in `meta`; nothing is rewritten when no new events appear."""
import logging
import re
import time

from . import eth, evmmsw
from .constants import (TOPIC_BLOCK_PLACED, TOPIC_BLOCK_RELEASED, TOPIC_DESTROYED_BLOCKED, USDT_DECIMALS)
from .db import ensure_proposal, meta_get, meta_set, update_proposal
from .registry import ERA_SPLIT, EVENT_SOURCES, evm_cfg
from .util import http_json, iso, parse_iso

log = logging.getLogger("freezewatch.tokenevents")
KIND_OF = {TOPIC_BLOCK_PLACED: "placed", TOPIC_BLOCK_RELEASED: "released", TOPIC_DESTROYED_BLOCKED: "destroyed"}
FUNC_OF = {"placed": "addToBlockedList", "released": "removeFromBlockedList", "destroyed": "destroyBlockedFunds"}
TOPIC_OWNERSHIP = "0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0"  # OwnershipTransferred
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


def stamp(c, rows):
    """Add the block timestamp (hex 'timeStamp', as Etherscan returns it) to raw RPC logs."""
    blocks = sorted({eth._h(x["blockNumber"]) for x in rows})
    ts = {}
    for i in range(0, len(blocks), 25):
        part = blocks[i:i + 25]
        for b, r in zip(part, evmmsw._batch(c, [("eth_getBlockByNumber", [hex(b), False]) for b in part])):
            ts[b] = int(r["timestamp"], 16)
    for x in rows:
        x["timeStamp"] = hex(ts[eth._h(x["blockNumber"])])


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
    stamp(c, rows)
    return rows, latest


def scan_state(conn, chain):
    g = lambda k: meta_get(conn, f"{chain}_scan_{k}")  # noqa: E731
    return {k: (int(g(k)) if g(k) not in (None, "") else None) for k in ("first", "lo", "hi", "latest")}


def plan_windows(lo, first, step, n):
    """Next `n` descending windows [(a, b)] below `lo` (exclusive), not going under `first`."""
    out, cur = [], lo
    while cur > first and len(out) < n:
        a = max(first, cur - step)
        out.append((a, cur - 1))
        cur = a
    return out


def _rpcscan(conn, cfg, spec, chain, budget=None):
    """Resumable eth_getLogs scan for chains whose RPC allows only small ranges (X Layer 100 blocks, Optimism 10,000).
    Every run scans the new blocks (hi+1..latest) first, then back-fills below `lo` towards the token's deploy block until
    the time budget is used. -> (rows, scanned_dict); the cursors are saved by the caller after the rows are stored."""
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor
    budget = budget if budget is not None else float(os.environ.get("FREEZEWATCH_SCAN_SEC", spec.get("budget", 60)))
    t0 = time.time()
    fwd_cap = 30  # seconds the "new blocks" phase may use; the back-fill below gets `budget` (0 = new blocks only)
    url, step, nb, workers = cfg["rpc"], spec.get("step", 100), spec.get("batch", 10), spec.get("parallel", 1)
    c = {"label": cfg["label"], "rpc": url, "msw": cfg["token"], "logs": {}}
    latest = int(eth.rpc_batch([("eth_blockNumber", [])], url=url)[0], 16)
    st = scan_state(conn, chain)
    first = st["first"]
    if first is None:
        first = evmmsw.creation_block(c, latest)
    lo, hi = (st["lo"], st["hi"]) if st["lo"] is not None else (latest + 1, latest)
    rows, err = [], None

    def run(windows):
        calls = [("eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(b), "address": cfg["token"], "topics": [TOPICS]}])
                 for a, b in windows]
        for attempt in range(5):
            try:
                res = eth.rpc_batch(calls, chunk=nb, url=url, retries=3)
                time.sleep(spec.get("pause", 0))
                return res
            except Exception as e:  # noqa: BLE001
                if attempt == 4 or not re.search(r"rate limit|too many|429|exceeded|capacity", str(e), re.I):
                    raise
                time.sleep(1.5 * (attempt + 1))

    def go(batches):
        """batches: list of window-lists, processed in order (possibly in parallel); stops at the first failure and returns
        the number of fully processed batches."""
        nonlocal err
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(run, b) for b in batches]
            for f in futs:
                try:
                    res = f.result()
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                    break
                for part in res:
                    rows.extend(part)
                done += 1
        return done

    # A. new blocks above hi
    cur = hi + 1
    while cur <= latest and not err and time.time() - t0 < fwd_cap:
        wins = []
        while cur <= latest and len(wins) < nb * workers:
            wins.append((cur, min(cur + step - 1, latest)))
            cur += step
        batches = [wins[i:i + nb] for i in range(0, len(wins), nb)]
        n = go(batches)
        covered_to = batches[n - 1][-1][1] if n else hi
        hi = max(hi, covered_to)
        if n < len(batches):
            break
    # B. back-fill below lo (towards the token's deploy block)
    while not err and lo > first and budget > 0 and time.time() - t0 < fwd_cap + budget:
        wins = plan_windows(lo, first, step, nb * workers)
        batches = [wins[i:i + nb] for i in range(0, len(wins), nb)]
        n = go(batches)
        if n:
            lo = batches[n - 1][-1][0]
        if n < len(batches):
            break
    stamp(c, rows)
    return rows, {"first": first, "lo": lo, "hi": hi, "latest": latest, "error": err}


def fetch(chain, from_block, conn=None, budget=None):
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
            if kind == "rpcscan":
                rows, state = _rpcscan(conn, cfg, arg, chain, budget)
                return rows, kind, state
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


def era_cutoff(conn, chain):
    """Epoch time when the token passed to the current (USDT0) owner; earlier events belong to the previous owner's era.
    Read once from OwnershipTransferred logs (Etherscan V2) and cached; None = no split for this chain."""
    cfg = ERA_SPLIT.get(chain)
    if not cfg:
        return None
    cached = meta_get(conn, f"{chain}_era_cutoff")
    if cached:
        return int(cached)
    v2 = next((s[1] for s in EVENT_SOURCES[chain] if s[0] == "v2"), None)
    if v2 is None:
        return None
    pad = "0x" + "0" * 24 + cfg["new_owner"].lower().removeprefix("0x")
    for lg in eth.get_logs(0, "latest", evm_cfg(chain)["token"], TOPIC_OWNERSHIP, v2):
        if len(lg.get("topics", [])) > 2 and lg["topics"][2].lower() == pad:
            ts = eth._h(lg["timeStamp"])
            meta_set(conn, f"{chain}_era_cutoff", ts)
            return ts
    return None


def reattribute(conn, chain, cutoff):
    """Event-only rows created under `chain` before the cutoff were attributed to the wrong era: drop them and let
    apply_events re-attribute (idempotent)."""
    if not cutoff:
        return 0
    c = iso(cutoff)
    n = conn.execute("DELETE FROM proposals WHERE chain=? AND public_proposal=0 AND executed_at<?", (chain, c)).rowcount
    conn.execute("UPDATE token_events SET linked=NULL WHERE chain=? AND ts<? AND linked=0", (chain, c))
    return n


def apply_events(conn, chain, cutoff=None):
    """Link events to proposals (same tx hash + target) or create event-only rows. Events older than `cutoff` are attributed
    to the legacy chain entry (previous owner, e.g. Tether's Gnosis MSW on Arbitrum). Idempotent."""
    cfg = evm_cfg(chain)
    legacy = (ERA_SPLIT.get(chain) or {}).get("legacy")
    created = linked = 0
    for ev in conn.execute("SELECT * FROM token_events WHERE chain=? AND linked IS NULL ORDER BY ts", (chain,)).fetchall():
        fn = FUNC_OF[ev["kind"]]
        target = legacy if (cutoff and legacy and parse_iso(ev["ts"]) < cutoff) else chain
        row = conn.execute(
            "SELECT tx_id, amount FROM proposals WHERE chain=? AND lower(exec_tx)=? AND lower(target)=? AND function=? "
            "AND public_proposal=1", (target, ev["tx_hash"], ev["address"].lower(), fn)).fetchone()
        if row:
            if ev["kind"] == "destroyed" and ev["amount"] is not None:
                update_proposal(conn, target, row["tx_id"], amount=ev["amount"])
            conn.execute("UPDATE token_events SET linked=1 WHERE chain=? AND tx_hash=? AND log_index=?",
                         (chain, ev["tx_hash"], ev["log_index"]))
            linked += 1
            continue
        tid = event_tx_id(ev["block"], ev["log_index"])
        ensure_proposal(conn, target, tid)
        update_proposal(conn, target, tid, destination=cfg["token"].lower(), function=fn, target=ev["address"],
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


def save_scan(conn, chain, state):
    """Persist the scan cursors; returns True if they moved."""
    old = scan_state(conn, chain)
    moved = (old["lo"], old["hi"]) != (state["lo"], state["hi"])
    for k in ("first", "lo", "hi", "latest"):
        meta_set(conn, f"{chain}_scan_{k}", state[k])
    return moved


def sync_chain(conn, chain, stale_days=7, backfill_sec=None):
    if not EVENT_SOURCES.get(chain):
        log.info("%s: no free event source configured, skipped", chain)
        return
    cur = int(meta_get(conn, f"{chain}_ev_next", 0) or 0)
    raw, kind, scanned = fetch(chain, max(0, cur - 5), conn, backfill_sec)
    events = [e for e in (parse_log(x) for x in raw) if e]
    n = store(conn, chain, events)
    scan_err = None
    if kind == "rpcscan":  # resumable scan: cursors, not ev_next, say where we are
        moved = save_scan(conn, chain, scanned)
        scan_err = scanned.get("error")
        if scanned["lo"] > scanned["first"]:
            log.info("%s: log scan covers blocks %s-%s of %s-%s", chain, scanned["lo"], scanned["hi"], scanned["first"],
                     scanned["latest"])
        if scanned["lo"] - 1 > scanned["first"]:
            ts = _block_ts(chain, scanned["lo"])
            if ts:
                meta_set(conn, f"{chain}_scan_lo_ts", ts)
    else:
        nxt = max(e["block"] for e in events) + 1 if events else cur
        if isinstance(scanned, int) and scanned - 2_000 > nxt and scanned - cur > 10_000:   # quiet scanned stretch: remember it
            nxt = scanned - 2_000
        if nxt != cur:
            meta_set(conn, f"{chain}_ev_next", nxt)
    meta_set(conn, f"{chain}_ev_source", kind)
    cutoff = era_cutoff(conn, chain)
    dropped = reattribute(conn, chain, cutoff)
    created, linked = apply_events(conn, chain, cutoff)
    fixed = relink(conn, chain)
    legacy = (ERA_SPLIT.get(chain) or {}).get("legacy")
    if legacy:
        fixed += relink(conn, legacy)
    if dropped:
        log.info("%s: re-attributed %s pre-USDT0 event rows to %s", chain, dropped, legacy)
    conn.commit()
    log.info("%s: %s events via %s (%s new), linked to Safe %s, event-only %s, merged %s", chain, len(events), kind, n,
             linked, created, fixed)
    if scan_err:
        if not moved:
            raise RuntimeError("log scan made no progress: " + scan_err)
        log.warning("%s: scan stopped early: %s", chain, scan_err[:100])


def _block_ts(chain, block):
    try:
        cfg = evm_cfg(chain)
        r = eth.rpc_batch([("eth_getBlockByNumber", [hex(block), False])], url=cfg["rpc"], retries=2)[0]
        return int(r["timestamp"], 16)
    except Exception:  # noqa: BLE001
        return None
