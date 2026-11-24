"""Evasion analysis around freezes: exit classes, BitOK-style outcome classes, lead times, priority-fee anomalies, third-party pulls
(sweeper candidates), service-fee collectors and the verified evasion watchlist (config/evasion_watchlist.yaml).

Everything here is a transparent heuristic computed from public chain data; the classes are this tool's own definitions, not
findings about anyone's intent. Pure functions take plain values so they are testable offline."""
import logging
import statistics

from .constants import ROOT
from .labels import NETWORK_OF, key_of
from . import reasons

log = logging.getLogger("freezewatch.evasion")
WATCHLIST_FILE = ROOT / "config" / "evasion_watchlist.yaml"
KINDS = ("interceptor", "sweeper", "fee_collector", "dex", "bridge", "delegate")
DUST_USD = 1.0               # transfers below this are noise (address poisoning, zero-value spam) in class statistics
FEE_COLLECTOR_MIN_SOURCES = 50   # recipients paid by this many different frozen addresses are service-fee collectors, not escapes
SWEEPER_MIN_VICTIMS = 2      # a contract that pulled from this many later-frozen addresses is a "sweeper candidate"
EXIT_CLASSES = ("cex", "bridge", "dex", "mixer", "watchlist", "unlabelled")


# ---------------- watchlist ----------------
def load_watchlist(path=None):
    """-> {(chain|'*', key): entry}. Entries need address, kind (KINDS), name and an evidence text (tx hash, URL or note)."""
    from .addresses import canon
    p = path or WATCHLIST_FILE
    if not p.exists():
        return {}
    out = {}
    for i, e in enumerate(reasons.parse_yaml(p.read_text(encoding="utf-8"))):
        if e.get("kind") not in KINDS or not e.get("address") or not e.get("evidence") or not e.get("name"):
            log.warning("evasion_watchlist.yaml entry %s skipped (needs address, name, kind in %s, evidence)", i + 1, KINDS)
            continue
        k = canon(e["address"])
        if k:
            out[(e.get("chain") or "*", k)] = e
    return out


def watch_entry(watch, chain, key):
    return watch.get((chain, key)) or watch.get(("*", key))


# ---------------- exit classification ----------------
def classify_exit(chain, recipient, labels, watch):
    """Class of one escaped outflow: cex / bridge / dex / mixer (wallet-attribution labels), watchlist (a verified evasion address),
    or unlabelled. Known DEX / bridge contracts of the watchlist win over labels."""
    from .addresses import canon_for
    ck = canon_for(chain, recipient)
    w = watch_entry(watch, chain, ck)
    if w and w["kind"] in ("dex", "bridge"):
        return w["kind"], w["name"]
    if w:
        return "watchlist", w["name"]
    net = NETWORK_OF.get(chain)
    lb = labels.get((net, key_of(net, recipient))) if net else None
    if lb:
        cat = (lb.get("category") or "")
        cats = set((lb.get("categories") or "").split(";")) | {cat}
        for want, cls in (("bridge", "bridge"), ("dex", "dex"), ("defi", "dex"), ("exchange", "cex"), ("cex", "cex"), ("mixer", "mixer")):
            if want in cats:
                return cls, lb.get("entity") or lb.get("label")
    return "unlabelled", None


def exit_breakdown(rows, labels, watch):
    """rows: classify() dicts (escaped only) -> {class: {usd, n}} sorted by usd."""
    out = {c: {"usd": 0.0, "n": 0} for c in EXIT_CLASSES}
    for r in rows:
        if r["internal"] or r["amount"] < DUST_USD:
            continue
        c, _n = classify_exit(r["chain"], r["recipient"], labels, watch)
        out[c]["usd"] += r["amount"]
        out[c]["n"] += 1
    return {k: {"usd": round(v["usd"], 2), "n": v["n"]} for k, v in sorted(out.items(), key=lambda kv: -kv[1]["usd"])}


# ---------------- BitOK-style outcome classes and lead times ----------------
def outcome_class(escaped, window, remaining):
    """Per freeze, from the 72 h outflow and the amount still on the address when it was frozen:
    out_share = escaped / (escaped + remaining). clean < 1 %; racing = at least 20 % of everything moved between proposal and
    execution; weak = at least 50 % had left BEFORE the proposal (the freeze caught little); otherwise partial.
    -> (class, out_share) or (None, None) when the frozen amount is unknown."""
    if remaining is None:
        return None, None
    tot = escaped + remaining
    if tot <= 0:
        return "clean", 0.0
    share = escaped / tot
    if share < 0.01:
        return "clean", share
    if window / tot >= 0.20:
        return "racing", share
    if share >= 0.50 and (escaped - window) / tot >= 0.50:
        return "weak", share
    return "partial", share


