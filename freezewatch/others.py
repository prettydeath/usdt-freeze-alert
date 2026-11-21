"""Виконані заморожування БЕЗ on-chain черги: USDC (Ethereum) та USDC/USDT (Solana).
Затримку подання->виконання тут виміряти неможливо (N/A): рішення ухвалюється поза ланцюгом."""
import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from . import eth
from .constants import (SOLANA_SOURCES, SEL_BALANCE_OF, TOPIC_USDC_BLACKLISTED, TOPIC_USDC_UNBLACKLISTED, USDC_ETH,
                        USDT_DECIMALS)
from .db import meta_get, meta_set
from .util import b58encode, env, http_json, iso, now_ts

log = logging.getLogger("freezewatch.others")
FREEZE_TYPES = {"freezeAccount": "freeze", "thawAccount": "unfreeze"}


def add_freeze(conn, source, subject, owner, action, ts_iso, tx_hash, block=None, amount=None, amount_kind=None,
               signers=None):
    cur = conn.execute(
        "INSERT OR IGNORE INTO freezes(source,subject,owner,action,ts,tx_hash,block,amount,amount_kind,signers,first_seen_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (source, subject, owner, action, ts_iso, tx_hash, block, amount, amount_kind, signers, iso(now_ts())))
    return bool(cur.rowcount)


# ======================= USDC на Ethereum =======================
def sync_usdc_eth(conn, since_ts=None, step=2_000_000):
    latest = eth.latest_block()
    cur = meta_get(conn, "usdc_eth_next_block")
    if cur is None:
        cur = eth.block_by_time(since_ts) if since_ts else eth.first_block(USDC_ETH, TOPIC_USDC_BLACKLISTED)
        log.info("USDC/ETH: початок з блоку %s", cur)
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
            log.info("USDC/ETH %s %s-%s: %s шт.", action, cur, to, len(logs))
        if last_block:
            meta_set(conn, "usdc_eth_next_block", last_block + 1)
        elif to < latest:
            meta_set(conn, "usdc_eth_next_block", to + 1)
        conn.commit()
        cur = to + 1
    # баланси (поточні; для заблокованої адреси переказ неможливий, тож це добре наближає суму під блокуванням)
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


def _ok(o):
    e = o.get("error")
    return not (e and (e.get("code") in (429, -32005, -32004, -32603) or "rate" in str(e.get("message", "")).lower()))


def sol_rpc(method, params):
    o = http_json("POST", sol_url(), json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                  interval=0.5, retries=12, validate=_ok)
    if "error" in o:
        raise RuntimeError(f"Solana RPC {method}: {o['error']}")
    return o["result"]


def parse_multisig(raw: bytes):
    """SPL Multisig (355 байт): m u8, n u8, is_initialized u8, signers[11] x 32."""
    if len(raw) < 355:
        raise ValueError("не схоже на SPL Multisig")
    m, n, init = raw[0], raw[1], raw[2]
    signers = [b58encode(raw[3 + i * 32:3 + (i + 1) * 32]) for i in range(n)]
    return {"m": m, "n": n, "initialized": bool(init), "signers": signers}


def extract_freezes(tx, mint):
    """Із розібраної (jsonParsed) транзакції -> список freeze/thaw інструкцій для потрібного mint.
    Адреса-авторитет трапляється й у сторонніх транзакціях, тому фільтр за mint і типом обов'язковий."""
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
    """null від публічного RPC буває тимчасовим (відстає вузол) - кілька повторів, інакше None."""
    for attempt in range(4):
        tx = sol_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if tx is not None:
            return tx
        time.sleep(2 * (attempt + 1))
    return None


def _process_sigs(conn, source, cfg, sigs, since_ts=None, workers=6):
    """Паралельне читання транзакцій (затримка публічного RPC ~2-3 с), запис у БД послідовно."""
    todo = [s for s in sigs if s.get("err") is None
            and not (since_ts and s.get("blockTime") and s["blockTime"] < since_ts)]
    n_new = 0
    nulls = set()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for s, tx in zip(todo, ex.map(lambda x: _fetch_tx(x["signature"]), todo)):
            if tx is None:
                log.warning("%s: getTransaction=null для %s", source, s["signature"])
                nulls.add(s["signature"])
                continue
            for f in extract_freezes(tx, cfg["mint"]):
                if add_freeze(conn, source, f["account"], f["owner"], f["action"], iso(tx["blockTime"]),
                              s["signature"], tx.get("slot"), f["amount"], "at_tx", ",".join(f["signers"])):
                    n_new += 1
                    conn.commit()
    key = f"{source}_null_sigs"
    cur = set(json.loads(meta_get(conn, key) or "[]"))
    cur = (cur - {s["signature"] for s in todo}) | nulls  # невдалі підписи повторюються при наступному запуску
    meta_set(conn, key, json.dumps(sorted(cur)))
    conn.commit()
    return n_new


def check_authority(conn, source, cfg):
    """Читає SPL Multisig авторитету; повертає опис зміни m/n/підписантів або None."""
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
    """Інкрементально (until=newest) + назад до since_ts (before=oldest), курсори зберігаються по сторінках.
    За замовчуванням історія - 180 днів назад; --since може заглибити."""
    cfg = SOLANA_SOURCES[source]
    t0 = time.time()
    auth = cfg["authority"]
    changes = []
    ch = check_authority(conn, source, cfg)
    if ch:
        changes.append(ch)
    retry = json.loads(meta_get(conn, f"{source}_null_sigs") or "[]")
    if retry:
        log.info("%s: повторюю %s підписів, що раніше повернули null", source, len(retry))
        _process_sigs(conn, source, cfg, [{"signature": s, "err": None} for s in retry])
    newest = meta_get(conn, f"{source}_newest_sig")
    oldest = meta_get(conn, f"{source}_oldest_sig")
    oldest_ts = meta_get(conn, f"{source}_oldest_ts")
    complete = meta_get(conn, f"{source}_complete") == "1"
    since_ts = since_ts or (now_ts() - 180 * 86400)
    # A. нове (вперед)
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
    # B. історія назад
    before = oldest
    first = newest is None
    while not complete and (oldest_ts is None or int(oldest_ts) > since_ts):
        if budget_sec and time.time() - t0 > budget_sec:
            log.warning("%s: бюджет часу вичерпано, історія продовжиться наступним запуском", source)
            break
        page = _sigs(auth, before=before)
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
        log.info("%s: сторінка до %s, нових фризів %s", source, iso(oldest_ts), n)
    return changes
