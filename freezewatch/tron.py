"""TRON: multisig call history from Tronscan (no key), state via TronGrid constant calls."""
import logging
import time

from . import abi
from .constants import (SEL_BALANCE_OF, SEL_CONF_COUNT, SEL_REQUIRED, SEL_TRANSACTIONS, SEL_TX_COUNT, TRON_DUMMY_CALLER,
                        TRON_MULTISIG, TRON_USDT, USDT_DECIMALS, SIG)
from .db import add_event, ensure_proposal, meta_get, meta_set, update_proposal
from .derive import derive_all
from . import deadline
from .util import HttpError, env, http_json, iso, key_pool, tron_from_hex20, tron_to_hex20

log = logging.getLogger("freezewatch.tron")
CALL_MAX_SEC = 45     # wall-clock cap of one TronGrid constant call including its retries
STALE_PER_RUN = 25    # stale / revoked open ids re-read per run (rotating)
CHAIN = "tron"
TRONGRID = "https://api.trongrid.io"
TRONSCAN = "https://apilist.tronscanapi.com/api/transaction"
DEFAULT_SINCE_MS = 1640995200000  # 2022-01-01; Tronscan serves multisig history from ~March 2022
WINDOW_MS = 14 * 86400 * 1000
METHODS = {"submitTransaction": "submit", "confirmTransaction": "confirm",
           "revokeConfirmation": "revoke", "executeTransaction": "execute"}


# ---------- TronGrid constant calls ----------
def constant_call(selector_hex_name: str, parameter: str = "", contract: str = TRON_MULTISIG) -> str:
    """selector_hex_name - signature, e.g. 'transactions(uint256)'. TronGrid keys rotate round-robin
    (TRONGRID_API_KEY may be a comma-separated list); an empty result or rate-limit reply cools the key down."""
    body = {"owner_address": TRON_DUMMY_CALLER, "contract_address": contract,
            "function_selector": selector_hex_name, "parameter": parameter, "visible": True}
    pool = key_pool("TRONGRID_API_KEY")
    interval = 0.06 / max(1, len(pool)) if len(pool) else 0.5
    last = None
    t_start = time.monotonic()
    for attempt in range(10 + 3 * len(pool)):
        if time.monotonic() - t_start > CALL_MAX_SEC or deadline.http_stop():   # bounded: a rate-limited TronGrid must not stall a run
            break
        key = pool.next()
        headers = {"TRON-PRO-API-KEY": key} if key else {}
        try:
            o = http_json("POST", f"{TRONGRID}/wallet/triggerconstantcontract", json=body, headers=headers,
                          interval=interval, retries=1)
            if o.get("constant_result"):
                return o["constant_result"][0]
            last = str(o)[:100]
        except HttpError as ex:
            last = str(ex)[:100]
        if key:
            pool.mark_bad(key, 20)
            log.debug("TronGrid %s cooling (%s)", pool.label(key), last[:40])
        time.sleep(min(1 + attempt, 8) if (not key or pool.cooling() >= len(pool)) else 0.1)
    raise HttpError(f"TronGrid: retries exhausted ({last})")


def pad_int(n):
    return f"{n:064x}"


def read_count():
    return int(constant_call(SIG["transactionCount"]), 16)


def read_required():
    return int(constant_call(SIG["required"]), 16)


def read_transaction(tid):
    return abi.decode_transactions_result(constant_call(SIG["transactions"], pad_int(tid)))


def read_conf_count(tid):
    return int(constant_call(SIG["getConfirmationCount"], pad_int(tid)), 16)


def balance_of(addr):
    param = "0" * 24 + tron_to_hex20(addr)
    return int(constant_call(SIG["balanceOf"], param, contract=TRON_USDT), 16) / 10 ** USDT_DECIMALS


# ---------- Tronscan ----------
def parse_call(item):
    """Tronscan item -> dict or None (foreign/unsuccessful/irrelevant calls are discarded)."""
    if item.get("contractRet") != "SUCCESS" or item.get("revert"):
        return None
    if item.get("toAddress") != TRON_MULTISIG:
        return None
    ti = item.get("trigger_info") or {}
    kind = METHODS.get(ti.get("methodName"))
    if not kind:
        return None
    par = ti.get("parameter") or {}
    row = {"tx_hash": item["hash"], "ts_ms": int(item["timestamp"]), "block": item.get("block"),
           "signer": item["ownerAddress"], "method": kind, "tx_id": None, "destination": None, "data": None}
    if kind == "submit":
        row["destination"] = par.get("destination")
        row["data"] = (par.get("data") or "").lower().removeprefix("0x")
    else:
        try:
            row["tx_id"] = int(par["transactionId"])
        except (KeyError, ValueError):
            return None
    return row


