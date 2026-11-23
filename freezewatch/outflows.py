"""Where the funds went before a freeze: outgoing token transfers from a frozen address in the 72 hours before the freeze
was executed, with the transfers between proposal submission and execution ("in-window") marked separately.

EVM (Ethereum USDT / USDC, Celo, Avalanche): Transfer logs with topic1 = the address (Etherscan V2 / Routescan), block range
from getblocknobytime. TRON: TronGrid trc20 only_from=true with min/max timestamp (key rotation, Transfer rows only).
Sinks (zero address, token contracts) and self-transfers are ignored. Budgeted and resumable: newest freezes first, every
(chain, address, freeze time) is done once."""
import json
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


MAX_QUERIES = 80       # log queries per (address, freeze) before the totals are marked truncated
PAGE = 1000            # Etherscan / Routescan return at most this many logs per query: a full page means "split the range"


def _evm_page(chain, addr, frm, to):
    contract, how = EVM_TOKENS[chain][1], EVM_TOKENS[chain][2]
    params = {"module": "logs", "action": "getLogs", "address": contract, "topic0": TRANSFER_TOPIC, "topic1": pad32(addr),
              "topic0_1_opr": "and", "fromBlock": frm, "toBlock": to, "page": 1, "offset": PAGE}
    if how[0] == "v2":
        o = eth._es(params, how[1])
        return o["result"] if o.get("status") == "1" else []
    o = http_json("GET", how[1], params=params, interval=0.3, retries=4)
    return o["result"] if isinstance(o.get("result"), list) else []


def fetch_evm_out(chain, addr, frm, to, page=_evm_page, max_queries=MAX_QUERIES, stop=None, ranges=None):
    """Outgoing transfers in the block range; a full result page splits the range in halves (no deep pagination, which the
    explorers cap at ~2,000). Explicit stack, so the scan can stop at any query (`stop()` true, query budget, HTTP error) and
    resume later from the returned `pending` ranges. -> (rows, truncated, pending, queries_used). truncated = a single block still
    overflows a page."""
    stack = [tuple(r) for r in ranges] if ranges else [(int(frm), int(to))]
    raw, trunc, left = [], False, max_queries
    while stack and left > 0 and not (stop and stop()):
        a, b = stack.pop()
        left -= 1
        try:
            res = page(chain, addr, a, b)
        except Exception as e:  # noqa: BLE001
            stack.append((a, b))
            log.info("outflows %s %s: query stopped (%s)", chain, str(addr)[:10], str(e)[:60])
            break
        if len(res) >= PAGE:
            if b > a:
                m = (a + b) // 2
                stack.append((m + 1, b))
                stack.append((a, m))
                continue
            trunc = True
        raw.extend(res)
    seen, rows = set(), []
    for x in raw:
        k = (x.get("transactionHash"), x.get("logIndex"))
        if k in seen:
            continue
        seen.add(k)
        pr = parse_out_log(x)
        if pr:
            rows.append(pr)
    return rows, trunc, [list(r) for r in stack], max_queries - left


TRON_MAX_PAGES = 40    # x 200 transfers, across all runs of one freeze


def fetch_tron_out(addr, min_ms, max_ms, get=None, max_pages=TRON_MAX_PAGES, fp=None, stop=None, pages_done=0):
    """TronGrid pages with a resumable fingerprint. -> (rows, truncated, next_fp, pages_done). next_fp is set when the scan
    stopped early (`stop()` / HTTP error); truncated when TronGrid still offered another page after `max_pages` pages."""
    get = get or _grid_get
    rows, more, i = [], False, pages_done
    while i < max_pages:
        if stop and stop():
            return [p for p in (parse_tron_out(x) for x in rows) if p], False, fp, i
        params = {"only_from": "true", "contract_address": TRON_USDT, "min_timestamp": int(min_ms), "max_timestamp": int(max_ms),
                  "limit": 200, "order_by": "block_timestamp,asc"}
        if fp:
            params["fingerprint"] = fp
        try:
            o = get(f"{TRONGRID}/v1/accounts/{addr}/transactions/trc20", params)
        except Exception as e:  # noqa: BLE001
            log.info("outflows tron %s: page stopped (%s)", str(addr)[:10], str(e)[:60])
            return [p for p in (parse_tron_out(x) for x in rows) if p], False, fp, i
        rows += o.get("data") or []
        i += 1
        fp = (o.get("meta") or {}).get("fingerprint")
        if not fp:
            return [p for p in (parse_tron_out(x) for x in rows) if p], False, None, i
        more = i >= max_pages
    return [p for p in (parse_tron_out(x) for x in rows) if p], more, None, i


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


