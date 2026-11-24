"""Cross-chain signals: an address that was just frozen (or has a freeze proposal) on one chain still holds USDT / USDT0 /
USDC on other chains where it is NOT blocked. Detection and follow-up run on every update:

1. detect(): for each fresh freeze event (executed, or a pending proposal) of an EVM-style / TRON address that has not been
   checked yet, ask every other tracked chain "blocked? balance?". Not blocked and balance >= MIN_USD -> an open signal.
2. resolve(): open signals are re-checked; when a chain blocks the address the lag is recorded together with whether the
   balance dropped before the freeze landed ("moved before").
Signals expire after EXPIRE_DAYS without resolution. All network access goes through `checker(chain, hex20)` so tests run
offline."""
import json
import logging
import time

from . import analytics, crosschain, eth
from .constants import SEL_BALANCE_OF, TRON_USDT, USDT_DECIMALS
from .db import iso
from .registry import ALIAS_CHAINS
from .util import HttpError, http_json, key_pool, now_ts, parse_iso, tron_from_hex20

log = logging.getLogger("freezewatch.signals")
MIN_USD = 1000.0
TRIGGER_FRESH_SEC = 48 * 3600
EXPIRE_DAYS = 14
MOVED_FRACTION = 0.9   # balance below 90% of the balance at signal time = funds moved before the freeze
TRONGRID = "https://api.trongrid.io"
FREEZE_FNS = ("addBlackList", "addToBlockedList", "freeze_accounts", "freeze_wallet")


def token_label(chain):
    from .alerts import token_of
    return token_of(chain)


def chains_to_check():
    """[(chain, target dict)] of the live-check targets (EVM) plus TRON; aliases share another chain's token."""
    from . import livecheck
    out = []
    for t in livecheck.targets()["evm"]:
        c = t["chain"]
        if c in ALIAS_CHAINS:
            continue
        out.append((c, t))
    out.append(("tron", None))
    return out


def _evm_check(t, hex20):
    last = None
    for url in t["rpcs"]:
        try:
            b, bal = eth.rpc_batch([("eth_call", [{"to": t["contract"], "data": "0x" + t["sel"] + "0" * 24 + hex20}, "latest"]),
                                    ("eth_call", [{"to": t["contract"], "data": "0x" + SEL_BALANCE_OF + "0" * 24 + hex20}, "latest"])],
                                   url=url, retries=2)
            if not (isinstance(b, str) and len(b) == 66):
                raise HttpError("bad result")
            return int(b, 16) != 0, int(bal, 16) / 10 ** USDT_DECIMALS
        except Exception as e:  # noqa: BLE001
            last = e
    raise HttpError(f"{t['chain']}: {str(last)[:60]}")


def _tron_check(hex20):
    from . import trongrid
    param = "0" * 24 + hex20

    def call(sel):
        for _ in range(3):
            o = trongrid.request("POST", "/wallet/triggerconstantcontract", max_sec=30,
                                 json={"owner_address": "41" + "0" * 40, "contract_address": "41" + _tron_hex(),
                                       "function_selector": sel, "parameter": param, "visible": False})
            r = (o.get("constant_result") or [None])[0]
            if r:
                return int(r, 16)
            time.sleep(0.5)
        raise HttpError("tron: empty result")
    return call("isBlackListed(address)") != 0, call("balanceOf(address)") / 10 ** USDT_DECIMALS


def _tron_hex():
    from .util import tron_to_hex20
    return tron_to_hex20(TRON_USDT)


def check_chain(chain, hex20, targets=None):
    """-> (blocked: bool, balance: float). Raises on network errors (caller skips the chain)."""
    hex20 = hex20.lower().removeprefix("0x")
    if chain == "tron":
        return _tron_check(hex20)
    t = dict((targets or dict(chains_to_check()))[chain])
    return _evm_check(t, hex20)