def fetch_window(start_ms, end_ms, depth=0):
    """All calls of the multisig address in [start_ms,end_ms]; splits the window if > ~9000 records."""
    rows, start, total = [], 0, None
    while True:
        o = http_json("GET", TRONSCAN, interval=0.35, validate=lambda x: "data" in x,
                      params={"address": TRON_MULTISIG, "start_timestamp": start_ms, "end_timestamp": end_ms,
                              "limit": 50, "start": start, "sort": "-timestamp"})
        items = o["data"]
        total = o.get("total", 0)
        if total > 9000 and end_ms - start_ms > 3600_000 and depth < 12:
            mid = (start_ms + end_ms) // 2
            return fetch_window(start_ms, mid, depth + 1) + fetch_window(mid + 1, end_ms, depth + 1)
        rows += items
        start += 50
        if not items or start >= total:
            return rows


def store_calls(conn, items):
    """Stores successful calls; returns the set of tx_ids touched by NEW calls."""
    touched = set()
    for it in items:
        c = parse_call(it)
        if not c:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO tron_calls(tx_hash,ts_ms,block,signer,method,tx_id,destination,data)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (c["tx_hash"], c["ts_ms"], c["block"], c["signer"], c["method"], c["tx_id"], c["destination"], c["data"]))
        if cur.rowcount and c["tx_id"] is not None:
            touched.add(c["tx_id"])
    return touched