def store_rows(conn, chain, addr, freeze_ts, rows):
    n = 0
    for rcp, amt, ts, tx, li, inw in rows:
        n += conn.execute("INSERT OR IGNORE INTO outflows(chain,frozen_address,recipient,amount,ts,tx_hash,log_index,in_window,"
                          "freeze_ts) VALUES(?,?,?,?,?,?,?,?,?)", (chain, addr, rcp, amt, iso(ts), tx, li, inw, iso(freeze_ts))).rowcount
    return n


def finish(conn, chain, addr, freeze_ts, n_transfers, truncated):
    """Mark a freeze done; totals come from the stored rows (a scan may span several runs)."""
    r = conn.execute("SELECT COALESCE(SUM(amount),0), COALESCE(SUM(CASE WHEN in_window THEN amount ELSE 0 END),0) FROM outflows "
                     "WHERE chain=? AND frozen_address=? AND freeze_ts=?", (chain, addr, iso(freeze_ts))).fetchone()
    conn.execute("INSERT OR REPLACE INTO outflow_done(chain,frozen_address,freeze_ts,checked_at,total_out,window_out,n_transfers,"
                 "truncated) VALUES(?,?,?,?,?,?,?,?)", (chain, addr, iso(freeze_ts), iso(now_ts()), r[0], r[1], n_transfers,
                                                        int(bool(truncated))))
    conn.execute("DELETE FROM outflow_cursor WHERE chain=? AND frozen_address=? AND freeze_ts=?", (chain, addr, iso(freeze_ts)))


def save(conn, chain, addr, freeze_ts, rows, n_transfers=None, truncated=False):
    """Store the rows of a complete scan and mark it done."""
    n = store_rows(conn, chain, addr, freeze_ts, rows)
    finish(conn, chain, addr, freeze_ts, n_transfers if n_transfers is not None else len(rows), truncated)
    return n


def requeue_suspects(conn):
    """Freezes scanned before truncation was tracked (truncated IS NULL) whose stored transfers look capped (>= 900) are
    scanned again with the range-splitting fetch; the others are marked complete."""
    n = 0
    for r in conn.execute("SELECT chain, frozen_address, freeze_ts FROM outflow_done WHERE truncated IS NULL").fetchall():
        c = conn.execute("SELECT COUNT(*) FROM outflows WHERE chain=? AND frozen_address=? AND freeze_ts=?",
                         (r[0], r[1], r[2])).fetchone()[0]
        if c >= 900:
            conn.execute("DELETE FROM outflow_done WHERE chain=? AND frozen_address=? AND freeze_ts=?", (r[0], r[1], r[2]))
            n += 1
        else:
            conn.execute("UPDATE outflow_done SET truncated=0 WHERE chain=? AND frozen_address=? AND freeze_ts=?", (r[0], r[1], r[2]))
    if n:
        log.info("outflows: %s possibly capped freezes queued for a full re-scan", n)
    conn.commit()
    return n


ADDRESS_SEC = 60       # one address may use at most this long per run; huge wallets continue on the next runs
MAX_TOTAL_QUERIES = 400   # explorer queries per freeze over all runs; beyond that the totals are marked truncated


def _load_cursors(conn):
    return {(r[0], r[1], r[2]): json.loads(r[3]) for r in conn.execute("SELECT chain, frozen_address, freeze_ts, state FROM outflow_cursor")}


