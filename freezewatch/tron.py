"""TRON: історія викликів мультисига з Tronscan (без ключа), стан через TronGrid constant calls."""
import logging
import time

from . import abi
from .constants import (SEL_BALANCE_OF, SEL_CONF_COUNT, SEL_REQUIRED, SEL_TRANSACTIONS, SEL_TX_COUNT, TRON_DUMMY_CALLER,
                        TRON_MULTISIG, TRON_USDT, USDT_DECIMALS, SIG)
from .db import add_event, ensure_proposal, events_grouped, meta_get, meta_set, update_proposal
from .derive import derive_all, infer_exec
from .util import HttpError, env, http_json, iso, tron_from_hex20, tron_to_hex20

log = logging.getLogger("freezewatch.tron")
CHAIN = "tron"
TRONGRID = "https://api.trongrid.io"
TRONSCAN = "https://apilist.tronscanapi.com/api/transaction"
DEFAULT_SINCE_MS = 1640995200000  # 2022-01-01; Tronscan віддає історію мультисига з ~березня 2022
WINDOW_MS = 14 * 86400 * 1000
METHODS = {"submitTransaction": "submit", "confirmTransaction": "confirm",
           "revokeConfirmation": "revoke", "executeTransaction": "execute"}


# ---------- TronGrid constant calls ----------
def _grid_headers():
    k = env("TRONGRID_API_KEY")
    return {"TRON-PRO-API-KEY": k} if k else {}


