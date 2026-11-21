"""Analytics: how much money was frozen / burned / unfrozen / reissued, and how much is still frozen.

Methodology (all approximations are labelled in the UI):
- Burned $: exact amount of the DestroyedBlackFunds / DestroyedBlockedFunds event of the executing transaction
  (enrich_destroy_amounts reads receipts once and stores it in proposals.amount).
- Still frozen $: frozen funds cannot move, so the current USDT balance of a currently blacklisted address is the
  frozen amount (table `balances`, refreshed incrementally and resumably within a time budget).
- Frozen $ of one freeze = burned amount if the address was later destroyed, else current balance if still frozen,
  else the balance last observed while frozen if it was unfrozen, else unknown (counted separately).
- Reissued (approx.): executed issue()/mint proposals matched 1:1 to an earlier destroy of the same chain with the
  same amount (+-1%) within 30 days."""
import logging
import time

from . import crosschain, eth
from .constants import (DESTROY_FUNCS, FREEZE_FUNCS, SEL_BALANCE_OF, SOLANA_SOURCES, UNFREEZE_FUNCS, USDC_ETH,
                        USDT_DECIMALS)
from .registry import MSW_CHAINS, SAFE_CHAINS
from .util import iso, now_ts, parse_iso

log = logging.getLogger("freezewatch.analytics")

# topic0 of DestroyedBlackFunds(address,uint256) (legacy USDT, ETH and TRON)
TOPIC_DESTROYED_LEGACY = "61e6e66b0d6339b2980aecc6ccc0039736791f0ccde9ed512e789a7fbdd698c6"
TOPIC_DESTROYED_NEW = "6a2859ae7902313752498feb80a014e6e7275fe964c79aa965db815db1c7f1e9"
ISSUE_FUNCS = ("issue", "mint")
REISSUE_WINDOW = 30 * 86400
REISSUE_TOL = 0.01
IDX = ["frozen_n", "frozen_usd", "frozen_unk", "burned_n", "burned_usd", "burned_unk", "unfrozen_n", "unfrozen_usd",
       "unfrozen_unk", "proposed_n", "still_n", "still_usd", "never_n", "never_usd", "reiss_n", "reiss_usd"]


def token_of(chain):
    if chain in SAFE_CHAINS:
        return "USDT0"
    return "USDC" if chain in ("usdc_eth", "sol_usdc") else "USDT"


def norm(chain, addr):
    if not addr:
        return None
    return addr.lower() if (chain in ("eth", "usdc_eth", "avax", "celo", "kaia", "kava") or chain in SAFE_CHAINS) else addr


def mint_amount(raw_data):
    """mint(address,uint256) calldata -> amount in USDT, else None."""
    d = (raw_data or "").lower().removeprefix("0x")
    if d.startswith("40c10f19") and len(d) >= 8 + 128:
        return int(d[8 + 64:8 + 128], 16) / 10 ** USDT_DECIMALS
    return None


# ---------------- reissue matching (pure) ----------------
def match_reissue(destroys, issues, window=REISSUE_WINDOW, tol=REISSUE_TOL):
    """destroys/issues: [(ts, amount, chain)]. Returns [(issue_index, destroy_index)] matched 1:1 (greedy, closest
    amount), same chain, amount within +-tol, destroy before issue and at most `window` seconds earlier."""
    used, pairs = set(), []
    order = sorted(range(len(issues)), key=lambda i: issues[i][0])
    for i in order:
        its, iamt, ich = issues[i]
        if not iamt:
            continue
        best, bd = None, None
        for j, (dts, damt, dch) in enumerate(destroys):
            if j in used or dch != ich or not damt:
                continue
            if not (dts <= its <= dts + window):
                continue
            diff = abs(iamt - damt) / max(iamt, damt)
            if diff <= tol and (bd is None or diff < bd):
                best, bd = j, diff
        if best is not None:
            used.add(best)
            pairs.append((i, best))
    return pairs