def refresh(conn, budget_sec=120):
    """Collect outflows for not-yet-done freezes: unfinished cursors first, then newest freezes. Every address has its own
    time cap and a resumable cursor (block ranges / TronGrid fingerprint); the step stops when the budget or the run
    deadline is over, also inside the paging / bisection loops. Returns the number of freezes finished."""
    from . import deadline
    t0 = time.time()
    requeue_suspects(conn)
    done = {(r[0], r[1], r[2]) for r in conn.execute("SELECT chain, frozen_address, freeze_ts FROM outflow_done")}
    cursors = _load_cursors(conn)
    cands = [c for c in candidates(conn) if (c[0], c[1], iso(c[2])) not in done]
    todo = sorted(cands, key=lambda c: (((c[0], c[1], iso(c[2])) not in cursors), 0))   # stable: cursors first, then newest first
    sinks = {}
    n = steps = 0
    for chain, addr, fts, sub in todo:
        if time.time() - t0 > budget_sec or deadline.http_stop():
            break
        key = (chain, addr, iso(fts))
        st = cursors.get(key) or {}
        sk = sinks.setdefault(chain, analytics.special_sinks(chain))
        end = min(time.time() + ADDRESS_SEC, t0 + budget_sec)
        stop = lambda end=end: time.time() > end or deadline.http_stop()  # noqa: E731
        try:
            if chain == "tron":
                rows, trunc, fp, pages = fetch_tron_out(addr, (fts - WINDOW_SEC) * 1000, (fts + 1) * 1000, fp=st.get("fp"), stop=stop,
                                                        pages_done=st.get("pages", 0))
                pending = fp is not None
                st = {"fp": fp, "pages": pages, "n": st.get("n", 0) + len(rows), "q": pages}
            else:
                ranges = st.get("ranges")
                if ranges is None:
                    ranges = [[block_at(chain, fts - WINDOW_SEC), block_at(chain, fts + 30)]]
                q_left = max(1, MAX_TOTAL_QUERIES - st.get("q", 0))
                rows, trunc, pend, used = fetch_evm_out(chain, addr, 0, 0, stop=stop, ranges=ranges, max_queries=min(MAX_QUERIES, q_left))
                st = {"ranges": pend, "q": st.get("q", 0) + used, "n": st.get("n", 0) + len(rows)}
                pending = bool(pend) and not trunc
                if pending and st["q"] >= MAX_TOTAL_QUERIES:   # the explorer budget for this freeze is spent: totals are a lower bound
                    trunc, pending = True, False
        except Exception as e:  # noqa: BLE001
            log.warning("outflows %s %s: %s", chain, str(addr)[:12], str(e)[:80])
            if "ETHERSCAN" in str(e).upper() or deadline.http_stop():
                break
            continue
        store_rows(conn, chain, addr, fts, window_rows(rows, addr, fts, sub, sk))
        steps += 1
        if pending:
            conn.execute("INSERT OR REPLACE INTO outflow_cursor(chain,frozen_address,freeze_ts,state,updated_at) VALUES(?,?,?,?,?)",
                         (chain, addr, iso(fts), json.dumps(st), iso(now_ts())))
            conn.commit()
            continue
        finish(conn, chain, addr, fts, st.get("n", len(rows)), trunc)
        n += 1
        if n % 20 == 0:
            conn.commit()
    conn.commit()
    log.info("outflows: %s freezes finished, %s scan steps (%s remaining, %s with a saved cursor)", n, steps,
             max(0, len(todo) - n), len(_load_cursors(conn)))
    return n


def coverage(conn):
    total = len(candidates(conn))
    done = conn.execute("SELECT COUNT(*) FROM outflow_done").fetchone()[0]
    return min(done, total), total


# ---------------- aggregates for the dashboard ----------------
def frozen_intervals(conn):
    """{(chain, normalised address): [(start, end|None)]} blocked intervals from all executed freeze / unfreeze actions."""
    from .inflows import blocked_intervals
    events, _n, _p = analytics.load_events(conn)
    return blocked_intervals(events)


