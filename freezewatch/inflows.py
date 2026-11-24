"""Funds sent to already-frozen addresses: token transfers whose recipient was blacklisted at transfer time.

Definition: a Transfer(sender -> frozen address) with block time after the freeze was executed and before the unfreeze
(if any). These funds are locked immediately and are typically burned at the next destroy.

Collection (budget-bounded, resumable, key rotation):
- Ethereum USDT / USDC, Celo (Etherscan V2 chainid 42220) and Avalanche (Routescan, Etherscan-compatible): one logs
  query per frozen address filtered by topic0=Transfer and topic2=address (topic0_2_opr=and), from the stored cursor.
- TRON: TronGrid /v1/accounts/{addr}/transactions/trc20?only_to=true&contract_address=USDT&min_timestamp=...
Not covered yet: Solana (token-account level), NEAR, Aptos, TON, Kaia, Kava and the USDT0 Safe chains."""
import logging
import time

import requests

from . import analytics, eth
from .constants import ETH_USDT, TRON_USDT, USDC_ETH, USDT_DECIMALS
from .registry import MSW_CHAINS
from .util import HttpError, http_json, iso, key_pool, now_ts, parse_iso, tron_to_hex20

log = logging.getLogger("freezewatch.inflows")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TRONGRID = "https://api.trongrid.io"
MIN_INFLOW_USD = 0.01
MAX_SANE_AMOUNT = 1e12  # above any stablecoin supply: rejects max-uint approvals / malformed rows
# chain -> (token label, token contract, how to query)
EVM_TOKENS = {
    "eth": ("USDT", ETH_USDT, ("v2", 1)),
    "usdc_eth": ("USDC", USDC_ETH, ("v2", 1)),
    "celo": ("USDT", MSW_CHAINS["celo"]["token"], ("v2", 42220)),
    "avax": ("USDT", MSW_CHAINS["avax"]["token"], ("compat", MSW_CHAINS["avax"]["logs"].get("url"))),
}
COVERED = tuple(EVM_TOKENS) + ("tron",)


def latest_block(chain):
    url = MSW_CHAINS[chain]["rpc"] if chain in MSW_CHAINS else None
    return int(eth.rpc_batch([("eth_blockNumber", [])], url=url)[0], 16)


def pad32(addr):
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


# ---------------- pure helpers ----------------
def blocked_intervals(events):
    """events: analytics-style (ts, chain, addr, action, amount) -> {(chain, addr): [(start, end|None)]}. An address
    stays blocked after a destroy; only an unfreeze ends the interval."""
    out, open_at = {}, {}
    for ts, chain, addr, action, _amt in sorted(events, key=lambda e: e[0]):
        k = (chain, addr)
        if action == "freeze" and k not in open_at:
            open_at[k] = ts
        elif action == "unfreeze" and k in open_at:
            out.setdefault(k, []).append((open_at.pop(k), ts))
    for k, s in open_at.items():
        out.setdefault(k, []).append((s, None))
    return out


def in_blocked(ts, intervals):
    return any(s < ts and (e is None or ts < e) for s, e in intervals)


def parse_evm_log(lg):
    """Etherscan-style Transfer log -> (sender, amount, ts, tx_hash, log_index) or None."""
    t = lg.get("topics") or []
    if len(t) < 3 or t[0].lower() != TRANSFER_TOPIC:
        return None
    amt = int((lg.get("data") or "0x0"), 16) / 10 ** USDT_DECIMALS
    if amt > MAX_SANE_AMOUNT:
        return None
    return ("0x" + t[1][-40:], amt, eth._h(lg["timeStamp"]), lg["transactionHash"], eth._h(lg.get("logIndex", "0x0")))