def scan_calls(conn, since_ms=None):
    """Coverage [low, high] expands backward to since_ms and forward to now. Returns touched ids."""
    now_ms = int(time.time() * 1000)
    since_ms = since_ms or DEFAULT_SINCE_MS
    touched = set()
    high, low = meta_get(conn, "tron_scan_high"), meta_get(conn, "tron_scan_low")
    if high is None:
        high = low = now_ms
        meta_set(conn, "tron_scan_high", now_ms)
        meta_set(conn, "tron_scan_low", now_ms)
    else:
        high, low = int(high), int(low)
        start = high - 2 * 3600_000  # overlap, inserts are idempotent
        while start < now_ms:
            end = min(start + WINDOW_MS, now_ms)
            touched |= store_calls(conn, fetch_window(start, end))
            conn.commit()
            start = end
        if now_ms - high > 6 * 3600_000:  # high is updated rarely so the DB does not change every time
            meta_set(conn, "tron_scan_high", now_ms)
        conn.commit()
    while low > since_ms:
        start = max(low - WINDOW_MS, since_ms)
        items = fetch_window(start, low)
        store_calls(conn, items)
        low = start
        meta_set(conn, "tron_scan_low", low)
        conn.commit()
        log.info("TRON scan: covered from %s (%s calls in window)", iso(low // 1000), len(items))
    return touched


# ---------- proposal assembly ----------
def _apply_onchain(conn, tid, d, required):
    dest = tron_from_hex20(d["destination_hex"])
    dec = abi.decode_calldata(d["data"], dest == TRON_USDT, tron_from_hex20)
    update_proposal(conn, CHAIN, tid, destination=dest, function=dec["function"], target=dec["target"],
                    amount=dec["amount"], raw_data="0x" + d["data"], executed=int(d["executed"]), required=required,
                    chain_verified=1)


def _set_submit(conn, tid, s):
    conn.execute("DELETE FROM confirmations WHERE chain='tron' AND tx_id=? AND kind='submit'", (tid,))
    add_event(conn, CHAIN, tid, s["signer"], "submit", iso(s["ts_ms"] // 1000), s["tx_hash"])
    update_proposal(conn, CHAIN, tid, submitted_at=iso(s["ts_ms"] // 1000), submit_tx=s["tx_hash"], submitter=s["signer"])


def match_submissions(conn, ids=None):
    """Attach Tronscan submitTransaction calls to ids PER ID (no global offset): a submission matches an id with the
    same (destination, data); among unconsumed candidates take the latest one not after the id's first confirmation
    (confirmTransaction carries the id explicitly); without confirmations take the earliest. Ids with no candidate
    get submitted_at = first confirmation or first-seen time (flagged by submit_tx IS NULL). Returns (matched, unmatched)."""
    rows = conn.execute("SELECT tx_id, destination, raw_data, first_seen_at FROM proposals WHERE chain='tron' AND destination IS NOT NULL "
                        "AND submit_tx IS NULL ORDER BY tx_id").fetchall()
    if ids is not None:
        rows = [r for r in rows if r["tx_id"] in ids]
    if not rows:
        return 0, 0
    used = {r[0] for r in conn.execute("SELECT submit_tx FROM proposals WHERE chain='tron' AND submit_tx IS NOT NULL")}
    cands = {}
    for s in conn.execute("SELECT * FROM tron_calls WHERE method='submit' ORDER BY ts_ms"):
        if s["tx_hash"] not in used:
            cands.setdefault((s["destination"], s["data"]), []).append(dict(s))
    first_conf = {r["tx_id"]: r["ts"] for r in conn.execute(
        "SELECT tx_id, MIN(ts_ms) ts FROM tron_calls WHERE method='confirm' GROUP BY tx_id")}
    matched = unmatched = 0
    for r in rows:
        tid = r["tx_id"]
        lst = cands.get((r["destination"], (r["raw_data"] or "")[2:].lower()), [])
        fc = first_conf.get(tid)
        pick = None
        if lst:
            ok = [c for c in lst if fc is None or c["ts_ms"] <= fc]
            pick = (max(ok, key=lambda c: c["ts_ms"]) if fc is not None else min(ok, key=lambda c: c["ts_ms"])) if ok else None
        if pick:
            lst.remove(pick)
            _set_submit(conn, tid, pick)
            matched += 1
        else:
            unmatched += 1
            # flagged fallback (submit_tx stays NULL): first confirmation, else the moment we first saw the id on-chain
            update_proposal(conn, CHAIN, tid, submitted_at=iso(fc // 1000) if fc is not None else r["first_seen_at"])
    return matched, unmatched


def assemble(conn, since_ms=None, stale_days=7, budget_sec=None, touched=None, first_id=None):
    """On-chain state is the primary source: every id up to transactionCount-1 is read (destination, data, executed) and
    all open ids are refreshed every run. Tronscan only supplies timestamps (explicit ids for confirmations, per-id
    matching for submissions); nothing here depends on a global submission order, and a low match rate degrades
    gracefully instead of skipping the assembly."""
    t0 = time.time()
    count = read_count()
    required = read_required()
    meta_set(conn, "tron_tx_count", count)
    meta_set(conn, "tron_required", required)
    row = conn.execute("SELECT MIN(tx_id) lo, MAX(tx_id) hi FROM proposals WHERE chain='tron' AND destination IS NOT NULL").fetchone()
    if row["lo"] is None:  # empty DB: start at first_id, else where Tronscan history starts
        n_sub = conn.execute("SELECT COUNT(*) FROM tron_calls WHERE method='submit'").fetchone()[0]
        lo = first_id if first_id is not None else max(0, count - n_sub)
    else:
        lo = row["lo"]
    # explicit-id events (confirm / revoke / execute) for ids in range
    for r in conn.execute("SELECT * FROM tron_calls WHERE method!='submit' AND tx_id>=? AND tx_id<?", (lo, count)).fetchall():
        ensure_proposal(conn, CHAIN, r["tx_id"])
        add_event(conn, CHAIN, r["tx_id"], r["signer"], r["method"], iso(r["ts_ms"] // 1000), r["tx_hash"])
    conn.commit()
    # 1. ids we have never read from the chain (new ids and gaps), newest first
    have = {r[0] for r in conn.execute("SELECT tx_id FROM proposals WHERE chain='tron' AND destination IS NOT NULL")}
    missing = [i for i in range(count - 1, lo - 1, -1) if i not in have]
    read_new = 0
    for tid in missing:
        if budget_sec and time.time() - t0 > budget_sec * 1.5:
            log.warning("TRON: time budget used, %s ids still unread (they are read first next run)", len(missing) - read_new)
            break
        d = read_transaction(tid)
        ensure_proposal(conn, CHAIN, tid)
        _apply_onchain(conn, tid, d, required)
        if not d["executed"]:
            update_proposal(conn, CHAIN, tid, confirmations=read_conf_count(tid))
        read_new += 1
        if read_new % 10 == 0:
            conn.commit()
    conn.commit()
    # 2. refresh EVERY open id on-chain (executed flag; confirmations for the pending ones)
    override = {}
    open_rows = conn.execute("SELECT tx_id, status FROM proposals WHERE chain='tron' AND executed=0 AND destination IS NOT NULL "
                             "ORDER BY tx_id DESC").fetchall()
    # pending ids are re-read every run (they can execute any minute); the old stale / revoked ones (100+) only a few per run,
    # in rotation - re-reading all of them each run took many minutes whenever TronGrid rate-limited
    pend = [r for r in open_rows if r["status"] == "pending"]
    rest = [r for r in open_rows if r["status"] != "pending"]
    off = int(meta_get(conn, "tron_stale_offset", 0) or 0) % max(1, len(rest))
    rot = (rest[off:] + rest[:off])[:STALE_PER_RUN]
    meta_set(conn, "tron_stale_offset", off + STALE_PER_RUN)
    open_rows = pend + rot
    for r in open_rows:
        tid = r["tx_id"]
        if budget_sec and time.time() - t0 > budget_sec:
            log.warning("TRON: time budget used while refreshing open ids")
            break
        d = read_transaction(tid)
        update_proposal(conn, CHAIN, tid, executed=int(d["executed"]), required=required, chain_verified=1)
        if not d["executed"] and r["status"] == "pending":
            override[tid] = read_conf_count(tid)
    conn.commit()
    # 3. timestamps
    matched, unmatched = match_submissions(conn)
    if unmatched:
        log.warning("TRON: %s proposals have no matching submitTransaction in Tronscan (submitted_at falls back to the first "
                    "confirmation, %s matched this run)", unmatched, matched)
    derive_all(conn, CHAIN, stale_days=stale_days, conf_override=override)
    conn.commit()
    log.info("TRON: count=%s, read %s new ids, refreshed %s open ids, submissions matched %s / unmatched %s", count, read_new,
             len(open_rows), matched, unmatched)


def sync(conn, since_ts=None, stale_days=7, budget_sec=None):
    since_ms = since_ts * 1000 if since_ts else None
    touched = scan_calls(conn, since_ms)
    assemble(conn, since_ms, stale_days, budget_sec, touched)
