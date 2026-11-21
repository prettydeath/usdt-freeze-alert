"""Derivation of proposal state from events + statuses + blacklist replay."""
from .db import ensure_proposal, update_proposal, events_grouped
from .util import iso, now_ts, parse_iso

_KIND_ORDER = {"submit": 0, "confirm": 1, "revoke": 2, "execute": 3, "fail": 4}


def sort_events(events):
    return sorted(events, key=lambda e: (e["ts"], _KIND_ORDER.get(e["kind"], 9)))


def net_state(events):
    """Returns (signers:set, had_revoke, had_fail) after replaying by time."""
    signers: set[str] = set()
    had_revoke = had_fail = False
    for e in sort_events(events):
        k = e["kind"]
        if k in ("submit", "confirm") and e["signer"]:
            signers.add(e["signer"])
        elif k == "revoke":
            signers.discard(e["signer"])
            had_revoke = True
        elif k == "fail":
            had_fail = True
    return signers, had_revoke, had_fail


def infer_exec(events, required):
    """Execution moment.
    1) if there is an explicit execute event (ETH: Execution; TRON: executeTransaction) - that one;
    2) otherwise (TRON): the time of the first confirmation (submit/confirm) at which the number of
       active confirmations (accounting for revocations) reached `required`.
    Approximation: the current required() is used; if it changed, the result may be off.
    Returns dict(ts, tx_hash, signer) or None."""
    evs = sort_events(events)
    for e in evs:
        if e["kind"] == "execute":
            signer = ""
            for c in evs:  # signer - who confirmed in the same transaction
                if c["tx_hash"] == e["tx_hash"] and c["kind"] in ("confirm", "submit"):
                    signer = c["signer"]
            return {"ts": e["ts"], "tx_hash": e["tx_hash"], "signer": signer}
    if not required:
        return None
    active: set[str] = set()
    for e in evs:
        if e["kind"] in ("submit", "confirm") and e["signer"]:
            active.add(e["signer"])
            if len(active) >= required:
                return {"ts": e["ts"], "tx_hash": e["tx_hash"], "signer": e["signer"]}
        elif e["kind"] == "revoke":
            active.discard(e["signer"])
    return None


def compute_status(executed, had_failure, confirmations, had_revoke, submitted_at, stale_days, now=None):
    if executed:
        return "executed"
    if confirmations == 0 and had_revoke:
        return "revoked"
    if had_failure:
        return "failed"
    sub = parse_iso(submitted_at)
    if sub is None:
        return "stale"  # age unknown (e.g. polling without logs): do not pass it off as a fresh proposal
    if sub is not None and ((now or now_ts()) - sub) > stale_days * 86400:
        return "stale"
    return "pending"


def derive_all(conn, chain, ids=None, stale_days=7, conf_override=None):
    """Recomputes derived fields for proposals (all or the given ids).
    conf_override: {tx_id: onchain_confirmation_count} for pending."""
    evs = events_grouped(conn, chain, set(ids) if ids is not None else None)
    q = "SELECT tx_id, executed, required, confirmations FROM proposals WHERE chain=?"
    rows = conn.execute(q, (chain,)).fetchall()
    for r in rows:
        tid = r["tx_id"]
        if ids is not None and tid not in ids:
            continue
        ev = evs.get(tid, [])
        signers, had_rev, had_fail = net_state(ev)
        subs = [e for e in sort_events(ev) if e["kind"] == "submit"]
        vals = {}
        if subs:
            vals.update(submitted_at=subs[0]["ts"], submit_tx=subs[0]["tx_hash"], submitter=subs[0]["signer"] or None)
        executed = bool(r["executed"])
        conf = len(signers)
        if conf_override and tid in conf_override and not executed:
            conf = conf_override[tid]
        vals["confirmations"] = conf
        sub_ts = vals.get("submitted_at")
        if executed:
            ex = infer_exec(ev, r["required"])
            if ex is None and ev:
                last = [e for e in sort_events(ev) if e["kind"] in ("submit", "confirm")]
                if last:
                    ex = {"ts": last[-1]["ts"], "tx_hash": last[-1]["tx_hash"], "signer": last[-1]["signer"]}
            if ex:
                vals.update(executed_at=ex["ts"], exec_tx=ex["tx_hash"], executor=ex["signer"] or None)
                if sub_ts:
                    vals["delay_seconds"] = max(0, parse_iso(ex["ts"]) - parse_iso(sub_ts))
        else:
            vals.update(executed_at=None, exec_tx=None, executor=None, delay_seconds=None)
        vals["status"] = compute_status(executed, had_fail, conf, had_rev, sub_ts, stale_days)
        update_proposal(conn, chain, tid, **vals)


def rebuild_blacklist(conn):
    """Full replay of executed add/remove/destroy into the blacklist table."""
    q = """SELECT chain, tx_id, function, target, executed_at FROM proposals
           WHERE executed=1 AND target IS NOT NULL AND executed_at IS NOT NULL
             AND function IN ('addBlackList','removeBlackList','destroyBlackFunds')
           ORDER BY executed_at, tx_id"""
    state = {}
    for r in conn.execute(q):
        key = (r["chain"], r["target"])
        s = state.setdefault(key, {"status": "unblocked", "first": None, "last": None, "tx": None, "destroyed": 0})
        if r["function"] == "addBlackList":
            s["status"] = "blocked"
            s["first"] = s["first"] or r["executed_at"]
            s["tx"] = r["tx_id"]
        elif r["function"] == "removeBlackList":
            s["status"] = "unblocked"
        else:
            s["destroyed"] = 1
            s["tx"] = s["tx"] if s["tx"] is not None else r["tx_id"]
        s["last"] = r["executed_at"]
    new = sorted((chain, addr, s["status"], s["first"], s["last"], s["tx"], s["destroyed"])
                 for (chain, addr), s in state.items())
    old = sorted(tuple(r) for r in conn.execute(
        "SELECT chain,address,status,first_blocked_at,last_change_at,proposal_tx_id,funds_destroyed FROM blacklist"))
    if new == old:
        return  # unchanged - leave the DB file alone
    conn.execute("DELETE FROM blacklist")
    conn.executemany(
        "INSERT INTO blacklist(chain,address,status,first_blocked_at,last_change_at,proposal_tx_id,funds_destroyed)"
        " VALUES(?,?,?,?,?,?,?)", new)