def parse_tron_transfer(row):
    """TronGrid trc20 row -> (sender, amount, ts, tx_hash, 0) or None. The endpoint also returns Approval events
    (often with max-uint values): only Transfer rows count."""
    if row.get("type", "Transfer") != "Transfer":
        return None
    try:
        amt = int(row["value"]) / 10 ** USDT_DECIMALS
        if amt > MAX_SANE_AMOUNT:
            return None
        return (row["from"], amt, int(row["block_timestamp"]) // 1000, row["transaction_id"], 0)
    except (KeyError, ValueError):
        return None


def save(conn, chain, token, frozen, rows, intervals):
    """Insert qualifying transfers; returns the number of NEW rows."""
    n = 0
    for sender, amt, ts, tx, li in rows:
        if amt < MIN_INFLOW_USD or sender.lower() == frozen.lower() or not in_blocked(ts, intervals):
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO inflows_after_freeze(chain,token,frozen_address,sender,amount,ts,tx_hash,log_index,"
            "first_seen_at) VALUES(?,?,?,?,?,?,?,?,?)", (chain, token, frozen, sender, amt, iso(ts), tx, li, iso(now_ts())))
        n += cur.rowcount
    return n


# ---------------- fetchers ----------------
def fetch_evm(chain, addr, from_block):
    """All Transfer logs to `addr` since from_block (Etherscan V2 / Routescan)."""
    token, contract, how = EVM_TOKENS[chain][0], EVM_TOKENS[chain][1], EVM_TOKENS[chain][2]
    rows, page = [], 1
    while page <= 10:
        params = {"module": "logs", "action": "getLogs", "address": contract, "topic0": TRANSFER_TOPIC,
                  "topic2": pad32(addr), "topic0_2_opr": "and", "fromBlock": from_block, "toBlock": "latest",
                  "page": page, "offset": 1000}
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
    return [p for p in (parse_evm_log(x) for x in rows) if p]


def _grid_get(url, params):
    """TronGrid GET (key rotation, 429 cooldown, bounded) - kept as the single entry point for tests and outflows."""
    from . import trongrid
    return trongrid.request("GET", url.replace(TRONGRID, ""), params=params)


def fetch_tron(addr, min_ts_ms, stop=None):
    """Transfers INTO a TRON address since min_ts_ms: TronGrid, or Tronscan when TronGrid is saturated."""
    from . import trongrid
    rows, _src = trongrid.transfers(addr, "in", min_ts_ms, stop=stop)
    return [p for p in (parse_tron_transfer(x) for x in rows) if p]


# ---------------- driver ----------------
def candidates(conn):
    """[(chain, address, intervals, start_ts)] for covered chains, newest freezes first."""
    events, _n, _p = analytics.load_events(conn)
    iv = blocked_intervals([e for e in events if e[1] in COVERED])
    out = [(c, a, ivs, ivs[0][0]) for (c, a), ivs in iv.items()]
    out.sort(key=lambda x: -x[3])
    return out


def cleanup_bad_rows(conn):
    """One-time fix: early TRON scans stored Approval events too. Drop TRON rows + cursors once so they are rescanned
    with the Transfer-only parser; drop any absurd amount on every run."""
    from .db import meta_get, meta_set
    if meta_get(conn, "inflows_tron_transfer_only") != "1":
        n = conn.execute("DELETE FROM inflows_after_freeze WHERE chain='tron'").rowcount
        conn.execute("DELETE FROM inflow_cursor WHERE chain='tron'")
        meta_set(conn, "inflows_tron_transfer_only", "1")
        log.info("inflows: dropped %s TRON rows for a Transfer-only rescan", n)
    conn.execute("DELETE FROM inflows_after_freeze WHERE amount > ?", (MAX_SANE_AMOUNT,))
    conn.commit()


def refresh(conn, budget_sec=120):
    """Check frozen addresses for inbound transfers: never-checked (newest freeze first), then those checked before
    today. Cursors make every check incremental. Returns the number of addresses checked."""
    t0 = time.time()
    cleanup_bad_rows(conn)
    purge_sinks(conn)
    today = iso(now_ts())[:10]
    cur = {(r["chain"], r["address"]): (r["cursor"], r["checked_at"]) for r in conn.execute("SELECT * FROM inflow_cursor")}
    todo = [c for c in candidates(conn) if cur.get((c[0], c[1]), (None, None))[1] != today]
    prio = {c: i for i, c in enumerate(COVERED)}  # cheap EVM chains first, TRON (rate-limited) last
    bal = {(r["chain"], r["address"]): r["balance"] or 0 for r in conn.execute("SELECT chain, address, balance FROM balances")}
    # never-checked first, then chain priority, then RECENT freezes first (by day) and the biggest balance within a day
    todo.sort(key=lambda c: (cur.get((c[0], c[1])) is not None, cur.get((c[0], c[1]), (0, ""))[1] or "", prio[c[0]], -(c[3] // 86400),
                             -bal.get((c[0], c[1]), 0)))
    latest = {}
    n = 0

    def done(chain, addr, ivs, token, rows, new_cursor):
        save(conn, chain, token, addr, rows, ivs)
        conn.execute("INSERT INTO inflow_cursor(chain,address,cursor,checked_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(chain,address) DO UPDATE SET cursor=excluded.cursor, checked_at=excluded.checked_at",
                     (chain, addr, str(new_cursor), today))

    evm = [c for c in todo if c[0] != "tron"]
    for chain, addr, ivs, start in evm:
        if time.time() - t0 > budget_sec * (0.5 if any(c[0] == "tron" for c in todo) else 1):
            break  # TRON (the slow part) always gets the second half of the budget, so its scan keeps progressing
        cursor = cur.get((chain, addr), (None, None))[0]
        try:
            if chain not in latest:
                latest[chain] = latest_block(chain)
            rows = fetch_evm(chain, addr, int(cursor) + 1 if cursor else 0)
        except Exception as e:  # noqa: BLE001
            log.warning("inflows %s %s: %s", chain, str(addr)[:12], str(e)[:80])
            if "ETHERSCAN" in str(e):
                break
            continue
        done(chain, addr, ivs, EVM_TOKENS[chain][0], rows, latest[chain])
        n += 1
        if n % 20 == 0:
            conn.commit()
    # TRON: TronGrid latency (~2 s) dominates, so addresses are fetched concurrently (the shared limiter keeps the rate)
    from concurrent.futures import ThreadPoolExecutor
    trn = [c for c in todo if c[0] == "tron"]

    def one(item):
        chain, addr, ivs, start = item
        cursor = cur.get((chain, addr), (None, None))[0]
        from_ms = max(start * 1000, int(cursor)) if cursor else start * 1000
        now_ms = now_ts() * 1000
        try:
            return item, fetch_tron(addr, from_ms), now_ms - 60_000
        except Exception as e:  # noqa: BLE001
            log.warning("inflows tron %s: %s", str(addr)[:10], str(e)[:80])
            return item, None, None
    with ThreadPoolExecutor(max_workers=max(6, 4 * len(key_pool("TRONGRID_API_KEY")))) as ex:
        for i in range(0, len(trn), 24):
            if time.time() - t0 > budget_sec:
                break
            for item, rows, new_cursor in ex.map(one, trn[i:i + 24]):
                if rows is None:
                    continue
                done("tron", item[1], item[2], "USDT", rows, new_cursor)
                n += 1
            conn.commit()
    conn.commit()
    log.info("inflows: %s addresses checked (%s remaining)", n, max(0, len(todo) - n))
    return n


def purge_sinks(conn):
    """Remove inflow rows / cursors of special sinks (zero address, token contracts); keep a running total of what was
    excluded so the dashboard can mention it."""
    import json
    tot_n, tot_usd = 0, 0.0
    for r in conn.execute("SELECT chain, frozen_address, COUNT(*) n, SUM(amount) usd FROM inflows_after_freeze "
                          "GROUP BY chain, frozen_address").fetchall():
        if r["frozen_address"] in analytics.special_sinks(r["chain"]):
            tot_n += r["n"]
            tot_usd += r["usd"] or 0.0
            conn.execute("DELETE FROM inflows_after_freeze WHERE chain=? AND frozen_address=?", (r["chain"], r["frozen_address"]))
            conn.execute("DELETE FROM inflow_cursor WHERE chain=? AND address=?", (r["chain"], r["frozen_address"]))
    for r in conn.execute("SELECT chain, address FROM inflow_cursor").fetchall():
        if r["address"] in analytics.special_sinks(r["chain"]):
            conn.execute("DELETE FROM inflow_cursor WHERE chain=? AND address=?", (r["chain"], r["address"]))
    if tot_n:
        row = conn.execute("SELECT value FROM meta WHERE key='inflow_excluded'").fetchone()
        old = json.loads(row[0]) if row else {"n": 0, "usd": 0.0}
        new = {"n": old["n"] + tot_n, "usd": round(old["usd"] + tot_usd, 2)}
        conn.execute("INSERT INTO meta(key,value) VALUES('inflow_excluded',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(new),))
    conn.commit()
    return tot_n, tot_usd


def coverage(conn):
    total = len(candidates(conn))
    done = conn.execute("SELECT COUNT(*) FROM inflow_cursor").fetchone()[0]
    return done, total
