"""Where the funds went before a freeze: outgoing token transfers from a frozen address in the 72 hours before the freeze
was executed, with the transfers between proposal submission and execution ("in-window") marked separately.

EVM (Ethereum USDT / USDC, Celo, Avalanche): Transfer logs with topic1 = the address (Etherscan V2 / Routescan), block range
from getblocknobytime. TRON: TronGrid trc20 only_from=true with min/max timestamp (key rotation, Transfer rows only).
Sinks (zero address, token contracts) and self-transfers are ignored. Budgeted and resumable: newest freezes first, every
(chain, address, freeze time) is done once."""
import logging
import time

from . import analytics, eth
from .inflows import (EVM_TOKENS, MAX_SANE_AMOUNT, TRANSFER_TOPIC, TRONGRID, _grid_get, latest_block, pad32)
from .constants import TRON_USDT, USDT_DECIMALS
from .util import http_json, iso, now_ts, parse_iso

log = logging.getLogger("freezewatch.outflows")
WINDOW_SEC = 72 * 3600
MIN_OUT_USD = 0.01
COVERED = tuple(EVM_TOKENS) + ("tron",)


# ---------------- pure helpers ----------------
def parse_out_log(lg):
    """Etherscan-style Transfer log -> (recipient, amount, ts, tx_hash, log_index) or None."""
    t = lg.get("topics") or []
    if len(t) < 3 or t[0].lower() != TRANSFER_TOPIC:
        return None
    amt = int((lg.get("data") or "0x0"), 16) / 10 ** USDT_DECIMALS
    if amt > MAX_SANE_AMOUNT:
        return None
    return ("0x" + t[2][-40:], amt, eth._h(lg["timeStamp"]), lg["transactionHash"], eth._h(lg.get("logIndex", "0x0")))