# ---------------- triggers ----------------
def triggers(conn, now=None):
    """Fresh freeze events: [(ref, chain, hex20, ts, stage)] newest first (executed freezes and pending proposals)."""
    now = now or now_ts()
    out = []
    qm = ",".join("?" * len(FREEZE_FNS))
    for r in conn.execute(f"SELECT chain, tx_id, target, executed, executed_at, submitted_at, status, first_seen_at FROM proposals "
                          f"WHERE function IN ({qm}) AND target IS NOT NULL", FREEZE_FNS):
        chain = "arb1" if r["chain"] == "arb_usdt" else r["chain"]
        a = crosschain.norm_addr(r["chain"], r["target"])
        if not a or a in analytics.special_sinks(r["chain"]):
            continue
        if r["executed"] and r["executed_at"]:
            ts, stage = parse_iso(r["executed_at"]), "frozen"
        elif r["status"] == "pending":
            ts, stage = parse_iso(r["submitted_at"]) or parse_iso(r["first_seen_at"]), "proposed"
        else:
            continue
        if ts and now - ts <= TRIGGER_FRESH_SEC:
            out.append((f"{r['chain']}:{r['tx_id']}:{stage}", chain, a, ts, stage))
    out.sort(key=lambda x: -x[3])
    return out


def detect(conn, checker=check_chain, now=None, budget_sec=90, min_usd=MIN_USD, chains=None):
    """Create / extend signals for fresh triggers. A chain whose check failed (RPC / TronGrid error, deadline) is NOT skipped:
    it stays in `signal_checked.pending` and is retried on the next run, only that chain (the trigger is fresh for 48 h).
    -> number of NEW signals."""
    now = now or now_ts()
    t0 = time.time()
    all_chains = chains if chains is not None else [c for c, _ in chains_to_check()]
    new = 0
    for ref, chain, addr, ts, stage in triggers(conn, now):
        if time.time() - t0 > budget_sec:
            break
        row = conn.execute("SELECT pending, risk FROM signal_checked WHERE address=? AND trigger_ref=?", (addr, ref)).fetchone()
        if row and not row["pending"]:
            continue                                   # fully checked
        todo = json.loads(row["pending"]) if row else [c for c in all_chains]
        risk = json.loads(row["risk"]) if row and row["risk"] else []
        failed = []
        for c in todo:
            if c == chain:
                continue
            if time.time() - t0 > budget_sec:
                failed.append(c)
                continue
            try:
                blocked, bal = checker(c, addr)
            except Exception as e:  # noqa: BLE001
                log.debug("signal check %s: %s", c, str(e)[:60])
                failed.append(c)
                continue
            if not blocked and bal >= min_usd:
                risk.append([c, token_label(c), round(bal, 2), round(bal, 2)])
        conn.execute("INSERT OR REPLACE INTO signal_checked(address,trigger_ref,checked_at,pending,risk) VALUES(?,?,?,?,?)",
                     (addr, ref, iso(now), json.dumps(failed) if failed else "", json.dumps(risk)))
        if failed:
            log.info("signals: %s %s: %s chain check(s) pending (%s), retried next run", chain, addr[:10], len(failed), ", ".join(failed[:5]))
        if not risk:
            continue
        cur = conn.execute("SELECT id, at_risk FROM signals WHERE address=? AND frozen_on=?", (addr, chain)).fetchone()
        if cur:  # known signal: refresh time / stage and add chains found late
            have = json.loads(cur["at_risk"])
            have += [e for e in risk if e[0] not in {x[0] for x in have}]
            conn.execute("UPDATE signals SET frozen_at=?, stage=?, at_risk=?, updated_at=? WHERE id=?",
                         (iso(ts), stage, json.dumps(have), iso(now), cur["id"]))
            continue
        conn.execute("INSERT INTO signals(address,frozen_on,frozen_at,first_seen,at_risk,status,stage,resolutions,updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (addr, chain, iso(ts), iso(now), json.dumps(risk), "open", stage, "[]", iso(now)))
        new += 1
    conn.commit()
    if new:
        log.info("signals: %s new cross-chain signals", new)
    return new


def pending_checks(conn):
    """Number of triggers with at least one chain check still pending (shown on the Live tab and the Status page)."""
    return conn.execute("SELECT COUNT(*) FROM signal_checked WHERE pending<>'' AND pending IS NOT NULL").fetchone()[0]


def executed_freeze_ts(conn, chain, addr):
    """Execution time of a freeze of `addr` on `chain` from our own data (None if not seen)."""
    best = None
    for r in conn.execute("SELECT target, executed_at, chain FROM proposals WHERE executed=1 AND executed_at IS NOT NULL "
                          "AND function IN (?,?,?,?) AND (chain=? OR (?='arb1' AND chain='arb_usdt'))", (*FREEZE_FNS, chain, chain)):
        if crosschain.norm_addr(r["chain"], r["target"]) == addr:
            t = parse_iso(r["executed_at"])
            best = t if best is None else min(best, t)
    return best


def resolve(conn, checker=check_chain, now=None, budget_sec=60):
    """Re-check open signals: record blocks (with lag and 'moved before'), expire old ones."""
    now = now or now_ts()
    t0 = time.time()
    n = 0
    for s in conn.execute("SELECT * FROM signals WHERE status='open'").fetchall():
        risk, res = json.loads(s["at_risk"]), json.loads(s["resolutions"] or "[]")
        done = {x["chain"] for x in res}
        frozen_at = parse_iso(s["frozen_at"])
        for entry in risk:
            c = entry[0]
            if c in done or time.time() - t0 > budget_sec:
                continue
            try:
                blocked, bal = checker(c, s["address"])
            except Exception:  # noqa: BLE001
                continue
            if not blocked:
                entry[3] = round(bal, 2)
                continue
            at = executed_freeze_ts(conn, c, s["address"]) or now
            res.append({"chain": c, "blocked_at": iso(at), "lag_s": max(0, int(at - frozen_at)),
                        "balance_before": entry[2], "balance_at_block": round(bal, 2),
                        "moved_before": bool(bal < MOVED_FRACTION * entry[2])})
            n += 1
        status = "resolved" if len({x["chain"] for x in res}) >= len(risk) else "open"
        if status == "open" and now - parse_iso(s["first_seen"]) > EXPIRE_DAYS * 86400:
            status = "expired"
        conn.execute("UPDATE signals SET at_risk=?, resolutions=?, status=?, updated_at=? WHERE id=?",
                     (json.dumps(risk), json.dumps(res), status, iso(now), s["id"]))
    conn.commit()
    return n


def display(addr, chain):
    return tron_from_hex20(addr.removeprefix("0x")) if chain == "tron" else addr


# ---------------- aggregates ----------------
def payload(conn, now=None):
    """Signals for the dashboard: open list (newest first) and resolved/expired history with lag statistics."""
    import statistics
    rows = [dict(r) for r in conn.execute("SELECT * FROM signals ORDER BY first_seen DESC")]
    out = []
    for r in rows:
        risk, res = json.loads(r["at_risk"]), json.loads(r["resolutions"] or "[]")
        out.append({"id": r["id"], "address": r["address"], "show": display(r["address"], r["frozen_on"]),
                    "frozen_on": r["frozen_on"], "frozen_at": r["frozen_at"], "first_seen": r["first_seen"], "stage": r["stage"],
                    "status": r["status"], "at_risk": risk, "resolutions": res})
    lags = [x["lag_s"] for r in out for x in r["resolutions"]]
    moved = [x for r in out for x in r["resolutions"]]
    open_ = [r for r in out if r["status"] == "open"]
    total_before = sum(x["balance_before"] for x in moved)
    lost = sum(max(0.0, x["balance_before"] - x["balance_at_block"]) for x in moved)
    return {"open": open_, "history": [r for r in out if r["status"] != "open"][:100],
            "stats": {"signals": len(out), "open": len(open_), "resolved_chains": len(moved),
                      "median_lag_s": int(statistics.median(lags)) if lags else None,
                      "moved_before_pct": round(100 * sum(1 for x in moved if x["moved_before"]) / len(moved), 1) if moved else None,
                      "moved_balance_pct": round(100 * lost / total_before, 1) if total_before else None,
                      "pending_checks": pending_checks(conn),
                      "open_usd": round(sum(e[3] for r in open_ for e in r["at_risk"]
                                            if e[0] not in {x["chain"] for x in r["resolutions"]}), 2)}}


def events(conn, now=None):
    """Telegram events: signals first seen within the freshness window (dedupe happens in alerts.dispatch)."""
    from .alerts import FRESH_SEC, fmt_signal
    now = now or now_ts()
    out = []
    for s in conn.execute("SELECT * FROM signals WHERE status='open'"):
        if now - (parse_iso(s["first_seen"]) or 0) > FRESH_SEC:
            continue
        out.append({"kind": "xchain", "chain": "xchain", "id": s["id"], "key_kind": "xsignal", "text": fmt_signal(dict(s))})
    return out