def _label_of(labels, chain, addr):
    from .labels import NETWORK_OF, key_of
    net = NETWORK_OF.get(chain)
    return labels.get((net, key_of(net, addr))) if (net and labels) else None


def is_service_wallet(labels, chain, addr):
    """The frozen address itself is labelled as an exchange / bridge / protocol wallet: its transfers are routine operations,
    not an escape attempt, so they are reported apart."""
    from .labels import is_connector
    return is_connector(_label_of(labels, chain, addr))


def is_internal(chain, frozen, recipient, ts, intervals, cluster_index=None, labels=None):
    """True when the transfer only moved funds between addresses that were (or became) frozen too, or inside one cluster:
    the recipient is blocked at or after the transfer time on that chain, or shares a batch/counterparty cluster with the
    frozen address. Such transfers are not an escape."""
    ivs = intervals.get((chain, analytics.norm(chain, recipient)), ())
    if any(e is None or e > ts for _s, e in ivs):
        return True
    if labels:  # a hop between two wallets of the same labelled entity (e.g. Binance hot -> Binance hot)
        a, b = _label_of(labels, chain, frozen), _label_of(labels, chain, recipient)
        if a and b and (a["entity"] or "") and a["entity"] == b["entity"]:
            return True
    if cluster_index:
        a = set(cluster_index.get((chain, analytics.norm(chain, frozen)), ()))
        if a and a & set(cluster_index.get((chain, analytics.norm(chain, recipient)), ())):
            return True
    return False


def classify(conn, since_iso=None, cluster_index=None, labels=None):
    """-> [dict(chain, frozen, freeze_ts, recipient, amount, in_window, internal)] for every stored outflow."""
    ivs = frozen_intervals(conn)
    q = "SELECT chain, frozen_address, freeze_ts, recipient, amount, ts, in_window FROM outflows"
    args = ()
    if since_iso:
        q += " WHERE freeze_ts>=?"
        args = (since_iso,)
    out = []
    for r in conn.execute(q, args):
        out.append({"chain": r["chain"], "frozen": r["frozen_address"], "freeze_ts": r["freeze_ts"], "recipient": r["recipient"],
                    "amount": r["amount"], "in_window": r["in_window"],
                    "internal": is_internal(r["chain"], r["frozen_address"], r["recipient"], parse_iso(r["ts"]), ivs, cluster_index, labels)})
    return out


def freeze_totals(rows):
    """{(chain, frozen, freeze_ts): {escaped, window, internal}} - escaped excludes internal moves."""
    tot = {}
    for r in rows:
        d = tot.setdefault((r["chain"], r["frozen"], r["freeze_ts"]), {"escaped": 0.0, "window": 0.0, "internal": 0.0})
        if r["internal"]:
            d["internal"] += r["amount"]
        else:
            d["escaped"] += r["amount"]
            if r["in_window"]:
                d["window"] += r["amount"]
    return tot