# ---------------- timelines and attribution (pure) ----------------
def attribute(events, balances):
    """events: [(ts, chain, addr, action, amount)] with action in freeze/unfreeze/destroy (amount for destroy and, for
    executed-only chains, the balance at freeze). balances: {(chain, addr): (balance, frozen_balance)}.
    Returns (freezes, destroys, unfreezes): freeze rows [(ts, chain, addr, kind, usd)] where kind is still/burned/
    unfrozen/dup; destroy rows [(ts, chain, addr, usd)]; unfreeze rows [(ts, chain, addr, usd)]."""
    by = {}
    for ev in sorted(events, key=lambda e: e[0]):
        by.setdefault((ev[1], ev[2]), []).append(ev)
    freezes, destroys, unfreezes = [], [], []
    for (chain, addr), evs in by.items():
        bal, fbal = balances.get((chain, addr), (None, None))
        open_idx = None  # index in freezes of the currently open interval
        last_unfreeze = None
        for ts, _c, _a, action, amount in evs:
            if action == "freeze":
                if open_idx is None:
                    freezes.append([ts, chain, addr, "still", amount])
                    open_idx = len(freezes) - 1
                else:
                    freezes.append([ts, chain, addr, "dup", None])
            elif action == "destroy":
                destroys.append((ts, chain, addr, amount))
                if open_idx is not None and freezes[open_idx][3] == "still":
                    freezes[open_idx][3] = "burned"
                    freezes[open_idx][4] = amount
                    open_idx = None
            elif action == "unfreeze":
                if open_idx is not None:
                    freezes[open_idx][3] = "unfrozen"
                    freezes[open_idx][4] = fbal
                    unfreezes.append((ts, chain, addr, fbal))
                    open_idx = None
                else:
                    unfreezes.append((ts, chain, addr, None))
        if open_idx is not None and freezes[open_idx][3] == "still":
            # at-freeze balance (executed-only chains) is kept if the balance table has nothing newer
            freezes[open_idx][4] = bal if bal is not None else freezes[open_idx][4]
    return freezes, destroys, unfreezes


def month_of(ts):
    return time.strftime("%Y-%m", time.gmtime(ts))


# ---------------- DB access ----------------
def load_events(conn):
    """All executed freeze/unfreeze/destroy actions as events, plus issue/mint rows and never-executed proposals."""
    ev, issues, never, proposed = [], [], [], []
    cls = {**{f: "freeze" for f in FREEZE_FUNCS}, **{f: "unfreeze" for f in UNFREEZE_FUNCS},
           **{f: "destroy" for f in DESTROY_FUNCS}}
    for r in conn.execute("SELECT chain, tx_id, function, target, amount, raw_data, status, executed, submitted_at, "
                          "executed_at, balance_now FROM proposals"):
        fn = r["function"]
        if fn in cls:
            sub = parse_iso(r["submitted_at"])
            if cls[fn] == "freeze":
                proposed.append((sub or parse_iso(r["executed_at"]) or 0, r["chain"]))
                if r["status"] in ("stale", "revoked", "failed") and sub:
                    never.append((sub, r["chain"], r["balance_now"]))
            if r["executed"] and r["executed_at"] and r["target"]:
                amt = r["amount"] if cls[fn] == "destroy" and (r["amount"] or 0) >= 0 else None
                ev.append((parse_iso(r["executed_at"]), r["chain"], norm(r["chain"], r["target"]), cls[fn], amt))
        elif fn in ISSUE_FUNCS and r["executed"] and r["executed_at"]:
            amt = r["amount"] if r["amount"] is not None else mint_amount(r["raw_data"])
            if amt:
                issues.append((parse_iso(r["executed_at"]), amt, r["chain"]))
    for f in conn.execute("SELECT source, subject, action, ts, amount FROM freezes"):
        ev.append((parse_iso(f["ts"]), f["source"], norm(f["source"], f["subject"]),
                   "freeze" if f["action"] == "freeze" else "unfreeze", f["amount"] if f["action"] == "freeze" else None))
        if f["action"] == "freeze":
            proposed.append((parse_iso(f["ts"]), f["source"]))
    return ev, issues, never, proposed


def load_balances(conn):
    return {(r["chain"], r["address"]): (r["balance"], r["frozen_balance"])
            for r in conn.execute("SELECT chain, address, balance, frozen_balance FROM balances")}