def parse_tron_out(row):
    """TronGrid trc20 row (only_from) -> (recipient, amount, ts, tx_hash, 0); Approval rows and absurd values are dropped."""
    if row.get("type", "Transfer") != "Transfer":
        return None
    try:
        amt = int(row["value"]) / 10 ** USDT_DECIMALS
        if amt > MAX_SANE_AMOUNT:
            return None
        return (row["to"], amt, int(row["block_timestamp"]) // 1000, row["transaction_id"], 0)
    except (KeyError, ValueError):
        return None


def window_rows(rows, frozen, freeze_ts, submitted_ts, sinks=()):
    """Keep transfers in [freeze-72h, freeze]; -> [(recipient, amount, ts, tx, li, in_window)].
    in_window = between proposal submission and execution (only when the submission time is known)."""
    out = []
    fz = (frozen or "").lower()
    for rcp, amt, ts, tx, li in rows:
        if rcp.lower() == fz or rcp in sinks or rcp.lower() in sinks or amt < MIN_OUT_USD:
            continue
        if not (freeze_ts - WINDOW_SEC <= ts <= freeze_ts):
            continue
        out.append((rcp, amt, ts, tx, li, int(submitted_ts is not None and submitted_ts <= ts <= freeze_ts)))
    return out


# ---------------- fetchers ----------------
_block_cache = {}


def block_at(chain, ts):
    """Block number at or before `ts` (Etherscan V2 / Routescan getblocknobytime), cached."""
    k = (chain, int(ts) // 600)
    if k in _block_cache:
        return _block_cache[k]
    how = EVM_TOKENS[chain][2]
    params = {"module": "block", "action": "getblocknobytime", "timestamp": int(ts), "closest": "before"}
    if how[0] == "v2":
        o = eth._es(params, how[1])
    else:
        o = http_json("GET", how[1], params=params, interval=0.3, retries=4)
    n = int(o["result"])
    _block_cache[k] = n
    return n


def fetch_evm_out(chain, addr, frm, to):
    contract, how = EVM_TOKENS[chain][1], EVM_TOKENS[chain][2]
    rows, page = [], 1
    while page <= 10:
        params = {"module": "logs", "action": "getLogs", "address": contract, "topic0": TRANSFER_TOPIC, "topic1": pad32(addr),
                  "topic0_1_opr": "and", "fromBlock": frm, "toBlock": to, "page": page, "offset": 1000}
        if how[0] == "v2":
            o = eth._es(params, how[1])
            res = o["result"] if o.get("status") == "1" else []
        else:
            o = http_json("GET", how[1], params=params, interval=0.3, retries=4)
            res = o["result"] if isinstance(o.get("result"), list) else []
        rows += res
        if len(res) < 1000:
            break
        page += 1
    return [p for p in (parse_out_log(x) for x in rows) if p]


def fetch_tron_out(addr, min_ms, max_ms):
    rows, fp = [], None
    for _ in range(10):
        params = {"only_from": "true", "contract_address": TRON_USDT, "min_timestamp": int(min_ms), "max_timestamp": int(max_ms),
                  "limit": 200, "order_by": "block_timestamp,asc"}
        if fp:
            params["fingerprint"] = fp
        o = _grid_get(f"{TRONGRID}/v1/accounts/{addr}/transactions/trc20", params)
        rows += o.get("data") or []
        fp = (o.get("meta") or {}).get("fingerprint")
        if not fp:
            break
    return [p for p in (parse_tron_out(x) for x in rows) if p]


# ---------------- driver ----------------
def candidates(conn):
    """[(chain, address, freeze_ts, submitted_ts|None)] of executed freezes on covered chains, newest first."""
    out = []
    for r in conn.execute("SELECT chain, target, executed_at, submitted_at, function FROM proposals "
                          "WHERE executed=1 AND executed_at IS NOT NULL AND target IS NOT NULL"):
        if r["chain"] not in COVERED:
            continue
        if r["function"] not in ("addBlackList", "addToBlockedList", "freeze_accounts", "freeze_wallet"):
            continue
        a = analytics.norm(r["chain"], r["target"])
        if a in analytics.special_sinks(r["chain"]):
            continue
        out.append((r["chain"], a, parse_iso(r["executed_at"]), parse_iso(r["submitted_at"]) if r["submitted_at"] else None))
    for r in conn.execute("SELECT source, subject, ts FROM freezes WHERE action='freeze' AND source='usdc_eth'"):
        out.append(("usdc_eth", r["subject"].lower(), parse_iso(r["ts"]), None))
    out.sort(key=lambda x: -x[2])
    return out


def save(conn, chain, addr, freeze_ts, rows):
    n = tot = win = 0
    for rcp, amt, ts, tx, li, inw in rows:
        n += conn.execute("INSERT OR IGNORE INTO outflows(chain,frozen_address,recipient,amount,ts,tx_hash,log_index,in_window,"
                          "freeze_ts) VALUES(?,?,?,?,?,?,?,?,?)", (chain, addr, rcp, amt, iso(ts), tx, li, inw, iso(freeze_ts))).rowcount
        tot += amt
        win += amt if inw else 0
    conn.execute("INSERT OR REPLACE INTO outflow_done(chain,frozen_address,freeze_ts,checked_at,total_out,window_out) "
                 "VALUES(?,?,?,?,?,?)", (chain, addr, iso(freeze_ts), iso(now_ts()), tot, win))
    return n


def refresh(conn, budget_sec=120):
    """Collect outflows for not-yet-done freezes, newest first. Returns the number processed."""
    t0 = time.time()
    done = {(r[0], r[1], r[2]) for r in conn.execute("SELECT chain, frozen_address, freeze_ts FROM outflow_done")}
    todo = [c for c in candidates(conn) if (c[0], c[1], iso(c[2])) not in done]
    sinks = {}
    n = 0
    for chain, addr, fts, sub in todo:
        if time.time() - t0 > budget_sec:
            break
        sk = sinks.setdefault(chain, analytics.special_sinks(chain))
        try:
            if chain == "tron":
                rows = fetch_tron_out(addr, (fts - WINDOW_SEC) * 1000, (fts + 1) * 1000)
            else:
                rows = fetch_evm_out(chain, addr, block_at(chain, fts - WINDOW_SEC), block_at(chain, fts + 30))
        except Exception as e:  # noqa: BLE001
            log.warning("outflows %s %s: %s", chain, str(addr)[:12], str(e)[:80])
            if "ETHERSCAN" in str(e).upper():
                break
            continue
        save(conn, chain, addr, fts, window_rows(rows, addr, fts, sub, sk))
        n += 1
        if n % 20 == 0:
            conn.commit()
    conn.commit()
    log.info("outflows: %s freezes checked (%s remaining)", n, max(0, len(todo) - n))
    return n


def coverage(conn):
    total = len(candidates(conn))
    done = conn.execute("SELECT COUNT(*) FROM outflow_done").fetchone()[0]
    return min(done, total), total


# ---------------- aggregates for the dashboard ----------------
def stats(conn, labels, now=None, frozen_usd=None):
    """KPIs over the last 12 months, top recipient entities, front-run list. `labels` = labels.load_map(conn);
    `frozen_usd` = {(chain, address, freeze_iso): usd} (amount frozen / burned)."""
    from .labels import NETWORK_OF, key_of
    now = now or now_ts()
    since = iso(now - 365 * 86400)
    done = {(r["chain"], r["frozen_address"], r["freeze_ts"]): r for r in conn.execute(
        "SELECT * FROM outflow_done WHERE freeze_ts>=?", (since,))}
    moved = sum((r["total_out"] or 0) for r in done.values())
    win = sum((r["window_out"] or 0) for r in done.values())
    with_out = sum(1 for r in done.values() if (r["total_out"] or 0) > 0)
    frozen_total = sum((frozen_usd or {}).get((c, a, f)) or 0 for (c, a, f) in done)
    ents, front = {}, []
    for r in conn.execute("SELECT chain, frozen_address, recipient, amount, in_window, freeze_ts FROM outflows WHERE freeze_ts>=?", (since,)):
        net = NETWORK_OF.get(r["chain"])
        lb = labels.get((net, key_of(net, r["recipient"]))) if net else None
        name = (lb["entity"] or lb["label"]) if lb else None
        if name:
            e = ents.setdefault(name, {"entity": name, "category": lb["category"], "usd": 0.0, "n": 0, "url": lb["source_url"]})
            e["usd"] += r["amount"]
            e["n"] += 1
    for (c, a, f), r in done.items():
        w = r["window_out"] or 0
        if w <= 0:
            continue
        remaining = (frozen_usd or {}).get((c, a, f))
        if remaining is None:  # amount frozen unknown: no share can be computed, never flag as front-run
            continue
        share = w / (w + remaining) if (w + remaining) else None
        if share is not None and share >= 0.5:
            front.append({"chain": c, "address": a, "freeze_ts": f, "window_out": round(w, 2), "frozen_usd": round(remaining, 2),
                          "share": round(share, 3)})
    front.sort(key=lambda x: -x["window_out"])
    top = sorted(ents.values(), key=lambda e: -e["usd"])[:15]
    return {"checked": len(done), "with_out": with_out, "moved_12m": round(moved, 2), "window_12m": round(win, 2),
            "frozen_12m": round(frozen_total, 2), "top_entities": top, "frontrun": front[:25], "n_frontrun": len(front)}


def per_address(conn, labels):
    """{(chain, address): [{freeze_ts, total, window, recipients:[{to, usd, window_usd, label?}]}]} for the address index."""
    from .labels import NETWORK_OF, key_of
    out = {}
    rows = conn.execute("SELECT chain, frozen_address, freeze_ts, recipient, SUM(amount) usd, SUM(CASE WHEN in_window THEN amount ELSE 0 END) w "
                        "FROM outflows GROUP BY chain, frozen_address, freeze_ts, recipient").fetchall()
    for r in rows:
        key = (r["chain"], r["frozen_address"])
        lst = out.setdefault(key, {})
        ent = lst.setdefault(r["freeze_ts"], {"freeze_ts": r["freeze_ts"], "total": 0.0, "window": 0.0, "recipients": []})
        ent["total"] += r["usd"]
        ent["window"] += r["w"]
        net = NETWORK_OF.get(r["chain"])
        lb = labels.get((net, key_of(net, r["recipient"]))) if net else None
        ent["recipients"].append({"to": r["recipient"], "usd": r["usd"], "w": r["w"], "label": lb})
    for key, d in out.items():
        for e in d.values():
            e["recipients"].sort(key=lambda x: -x["usd"])
    return {k: sorted(v.values(), key=lambda e: e["freeze_ts"]) for k, v in out.items()}