def lead_times(rows, submitted_ts, executed_ts):
    """In-window outflows of one freeze -> list of {sec_after_submit, sec_before_execute, usd, tx}; ts are epoch seconds."""
    out = []
    for r in rows:
        if submitted_ts is None or not (submitted_ts <= r["ts"] <= executed_ts):
            continue
        out.append({"after_submit_s": r["ts"] - submitted_ts, "before_execute_s": executed_ts - r["ts"], "usd": r["amount"],
                    "tx": r.get("tx")})
    return sorted(out, key=lambda x: x["after_submit_s"])


def lead_summary(items):
    """Median seconds after submit / before execute, weighted by nothing: simple median over transfers."""
    if not items:
        return None
    return {"n": len(items), "median_after_submit_s": int(statistics.median(i["after_submit_s"] for i in items)),
            "median_before_execute_s": int(statistics.median(i["before_execute_s"] for i in items)),
            "first_after_submit_s": min(i["after_submit_s"] for i in items)}


# ---------------- priority-fee anomaly (Ethereum) ----------------
def effective_tip(tx, base_fee):
    """Priority fee actually paid per gas (wei) of a transaction dict (hex fields) given the block base fee (wei)."""
    h = lambda k: int(tx[k], 16) if tx.get(k) not in (None, "0x") else None  # noqa: E731
    mp, mf, gp = h("maxPriorityFeePerGas"), h("maxFeePerGas"), h("gasPrice")
    if mp is not None and mf is not None:
        return max(0, min(mp, mf - base_fee))
    return max(0, (gp or 0) - base_fee)


def fee_anomaly(tx, block, ratio=10.0, min_gwei=5.0):
    """Compare the tip of `tx` with the median tip of the other transactions of its block (full tx objects).
    -> {tip_gwei, median_gwei, ratio, index, flagged}. Direct coinbase payments need traces and are not detected here."""
    base = int(block.get("baseFeePerGas", "0x0"), 16)
    tips = [effective_tip(t, base) for t in block.get("transactions", []) if t.get("hash") != tx.get("hash") and isinstance(t, dict)]
    med = statistics.median(tips) if tips else 0
    tip = effective_tip(tx, base)
    r = (tip / med) if med > 0 else (float("inf") if tip > 0 else 0.0)
    return {"tip_gwei": round(tip / 1e9, 3), "median_gwei": round(med / 1e9, 3), "ratio": None if r == float("inf") else round(r, 1),
            "index": int(tx["transactionIndex"], 16) if tx.get("transactionIndex") else None,
            "flagged": bool(tip / 1e9 >= min_gwei and r >= ratio)}


# ---------------- third-party pulls and sweeper candidates ----------------
def third_party(caller, frozen_address):
    """True when a transfer out of the frozen address was initiated by someone else (a contract / bot pulled the funds)."""
    return bool(caller) and caller.lower() != (frozen_address or "").lower()


def sweeper_candidates(pulls, min_victims=SWEEPER_MIN_VICTIMS):
    """pulls: [(contract, frozen_address, caller)] third-party pulls -> {contract: {victims, callers}} for contracts that pulled
    from at least `min_victims` different later-frozen addresses."""
    by = {}
    for contract, frozen, caller in pulls:
        d = by.setdefault(contract, {"victims": set(), "callers": set()})
        d["victims"].add(frozen)
        if caller:
            d["callers"].add(caller)
    return {c: {"victims": len(d["victims"]), "callers": sorted(d["callers"])} for c, d in by.items() if len(d["victims"]) >= min_victims}


# ---------------- false-positive filters ----------------
def fee_collectors(rows, watch=None, min_sources=FEE_COLLECTOR_MIN_SOURCES):
    """Recipients paid by many different frozen addresses (service fees, exchanges' sweep wallets...) plus the configured
    fee collectors: excluded from repeat-recipient / interceptor statistics. rows: [(chain, frozen, recipient)]."""
    src = {}
    for chain, frozen, rcp in rows:
        src.setdefault((chain, rcp), set()).add(frozen)
    out = {k for k, v in src.items() if len(v) >= min_sources}
    for (c, key), e in (watch or {}).items():
        if e["kind"] == "fee_collector":
            out.add((c, key))
    return out


def repeat_recipients(rows, collectors=frozenset(), min_sources=2):
    """Recipients that received funds from >= min_sources different frozen addresses (interceptor candidates), without the
    fee collectors and dust. rows: [(chain, frozen, recipient, usd)] -> [(chain, recipient, n_sources, usd)] by usd."""
    agg = {}
    for chain, frozen, rcp, usd in rows:
        if usd < DUST_USD or (chain, rcp) in collectors:
            continue
        d = agg.setdefault((chain, rcp), [set(), 0.0])
        d[0].add(frozen)
        d[1] += usd
    return sorted(((c, r, len(s), round(u, 2)) for (c, r), (s, u) in agg.items() if len(s) >= min_sources), key=lambda x: -x[3])