def compute(conn, now=None):
    """Aggregates per chain x month (see IDX) plus the top still-frozen list and coverage numbers."""
    now = now or now_ts()
    events, issues, never, proposed = load_events(conn)
    bal = load_balances(conn)
    freezes, destroys, unfreezes = attribute(events, bal)
    monthly = {}

    def cell(chain, ts):
        return monthly.setdefault(chain, {}).setdefault(month_of(ts), [0] * len(IDX))
    for ts, chain, addr, kind, usd in freezes:
        if kind == "dup":
            continue
        c = cell(chain, ts)
        c[0] += 1
        if usd is None:
            c[2] += 1
        else:
            c[1] += usd
        if kind == "still":
            c[10] += 1
            c[11] += usd or 0
    for ts, chain, addr, usd in destroys:
        c = cell(chain, ts)
        c[3] += 1
        if usd is None:
            c[5] += 1
        else:
            c[4] += usd
    for ts, chain, addr, usd in unfreezes:
        c = cell(chain, ts)
        c[6] += 1
        if usd is None:
            c[8] += 1
        else:
            c[7] += usd
    for ts, chain in proposed:
        cell(chain, ts)[9] += 1
    for ts, chain, usd in never:
        c = cell(chain, ts)
        c[12] += 1
        c[13] += usd or 0
    d_rows = [(ts, usd, chain) for ts, chain, addr, usd in destroys if usd]
    pairs = match_reissue(d_rows, issues)
    for i, _j in pairs:
        c = cell(issues[i][2], issues[i][0])
        c[14] += 1
        c[15] += issues[i][1]
    inflow = inflow_stats(conn, now)
    top = sorted(([chain, addr, usd, ts] for ts, chain, addr, kind, usd in freezes if kind == "still" and usd),
                 key=lambda x: -x[2])[:300]
    still_total = sum(1 for f in freezes if f[3] == "still")
    checked = conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0]
    targets = open_targets(conn)
    return {"idx": IDX, "monthly": {ch: m for ch, m in monthly.items()},
            "tokens": {ch: token_of(ch) for ch in monthly}, "top": top,
            "coverage": {"checked": len([t for t in targets if t in bal]), "total": len(targets), "rows": checked},
            "reissue": {"pairs": len(pairs), "reissued_usd": sum(issues[i][1] for i, _ in pairs),
                        "window_days": REISSUE_WINDOW // 86400, "tolerance": REISSUE_TOL},
            "still_open": still_total, "inflow": inflow, "generated_at": iso(now)}


# ---------------- balances ----------------
def supported(chain):
    """Chains whose balances can be read (Aptos and TON stay 'unknown')."""
    return chain in ("eth", "usdc_eth", "tron", "near") or chain in MSW_CHAINS or chain in SAFE_CHAINS or chain in SOLANA_SOURCES


def open_targets(conn):
    """(chain, address) of currently frozen, not destroyed addresses (the ones whose balance is 'still frozen')."""
    events, _i, _n, _p = load_events(conn)
    freezes, _d, _u = attribute(events, {})
    return sorted({(chain, addr) for ts, chain, addr, kind, usd in freezes if kind == "still" and supported(chain)})


def _batch_eth_balances(rpc_url, token, addrs):
    calls = [("eth_call", [{"to": token, "data": "0x" + SEL_BALANCE_OF + "0" * 24 + a[2:]}, "latest"]) for a in addrs]
    res = eth.rpc_batch(calls, chunk=25, url=rpc_url, retries=3)
    return {a: int(r, 16) / 10 ** USDT_DECIMALS for a, r in zip(addrs, res)}


def fetch_balances(chain, addrs):
    """Current USDT/USDC balances for addresses of one chain. Returns {addr: balance}; unsupported chains -> {}."""
    if chain == "eth":
        return _batch_eth_balances(eth.rpc_url(), crosschain.rpc_and_token("eth")[1], addrs)
    if chain == "usdc_eth":
        return _batch_eth_balances(eth.rpc_url(), USDC_ETH, addrs)
    if chain in MSW_CHAINS:
        c = MSW_CHAINS[chain]
        return _batch_eth_balances(c["rpc"], c["token"], addrs)
    if chain in SAFE_CHAINS:
        c = SAFE_CHAINS[chain]
        return _batch_eth_balances(c["rpc"], c["token"], addrs)
    if chain == "tron":
        from . import tron
        return {a: tron.balance_of(a) for a in addrs}
    if chain in SOLANA_SOURCES:
        from . import others
        out = {}
        for i in range(0, len(addrs), 50):
            part = addrs[i:i + 50]
            res = others.sol_rpc("getMultipleAccounts", [part, {"encoding": "jsonParsed"}])["value"]
            for a, v in zip(part, res):
                try:
                    out[a] = float(v["data"]["parsed"]["info"]["tokenAmount"]["uiAmountString"])
                except Exception:  # noqa: BLE001
                    out[a] = 0.0  # closed account
        return out
    if chain == "near":
        from . import near
        out = {}
        for a in addrs:
            try:
                out[a] = int(near.view("ft_balance_of", {"account_id": a}, account=near.TOKEN)) / 10 ** USDT_DECIMALS
            except Exception:  # noqa: BLE001
                continue
        return out
    return {}


def refresh_balances(conn, budget_sec=150):
    """Incremental, resumable: never-checked addresses first, then those checked before today. Writes at most one
    row change per address per day (checked_at is a date), so the DB file stays stable between runs."""
    t0 = time.time()
    today = iso(now_ts())[:10]
    have = {(r["chain"], r["address"]): r["checked_at"] for r in conn.execute("SELECT chain, address, checked_at FROM balances")}
    todo = [t for t in open_targets(conn) if have.get(t) != today]
    todo.sort(key=lambda t: (have.get(t) is not None, have.get(t) or ""))
    by_chain = {}
    for chain, addr in todo:
        by_chain.setdefault(chain, []).append(addr)
    size = {"tron": 8, "near": 5}
    n = 0
    for chain, addrs in by_chain.items():
        step = size.get(chain, 50)
        for i in range(0, len(addrs), step):
            if time.time() - t0 > budget_sec:
                log.info("balances: budget used, %s addresses refreshed this run", n)
                return n
            part = addrs[i:i + step]
            try:
                res = fetch_balances(chain, part)
            except Exception as e:  # noqa: BLE001
                log.warning("balances %s: %s", chain, str(e)[:100])
                break
            for a, b in res.items():
                conn.execute(
                    "INSERT INTO balances(chain,address,token,balance,checked_at,frozen_balance,frozen_checked_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(chain,address) DO UPDATE SET balance=excluded.balance, "
                    "checked_at=excluded.checked_at, frozen_balance=excluded.frozen_balance, "
                    "frozen_checked_at=excluded.frozen_checked_at",
                    (chain, a, token_of(chain), b, today, b, today))
                n += 1
            conn.commit()
    log.info("balances: %s addresses refreshed", n)
    return n


# ---------------- burned amounts from receipts ----------------
def parse_destroy_logs(logs, target):
    """logs: receipt logs (EVM: dicts with topics/data; TRON: topics without 0x). -> amount for `target` or None."""
    tgt = (target or "").lower().removeprefix("0x")
    found = []
    for lg in logs or []:
        topics = [t.lower().removeprefix("0x") for t in lg.get("topics", [])]
        if not topics or topics[0] not in (TOPIC_DESTROYED_LEGACY, TOPIC_DESTROYED_NEW):
            continue
        data = (lg.get("data") or "").lower().removeprefix("0x")
        if len(data) < 64:
            continue
        addr = topics[1][-40:] if len(topics) > 1 else data[24:64]
        found.append((addr, int(data[-64:], 16) / 10 ** USDT_DECIMALS))
    for a, amt in found:
        if tgt and a == tgt[-40:]:
            return amt
    return found[0][1] if len(found) == 1 else None


def enrich_destroy_amounts(conn, budget_sec=150):
    """Store the burned amount of executed destroy proposals (proposals.amount) from transaction receipts."""
    from .util import tron_to_hex20
    t0 = time.time()
    rows = conn.execute("SELECT chain, tx_id, target, exec_tx FROM proposals WHERE executed=1 AND amount IS NULL "
                        "AND function IN (?,?) AND exec_tx IS NOT NULL ORDER BY executed_at DESC", DESTROY_FUNCS).fetchall()
    done = 0
    by_chain = {}
    for r in rows:
        by_chain.setdefault(r["chain"], []).append(r)
    for chain, items in by_chain.items():
        if chain == "tron":
            from . import tron
            for r in items:
                if time.time() - t0 > budget_sec:
                    return done
                try:
                    from .util import http_json, key_pool
                    key = key_pool("TRONGRID_API_KEY").next()
                    o = http_json("POST", tron.TRONGRID + "/wallet/gettransactioninfobyid", json={"value": r["exec_tx"]},
                                  headers={"TRON-PRO-API-KEY": key} if key else {}, interval=0.06 if key else 0.5,
                                  retries=4)
                except Exception as e:  # noqa: BLE001
                    log.debug("tron receipt: %s", str(e)[:60])
                    continue
                if not o:
                    continue  # not (yet) available
                amt = parse_destroy_logs(o.get("log"), tron_to_hex20(r["target"]) if r["target"] else "")
                conn.execute("UPDATE proposals SET amount=? WHERE chain=? AND tx_id=?",
                             (amt if amt is not None else 0.0, chain, r["tx_id"]))
                done += 1
                if done % 25 == 0:
                    conn.commit()
        elif chain == "eth" or chain in MSW_CHAINS or chain in SAFE_CHAINS:
            url = eth.rpc_url() if chain == "eth" else (MSW_CHAINS.get(chain) or SAFE_CHAINS.get(chain))["rpc"]
            for i in range(0, len(items), 25):
                if time.time() - t0 > budget_sec:
                    conn.commit()
                    return done
                part = items[i:i + 25]
                try:
                    res = eth.rpc_batch([("eth_getTransactionReceipt", [p["exec_tx"]]) for p in part], chunk=25, url=url,
                                        retries=3)
                except Exception as e:  # noqa: BLE001
                    log.debug("%s receipts: %s", chain, str(e)[:60])
                    continue
                for p, rc in zip(part, res):
                    if not rc:
                        continue
                    amt = parse_destroy_logs(rc.get("logs"), p["target"])
                    conn.execute("UPDATE proposals SET amount=? WHERE chain=? AND tx_id=?",
                                 (amt if amt is not None else 0.0, chain, p["tx_id"]))
                    done += 1
                conn.commit()
    conn.commit()
    return done


def months_since(period, now=None):
    """First month included for a period: '30d' | '90d' | '12m' | 'all' -> 'YYYY-MM' (None = no lower bound)."""
    now = now or now_ts()
    days = {"30d": 30, "90d": 90, "12m": 365}.get(period)
    return month_of(now - days * 86400) if days else None


def summarize(an, chains=None, since=None, token=None):
    """Sum the monthly aggregates over chains / token / months >= since. Mirrors the dashboard's JS logic."""
    tot = dict.fromkeys(IDX, 0)
    for ch, months in an["monthly"].items():
        if chains is not None and ch not in chains:
            continue
        if token and an["tokens"].get(ch) != token:
            continue
        for mo, v in months.items():
            if since and mo < since:
                continue
            for k, x in zip(IDX, v):
                tot[k] += x
    return tot


PERIODS = {"30d": 30, "90d": 90, "12m": 365, "all": None}


def inflow_stats(conn, now):
    """Transfers sent to already-frozen addresses: exact distinct counts and top lists for every period."""
    rows = [(r["chain"], r["frozen_address"], r["sender"], r["amount"], parse_iso(r["ts"]))
            for r in conn.execute("SELECT chain, frozen_address, sender, amount, ts FROM inflows_after_freeze")]
    out = {"periods": {}, "senders": {}, "receivers": {}}
    try:
        from . import inflows
        out["coverage"] = list(inflows.coverage(conn))
    except Exception:  # noqa: BLE001
        out["coverage"] = None
    for p, days in PERIODS.items():
        cutoff = now - days * 86400 if days else 0
        per, snd, rcv = {}, {}, {}
        for chain, addr, sender, amt, ts in rows:
            if ts < cutoff:
                continue
            c = per.setdefault(chain, [set(), set(), 0, 0.0])
            c[0].add(addr)
            c[1].add(sender)
            c[2] += 1
            c[3] += amt
            for d, key in ((snd, (chain, sender)), (rcv, (chain, addr))):
                e = d.setdefault(key, [0, 0.0, 0])
                e[0] += 1
                e[1] += amt
                e[2] = max(e[2], ts)
        out["periods"][p] = {ch: [len(v[0]), len(v[1]), v[2], round(v[3], 2)] for ch, v in per.items()}
        for name, d in (("senders", snd), ("receivers", rcv)):
            top = sorted(d.items(), key=lambda kv: -kv[1][1])[:150]
            out[name][p] = [[k[0], k[1], v[0], round(v[1], 2), v[2]] for k, v in top]
    return out


def inflow_summary(an, period, chains=None):
    """[addresses, senders, transfers, usd] summed over chains (mirrors the dashboard)."""
    tot = [0, 0, 0, 0.0]
    for ch, v in an["inflow"]["periods"][period].items():
        if chains is None or ch in chains:
            tot = [a + b for a, b in zip(tot, v)]
    return tot