def stats(conn, labels, now=None, frozen_usd=None, cluster_index=None):
    """12-month KPIs, top recipient entities and the front-run list - all on ESCAPED funds (internal moves between frozen
    addresses are reported separately). Freezes whose transfer list was truncated never count as front-run."""
    from .labels import NETWORK_OF, key_of
    now = now or now_ts()
    since = iso(now - 365 * 86400)
    done = {(r["chain"], r["frozen_address"], r["freeze_ts"]): r for r in conn.execute(
        "SELECT * FROM outflow_done WHERE freeze_ts>=?", (since,))}
    rows = classify(conn, since, cluster_index, labels)
    tot_all = freeze_totals(rows)
    service = {k: d for k, d in tot_all.items() if is_service_wallet(labels, k[0], k[1])}
    tot = {k: d for k, d in tot_all.items() if k not in service}
    rows = [r for r in rows if (r["chain"], r["frozen"], r["freeze_ts"]) not in service]
    service_usd = sum(d["escaped"] + d["internal"] for d in service.values())
    escaped = sum(d["escaped"] for d in tot.values())
    internal = sum(d["internal"] for d in tot.values())
    win = sum(d["window"] for d in tot.values())
    with_out = sum(1 for d in tot.values() if d["escaped"] > 0)
    trunc = {k for k, r in done.items() if r["truncated"]}
    frozen_total = sum((frozen_usd or {}).get(k) or 0 for k in done if k not in service)
    ents = {}
    for r in rows:
        if r["internal"]:
            continue
        net = NETWORK_OF.get(r["chain"])
        lb = labels.get((net, key_of(net, r["recipient"]))) if net else None
        name = (lb["entity"] or lb["label"]) if lb else None
        if name:
            e = ents.setdefault(name, {"entity": name, "category": lb["category"], "usd": 0.0, "n": 0, "url": lb["source_url"]})
            e["usd"] += r["amount"]
            e["n"] += 1
    front = []
    for k, d in tot.items():
        w = d["window"]
        if w <= 0 or k in trunc:
            continue
        remaining = (frozen_usd or {}).get(k)
        if remaining is None:  # amount frozen unknown: no share can be computed
            continue
        share = w / (w + remaining) if (w + remaining) else None
        if share is not None and share >= 0.5:
            c, a, f = k
            net = NETWORK_OF.get(c)
            lb = labels.get((net, key_of(net, a))) if net else None
            front.append({"chain": c, "address": a, "freeze_ts": f, "window_out": round(w, 2), "frozen_usd": round(remaining, 2),
                          "share": round(share, 3), "label": (lb["entity"] or lb["label"]) if lb else None})
    front.sort(key=lambda x: -x["window_out"])
    top = sorted(ents.values(), key=lambda e: -e["usd"])[:15]
    return {"checked": len(done), "with_out": with_out, "truncated": len(trunc), "moved_12m": round(escaped, 2),
            "internal_12m": round(internal, 2), "service_12m": round(service_usd, 2), "service_n": len(service), "window_12m": round(win, 2), "frozen_12m": round(frozen_total, 2),
            "top_entities": top, "frontrun": front[:25], "n_frontrun": len(front)}


def per_address(conn, labels, cluster_index=None):
    """{(chain, address): [{freeze_ts, escaped, window, internal, truncated, recipients:[{to, usd, w, internal, label}]}]}."""
    from .labels import NETWORK_OF, key_of
    trunc = {(r[0], r[1], r[2]) for r in conn.execute("SELECT chain, frozen_address, freeze_ts FROM outflow_done WHERE truncated=1")}
    out = {}
    agg = {}
    for r in classify(conn, None, cluster_index, labels):
        k = (r["chain"], r["frozen"], r["freeze_ts"], r["recipient"], r["internal"])
        e = agg.setdefault(k, [0.0, 0.0])
        e[0] += r["amount"]
        e[1] += r["amount"] if r["in_window"] else 0.0
    for (chain, addr, fts, rcp, internal), (usd, w) in agg.items():
        ent = out.setdefault((chain, addr), {}).setdefault(fts, {"freeze_ts": fts, "escaped": 0.0, "window": 0.0, "internal": 0.0,
                                                                  "truncated": (chain, addr, fts) in trunc, "recipients": [],
                                                                  "service": is_service_wallet(labels, chain, addr)})
        if internal:
            ent["internal"] += usd
        else:
            ent["escaped"] += usd
            ent["window"] += w
        net = NETWORK_OF.get(chain)
        ent["recipients"].append({"to": rcp, "usd": usd, "w": w, "internal": internal,
                                  "label": labels.get((net, key_of(net, rcp))) if net else None})
    for key, d in out.items():
        for e in d.values():
            e["recipients"].sort(key=lambda x: (x["internal"], -x["usd"]))
    return {k: sorted(v.values(), key=lambda e: e["freeze_ts"]) for k, v in out.items()}
