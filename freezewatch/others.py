"""Executed freezes WITHOUT an on-chain queue: USDC (Ethereum) and USDC/USDT (Solana).
The submission->execution delay cannot be measured here (N/A): the decision is made off-chain."""
import base64
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import eth
from .constants import (SOLANA_SOURCES, SEL_BALANCE_OF, TOPIC_USDC_BLACKLISTED, TOPIC_USDC_UNBLACKLISTED, USDC_ETH,
                        USDT_DECIMALS)
from .db import meta_get, meta_set
from .util import _session, b58encode, env, iso, now_ts

log = logging.getLogger("freezewatch.others")
FREEZE_TYPES = {"freezeAccount": "freeze", "thawAccount": "unfreeze"}


def add_freeze(conn, source, subject, owner, action, ts_iso, tx_hash, block=None, amount=None, amount_kind=None,
               signers=None):
    cur = conn.execute(
        "INSERT OR IGNORE INTO freezes(source,subject,owner,action,ts,tx_hash,block,amount,amount_kind,signers,first_seen_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (source, subject, owner, action, ts_iso, tx_hash, block, amount, amount_kind, signers, iso(now_ts())))
    return bool(cur.rowcount)


# ======================= USDC on Ethereum =======================
def sync_usdc_eth(conn, since_ts=None, step=2_000_000):
    if not env("ETHERSCAN_API_KEY"):
        log.warning("ETHERSCAN_API_KEY is not set: skipping USDC Ethereum")
        return
    latest = eth.latest_block()
    cur = meta_get(conn, "usdc_eth_next_block")
    if cur is None:
        cur = eth.block_by_time(since_ts) if since_ts else eth.first_block(USDC_ETH, TOPIC_USDC_BLACKLISTED)
        log.info("USDC/ETH: starting from block %s", cur)
    else:
        cur = max(0, int(cur) - 10)
    cur = int(cur)
    while cur <= latest:
        to = min(cur + step - 1, latest)
        last_block = None
        for topic, action in ((TOPIC_USDC_BLACKLISTED, "freeze"), (TOPIC_USDC_UNBLACKLISTED, "unfreeze")):
            logs = eth.get_logs(cur, to, USDC_ETH, topic)
            for lg in logs:
                addr = "0x" + lg["topics"][1][-40:]
                bn = int(lg["blockNumber"], 16)
                add_freeze(conn, "usdc_eth", addr, addr, action, iso(eth._h(lg["timeStamp"])), lg["transactionHash"], bn)
                last_block = max(last_block or 0, bn)
            log.info("USDC/ETH %s %s-%s: %s entries", action, cur, to, len(logs))
        if last_block:
            meta_set(conn, "usdc_eth_next_block", last_block + 1)
        elif to < latest:
            meta_set(conn, "usdc_eth_next_block", to + 1)
        conn.commit()
        cur = to + 1
    # balances (current; a blocked address cannot transfer, so this closely approximates the amount under the block)
    rows = conn.execute("SELECT subject FROM freezes WHERE source='usdc_eth' AND action='freeze' AND amount IS NULL"
                        " GROUP BY subject").fetchall()
    subs = [r["subject"] for r in rows]
    for i in range(0, len(subs), 50):
        part = subs[i:i + 50]
        res = eth.rpc_batch([("eth_call", [{"to": USDC_ETH, "data": "0x" + SEL_BALANCE_OF + "0" * 24 + a[2:]}, "latest"])
                             for a in part])
        for a, v in zip(part, res):
            conn.execute("UPDATE freezes SET amount=?, amount_kind='current' WHERE source='usdc_eth' AND subject=? "
                         "AND action='freeze' AND amount IS NULL", (int(v, 16) / 10 ** USDT_DECIMALS, a))
        conn.commit()


# ======================= Solana =======================
def sol_url():
    return env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


_sol_lock = threading.Lock()
_sol_state = {"next": 0.0, "pause": 0.0}


def sol_rpc(method, params, retries=40):
    """JSON-RPC with a shared adaptive limiter: ~4 req/s; any 429 pauses ALL threads (growing 2..20 s) instead of
    per-thread exponential backoff, which is much faster on the rate-limited public endpoint."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last = None
    for _ in range(retries):
        with _sol_lock:
            now = time.monotonic()
            slot = max(now, _sol_state["next"])
            _sol_state["next"] = slot + 0.25
        if slot > now:
            time.sleep(slot - now)
        try:
            r = _session.post(sol_url(), json=body, timeout=40)
            o = r.json() if r.status_code != 429 else {"error": {"code": 429}}
        except Exception as e:  # noqa: BLE001
            last = repr(e)
            time.sleep(2)
            continue
        e = o.get("error")
        if e and (e.get("code") in (429, -32005, -32004, -32603) or "rate" in str(e.get("message", "")).lower()):
            with _sol_lock:
                _sol_state["pause"] = min(20.0, max(2.0, _sol_state["pause"] * 1.5))
                _sol_state["next"] = max(_sol_state["next"], time.monotonic() + _sol_state["pause"])
            last = str(e)[:80]
            continue
        with _sol_lock:
            _sol_state["pause"] = max(0.0, _sol_state["pause"] * 0.8)
        if e:
            raise RuntimeError(f"Solana RPC {method}: {e}")
        return o["result"]
    raise RuntimeError(f"Solana RPC {method}: retries exhausted ({last})")


def parse_multisig(raw: bytes):
    """SPL Multisig (355 bytes): m u8, n u8, is_initialized u8, signers[11] x 32."""
    if len(raw) < 355:
        raise ValueError("does not look like an SPL Multisig")
    m, n, init = raw[0], raw[1], raw[2]
    signers = [b58encode(raw[3 + i * 32:3 + (i + 1) * 32]) for i in range(n)]
    return {"m": m, "n": n, "initialized": bool(init), "signers": signers}


def extract_freezes(tx, mint):
    """From a parsed (jsonParsed) transaction -> list of freeze/thaw instructions for the required mint.
    The authority address also appears in unrelated transactions, so filtering by mint and type is mandatory."""
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return []
    msg = tx["transaction"]["message"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in msg["accountKeys"]]
    instrs = list(msg["instructions"])
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        instrs += inner.get("instructions", [])
    bal = {}
    for b in (tx["meta"].get("postTokenBalances") or []) + (tx["meta"].get("preTokenBalances") or []):
        bal.setdefault(b["accountIndex"], b)
    out = []
    for ins in instrs:
        p = ins.get("parsed")
        if not isinstance(p, dict) or not str(ins.get("program", "")).startswith("spl-token"):
            continue
        action = FREEZE_TYPES.get(p.get("type"))
        info = p.get("info") or {}
        if not action or info.get("mint") != mint:
            continue
        acct = info["account"]
        b = bal.get(keys.index(acct)) if acct in keys else None
        signers = info.get("signers") or [info.get("freezeAuthority") or info.get("multisigFreezeAuthority")]
        out.append({"action": action, "account": acct,
                    "owner": b.get("owner") if b else None,
                    "amount": float(b["uiTokenAmount"].get("uiAmountString") or 0) if b else None,
                    "signers": [s for s in signers if s],
                    "authority": info.get("multisigFreezeAuthority") or info.get("freezeAuthority")})
    return out


def _sigs(authority, before=None, until=None, limit=250):
    opt = {"limit": limit}
    if before:
        opt["before"] = before
    if until:
        opt["until"] = until
    return sol_rpc("getSignaturesForAddress", [authority, opt])


def _fetch_tx(sig):
    """null from a public RPC can be transient (node lagging) - a few retries, otherwise None."""
    for attempt in range(4):
        tx = sol_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if tx is not None:
            return tx
        time.sleep(2 * (attempt + 1))
    return None


def _process_sigs(conn, source, cfg, sigs, since_ts=None, workers=8):
    """Parallel transaction reads (public RPC latency ~2-3 s), sequential DB writes."""
    todo = [s for s in sigs if s.get("err") is None
            and not (since_ts and s.get("blockTime") and s["blockTime"] < since_ts)]
    n_new = 0
    nulls = set()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for s, tx in zip(todo, ex.map(lambda x: _fetch_tx(x["signature"]), todo)):
            if tx is None:
                log.warning("%s: getTransaction=null for %s", source, s["signature"])
                nulls.add(s["signature"])
                continue
            for f in extract_freezes(tx, cfg["mint"]):
                if add_freeze(conn, source, f["account"], f["owner"], f["action"], iso(tx["blockTime"]),
                              s["signature"], tx.get("slot"), f["amount"], "at_tx", ",".join(f["signers"])):
                    n_new += 1
                    conn.commit()
    key = f"{source}_null_sigs"
    cur = set(json.loads(meta_get(conn, key) or "[]"))
    cur = (cur - {s["signature"] for s in todo}) | nulls  # failed signatures are retried on the next run
    meta_set(conn, key, json.dumps(sorted(cur)))
    conn.commit()
    return n_new


def check_authority(conn, source, cfg):
    """Reads the authority SPL Multisig; returns a description of the m/n/signers change or None."""
    v = sol_rpc("getAccountInfo", [cfg["authority"], {"encoding": "base64"}])["value"]
    if not v:
        return None
    ms = parse_multisig(base64.b64decode(v["data"][0]))
    sig = ",".join(ms["signers"])
    row = conn.execute("SELECT * FROM authority_state WHERE source=?", (source,)).fetchone()
    if row and (row["m"], row["n"], row["signers"]) == (ms["m"], ms["n"], sig):
        return None
    conn.execute("INSERT INTO authority_state(source,authority,m,n,signers,updated_at) VALUES(?,?,?,?,?,?) "
                 "ON CONFLICT(source) DO UPDATE SET m=excluded.m,n=excluded.n,signers=excluded.signers,"
                 "updated_at=excluded.updated_at", (source, cfg["authority"], ms["m"], ms["n"], sig, iso(now_ts())))
    conn.commit()
    if row is None:
        log.info("%s: multisig %s-of-%s: %s", source, ms["m"], ms["n"], sig)
        return None
    return {"source": source, "old": (row["m"], row["n"], row["signers"]), "new": (ms["m"], ms["n"], sig)}


def sync_solana(conn, source, since_ts=None, budget_sec=None):
    """Incrementally (until=newest) + backwards to since_ts (before=oldest), cursors saved per page.
    By default history goes back 180 days; --since can go deeper."""
    cfg = SOLANA_SOURCES[source]
    t0 = time.time()
    auth = cfg["authority"]
    changes = []
    ch = check_authority(conn, source, cfg)
    if ch:
        changes.append(ch)
    retry = json.loads(meta_get(conn, f"{source}_null_sigs") or "[]")
    if retry:
        log.info("%s: retrying %s signatures that previously returned null", source, len(retry))
        _process_sigs(conn, source, cfg, [{"signature": s, "err": None} for s in retry])
    newest = meta_get(conn, f"{source}_newest_sig")
    oldest = meta_get(conn, f"{source}_oldest_sig")
    oldest_ts = meta_get(conn, f"{source}_oldest_ts")
    complete = meta_get(conn, f"{source}_complete") == "1"
    since_ts = since_ts or (now_ts() - 180 * 86400)
    # A. new (forward)
    if newest:
        collected, before = [], None
        while True:
            page = _sigs(auth, before=before, until=newest)
            collected += page
            if len(page) < 250:
                break
            before = page[-1]["signature"]
        if collected:
            _process_sigs(conn, source, cfg, collected)
            meta_set(conn, f"{source}_newest_sig", collected[0]["signature"])
            conn.commit()
    # B. history backward
    before = oldest
    first = newest is None
    # history is optional work: in scheduled runs keep it short so the whole run fits the cron interval
    hist_budget = int(os.environ.get("FREEZEWATCH_SOL_HISTORY_SEC", "0") or 0) or budget_sec
    page_limit = 50 if hist_budget and hist_budget <= 300 else 250
    while not complete and (oldest_ts is None or int(oldest_ts) > since_ts):
        if hist_budget and time.time() - t0 > hist_budget:
            log.warning("%s: time budget exhausted, history will continue on the next run", source)
            break
        page = _sigs(auth, before=before, limit=page_limit)
        if not page:
            complete = True
            meta_set(conn, f"{source}_complete", "1")
            break
        n = _process_sigs(conn, source, cfg, page, since_ts)
        if first:
            meta_set(conn, f"{source}_newest_sig", page[0]["signature"])
            first = False
        before = page[-1]["signature"]
        oldest_ts = page[-1].get("blockTime") or 0
        meta_set(conn, f"{source}_oldest_sig", before)
        meta_set(conn, f"{source}_oldest_ts", oldest_ts)
        conn.commit()
        log.info("%s: page up to %s, new freezes %s", source, iso(oldest_ts), n)
    return changes