def constant_call(selector_hex_name: str, parameter: str = "", contract: str = TRON_MULTISIG) -> str:
    """selector_hex_name - сигнатура, напр. 'transactions(uint256)'."""
    body = {"owner_address": TRON_DUMMY_CALLER, "contract_address": contract,
            "function_selector": selector_hex_name, "parameter": parameter, "visible": True}
    interval = 0.06 if env("TRONGRID_API_KEY") else 0.5
    o = http_json("POST", f"{TRONGRID}/wallet/triggerconstantcontract", json=body, headers=_grid_headers(),
                  interval=interval, retries=8, validate=lambda x: bool(x.get("constant_result")))
    return o["constant_result"][0]


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
    """Елемент Tronscan -> dict або None (чужі/неуспішні/нерелевантні виклики відкидаються)."""
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
    """Усі виклики адреси мультисига у [start_ms,end_ms]; ділить вікно, якщо > ~9000 записів."""
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
    """Зберігає успішні виклики; повертає множину tx_id, зачеплених НОВИМИ викликами."""
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
    """Покриття [low, high] розширюється назад до since_ms і вперед до now. Повертає touched ids."""
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
        start = high - 2 * 3600_000  # перекриття, вставки ідемпотентні
        while start < now_ms:
            end = min(start + WINDOW_MS, now_ms)
            touched |= store_calls(conn, fetch_window(start, end))
            conn.commit()
            start = end
        if now_ms - high > 6 * 3600_000:  # high оновлюється рідко, щоб БД не мінялась щоразу
            meta_set(conn, "tron_scan_high", now_ms)
        conn.commit()
    while low > since_ms:
        start = max(low - WINDOW_MS, since_ms)
        items = fetch_window(start, low)
        store_calls(conn, items)
        low = start
        meta_set(conn, "tron_scan_low", low)
        conn.commit()
        log.info("TRON скан: покрито з %s (%s викликів у вікні)", iso(low // 1000), len(items))
    return touched


# ---------- збірка пропозицій ----------
def _calibrate(conn, subs, count, n, sample=25):
    """Вибір зсуву k (затримка індексації Tronscan) за збігом data найновіших id."""
    ids = list(range(max(0, count - sample), count))
    onchain = {i: read_transaction(i) for i in ids}
    best, best_k = -1, 0
    for k in range(0, 16):
        off = count - n - k
        score = 0
        for i in ids:
            r = i - off
            if 0 <= r < n and subs[r]["data"] == onchain[i]["data"]:
                score += 1
        if score > best:
            best, best_k = score, k
    return best_k, best / max(1, len(ids)), onchain


def _apply_onchain(conn, tid, d, required):
    dest = tron_from_hex20(d["destination_hex"])
    dec = abi.decode_calldata(d["data"], dest == TRON_USDT, tron_from_hex20)
    update_proposal(conn, CHAIN, tid, destination=dest, function=dec["function"], target=dec["target"],
                    amount=dec["amount"], raw_data="0x" + d["data"], executed=int(d["executed"]), required=required,
                    chain_verified=1)


def _apply_submit_data(conn, tid, sub, required):
    """Швидкий шлях без читання з ланцюга: поля з параметрів submitTransaction (Tronscan)."""
    dest = sub["destination"]
    dec = abi.decode_calldata(sub["data"], dest == TRON_USDT, tron_from_hex20)
    update_proposal(conn, CHAIN, tid, destination=dest, function=dec["function"], target=dec["target"],
                    amount=dec["amount"], raw_data="0x" + sub["data"], required=required)


def _set_submit(conn, tid, s):
    conn.execute("DELETE FROM confirmations WHERE chain='tron' AND tx_id=? AND kind='submit'", (tid,))
    add_event(conn, CHAIN, tid, s["signer"], "submit", iso(s["ts_ms"] // 1000), s["tx_hash"])


def assemble(conn, since_ms=None, stale_days=7, budget_sec=None, touched=None):
    """1) швидко: усі пропозиції з даних Tronscan (submit/confirm/revoke), виконання - висновок з підтверджень;
    2) у межах бюджету: верифікація з ланцюга (прапор executed, data), спершу відкриті, потім неоднозначні
       (кілька submit в одному блоці), потім найновіші. Після верифікації chain_verified=1."""
    t0 = time.time()
    count = read_count()
    required = read_required()
    meta_set(conn, "tron_tx_count", count)
    meta_set(conn, "tron_required", required)
    subs = [dict(r) for r in conn.execute(
        "SELECT * FROM tron_calls WHERE method='submit' ORDER BY ts_ms, block, tx_hash")]
    n = len(subs)
    if n == 0:
        return
    k, share, cache = _calibrate(conn, subs, count, n)
    off = max(0, count - n - k)
    log.info("TRON: count=%s, submit=%s, зсув k=%s, збіг data=%.0f%%, перший id=%s", count, n, k, share * 100, off)
    if share < 0.8:
        log.error("TRON: розбіжність зіставлення submit->id (збіг %.0f%%), збірку пропущено", share * 100)
        return
    # --- фаза A: події і пропозиції зі submit-даних ---
    for r in conn.execute("SELECT * FROM tron_calls WHERE method!='submit' AND tx_id>=? AND tx_id<?",
                          (off, count)).fetchall():
        ensure_proposal(conn, CHAIN, r["tx_id"])
        add_event(conn, CHAIN, r["tx_id"], r["signer"], r["method"], iso(r["ts_ms"] // 1000), r["tx_hash"])
    have = {r["tx_id"]: r["chain_verified"] for r in conn.execute(
        "SELECT tx_id, chain_verified FROM proposals WHERE chain='tron' AND destination IS NOT NULL")}
    has_submit = {r["tx_id"] for r in conn.execute(
        "SELECT tx_id FROM confirmations WHERE chain='tron' AND kind='submit'")}
    for rk, s in enumerate(subs):
        tid = off + rk
        if tid >= count:
            break
        ensure_proposal(conn, CHAIN, tid)
        if tid not in have:
            _apply_submit_data(conn, tid, s, required)
        if tid not in has_submit and have.get(tid, 0) == 0:
            _set_submit(conn, tid, s)
    conn.commit()
    # прапор executed для ще не верифікованих - висновок із підтверджень
    evs = events_grouped(conn, CHAIN)
    for r in conn.execute("SELECT tx_id, chain_verified FROM proposals WHERE chain='tron'").fetchall():
        if r["chain_verified"]:
            continue
        ex = infer_exec(evs.get(r["tx_id"], []), required)
        update_proposal(conn, CHAIN, r["tx_id"], executed=1 if ex else 0, required=required)
    conn.commit()
    derive_all(conn, CHAIN, stale_days=stale_days)
    conn.commit()
    # --- фаза B: верифікація з ланцюга ---
    block_cnt: dict = {}
    for s in subs:
        block_cnt[s["block"]] = block_cnt.get(s["block"], 0) + 1
    refresh_ids = set(touched or ())
    full = (time.time() - float(meta_get(conn, "tron_full_refresh_ts", 0) or 0)) > 86400
    rows = conn.execute("SELECT tx_id, status, executed, chain_verified FROM proposals "
                        "WHERE chain='tron' AND tx_id>=? AND tx_id<?", (off, count)).fetchall()
    prio = []
    for r in rows:
        tid = r["tx_id"]
        opened = not r["executed"]
        if opened and (r["status"] == "pending" or full or tid in refresh_ids):
            prio.append((0, -tid, tid))                 # відкриті - щоразу
        elif not r["chain_verified"]:
            amb = block_cnt.get(subs[tid - off]["block"], 1) > 1
            prio.append((1 if amb else 2, -tid, tid))   # неоднозначні, потім найновіші
    prio.sort()
    log.info("TRON: черга верифікації з ланцюга: %s id (відкритих/оновлюваних %s)", len(prio),
             sum(1 for p in prio if p[0] == 0))
    override, done = {}, 0
    for pr, _, tid in prio:
        if budget_sec and pr > 0 and time.time() - t0 > budget_sec:
            log.warning("TRON: бюджет часу вичерпано, лишилось верифікувати %s id", len(prio) - done)
            break
        d = cache.get(tid) or read_transaction(tid)
        _apply_onchain(conn, tid, d, required)
        rk = tid - off
        s = subs[rk]
        if s["data"] != d["data"] or s["destination"] != tron_from_hex20(d["destination_hex"]):
            found = None
            for j in sorted(range(max(0, rk - 8), min(n, rk + 9)), key=lambda x: abs(x - rk)):
                if subs[j]["data"] == d["data"] and subs[j]["destination"] == tron_from_hex20(d["destination_hex"]):
                    found = subs[j]
                    break
            if found is not None:
                _set_submit(conn, tid, found)
                log.info("TRON: id %s - submit перепризначено після звірки з ланцюгом", tid)
        if not d["executed"]:
            override[tid] = read_conf_count(tid)
        done += 1
        if done % 10 == 0:
            derive_all(conn, CHAIN, stale_days=stale_days, conf_override=override)
            conn.commit()
            if done % 100 == 0:
                log.info("TRON: верифіковано %s/%s", done, len(prio))
    if full:
        meta_set(conn, "tron_full_refresh_ts", time.time())
    derive_all(conn, CHAIN, stale_days=stale_days, conf_override=override)
    conn.commit()


def sync(conn, since_ts=None, stale_days=7, budget_sec=None):
    since_ms = since_ts * 1000 if since_ts else None
    touched = scan_calls(conn, since_ms)
    assemble(conn, since_ms, stale_days, budget_sec, touched)
