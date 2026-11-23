"""Delay and activity statistics. Output -> data/stats.json."""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .constants import BALANCE_FUNCS, DATA_DIR, FREEZE_FUNCS, SOURCE_LABELS
from .util import iso, now_ts, parse_iso

WINDOWS = {"7d": 7, "30d": 30, "all": None}
BUCKETS = [("0 s (no advance notice)", 0, 1), ("under 5 min", 1, 300), ("5-30 min", 300, 1800), ("30-60 min", 1800, 3600),
           ("1-3 h", 3600, 10800), ("3-6 h", 10800, 21600), ("6-24 h", 21600, 86400), (">24 h", 86400, None)]


def percentile(vals, p):
    """Linear interpolation (like numpy.percentile). vals - sorted list."""
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    k = (len(vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def delay_stats(delays):
    d = sorted(delays)
    n = len(d)
    if not n:
        return {"n": 0}
    return {
        "n": n, "min": d[0], "p25": percentile(d, 25), "median": percentile(d, 50),
        "p75": percentile(d, 75), "p90": percentile(d, 90), "max": d[-1],
        "share_same_block": sum(1 for x in d if x == 0) / n,
        "share_le_60s": sum(1 for x in d if x <= 60) / n,
        "share_lt_5m": sum(1 for x in d if x < 300) / n,
        "share_lt_1h": sum(1 for x in d if x < 3600) / n,
        "share_lt_6h": sum(1 for x in d if x < 21600) / n,
        "share_gt_24h": sum(1 for x in d if x > 86400) / n,
    }


def histogram(delays):
    out = []
    for label, lo, hi in BUCKETS:
        if lo == 0 and hi == 1:
            c = sum(1 for x in delays if x == 0)
        else:
            c = sum(1 for x in delays if x >= lo and (hi is None or x < hi))
        out.append({"label": label, "count": c})
    return out


def week_start(ts):
    d = datetime.fromtimestamp(ts, timezone.utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _rows(conn):
    rows = []
    for r in conn.execute("SELECT * FROM proposals"):
        d = dict(r)
        d["sub_ts"] = parse_iso(d["submitted_at"])
        d["exec_ts"] = parse_iso(d["executed_at"])
        rows.append(d)
    return rows


def compute(conn, now=None):
    now = now or now_ts()
    rows = _rows(conn)
    out = {"generated_at": iso(now), "scopes": {}, "signers": {}, "histogram": {}, "weekly": {},
           "pending_usdt": {}, "coverage": {}}
    funcs = sorted({r["function"] for r in rows if r["function"]})
    CH = sorted({r["chain"] for r in rows})
    out["chains"] = CH
    for scope in CH + ["all"]:
        srows = [r for r in rows if scope == "all" or r["chain"] == scope]
        out["scopes"][scope] = {}
        for wname, days in WINDOWS.items():
            cutoff = now - days * 86400 if days else 0
            wout = {}
            for fn in (["all"] + funcs) if scope in ("eth", "tron", "all") else ["all"]:
                fr = [r for r in srows if fn == "all" or r["function"] == fn]
                if not fr:
                    continue
                prop = [r for r in fr if (r["sub_ts"] or 0) >= cutoff and (days is None or r["sub_ts"])]
                ex = [r for r in fr if r["executed"] and r["exec_ts"] and r["exec_ts"] >= cutoff]
                delays = [r["delay_seconds"] for r in ex if r["delay_seconds"] is not None]
                span_days = days
                if not span_days:
                    first = min((r["exec_ts"] for r in fr if r["exec_ts"]), default=None)
                    span_days = max(1.0, (now - first) / 86400) if first else None
                cnt = lambda s: sum(1 for r in prop if r["status"] == s)
                wout[fn] = {
                    "proposed": len(prop), "executed": len(ex), "pending": cnt("pending"), "revoked": cnt("revoked"),
                    "stale": cnt("stale"), "failed": cnt("failed"),
                    "delay": delay_stats(delays),
                    "exec_per_day": round(len(ex) / span_days, 2) if span_days else None,
                    "exec_per_week": round(len(ex) / span_days * 7, 2) if span_days else None,
                }
            out["scopes"][scope][wname] = wout
    # histograms
    for chain in CH + ["all"]:
        out["histogram"][chain] = {}
        for wname, days in WINDOWS.items():
            cutoff = now - days * 86400 if days else 0
            dl = [r["delay_seconds"] for r in rows if (chain == "all" or r["chain"] == chain) and r["executed"]
                  and r["delay_seconds"] is not None and r["exec_ts"] and r["exec_ts"] >= cutoff]
            out["histogram"][chain][wname] = histogram(dl)
    # weekly executions
    for chain in CH:
        by_week = defaultdict(lambda: defaultdict(int))
        for r in rows:
            if r["chain"] == chain and r["executed"] and r["exec_ts"]:
                by_week[week_start(r["exec_ts"])][r["function"] or "?"] += 1
        weeks = sorted(by_week)[-60:]
        out["weekly"][chain] = [{"week": w, "total": sum(by_week[w].values()), "by_function": dict(by_week[w])}
                                for w in weeks]
    # signers
    sub_by = {(r["chain"], r["tx_id"]): r["sub_ts"] for r in rows}
    for chain in CH:
        out["signers"][chain] = {}
        for wname, days in WINDOWS.items():
            cutoff = now - days * 86400 if days else 0
            agg = defaultdict(lambda: {"submits": 0, "confirms": 0, "revokes": 0, "lat": []})
            q = "SELECT tx_id, signer, kind, ts FROM confirmations WHERE chain=? AND signer!=''"
            for e in conn.execute(q, (chain,)):
                ts = parse_iso(e["ts"])
                if ts < cutoff:
                    continue
                a = agg[e["signer"]]
                if e["kind"] == "submit":
                    a["submits"] += 1
                elif e["kind"] == "confirm":
                    a["confirms"] += 1
                    st = sub_by.get((chain, e["tx_id"]))
                    if st and ts >= st:
                        a["lat"].append(ts - st)
                elif e["kind"] == "revoke":
                    a["revokes"] += 1
            lst = []
            for s, a in agg.items():
                lat = sorted(a["lat"])
                lst.append({"signer": s, "submits": a["submits"], "confirms": a["confirms"], "revokes": a["revokes"],
                            "avg_confirm_latency": (sum(lat) / len(lat)) if lat else None,
                            "median_confirm_latency": percentile(lat, 50)})
            lst.sort(key=lambda x: -(x["submits"] + x["confirms"]))
            out["signers"][chain][wname] = lst
    # balances of open ones
    for chain in CH:
        pend = [r for r in rows if r["chain"] == chain and r["status"] == "pending" and r["function"] in BALANCE_FUNCS]
        out["pending_usdt"][chain] = {
            "n": len(pend), "total_usdt": sum(r["balance_now"] or 0 for r in pend),
            "addblacklist_usdt": sum(r["balance_now"] or 0 for r in pend if r["function"] == "addBlackList")}
    # coverage
    for chain in CH:
        cr = [r for r in rows if r["chain"] == chain and r["destination"]]
        subs = [r["sub_ts"] for r in cr if r["sub_ts"]]
        out["coverage"][chain] = {
            "proposals": len(cr), "min_id": min((r["tx_id"] for r in cr), default=None),
            "max_id": max((r["tx_id"] for r in cr), default=None),
            "first_submitted": iso(min(subs)) if subs else None, "last_submitted": iso(max(subs)) if subs else None,
            "executed": sum(1 for r in cr if r["executed"]),
            "freezes": sum(1 for r in cr if r["executed"] and r["function"] in FREEZE_FUNCS),
            "no_public": sum(1 for r in cr if r["executed"] and r["function"] in FREEZE_FUNCS and r.get("public_proposal") == 0),
            "without_submit_time": sum(1 for r in cr if not r["sub_ts"]),
        }
    out["others"] = compute_others(conn, now)
    return out


def compute_others(conn, now):
    """Executed freezes only (USDC ETH, Solana USDC/USDT); delay N/A."""
    from .db import meta_get
    res = {}
    for src, label in SOURCE_LABELS.items():
        rows = [dict(r) for r in conn.execute("SELECT * FROM freezes WHERE source=? ORDER BY ts", (src,))]
        for r in rows:
            r["t"] = parse_iso(r["ts"])
        fz = [r for r in rows if r["action"] == "freeze"]
        uf = [r for r in rows if r["action"] == "unfreeze"]
        by_week = defaultdict(lambda: {"freeze": 0, "unfreeze": 0})
        for r in rows:
            by_week[week_start(r["t"])][r["action"]] += 1
        known = [r["amount"] for r in fz if r["amount"] is not None]
        if src == "usdc_eth":
            cov = {"from": "full history (from the first Blacklisted event)", "complete": True}
        else:
            oldest = meta_get(conn, f"{src}_oldest_ts")
            done = meta_get(conn, f"{src}_complete") == "1"
            cov = {"from": ("full history" if done else (iso(int(oldest)) if oldest else None)), "complete": done}
        res[src] = {
            "label": label, "delay": "N/A", "freezes": len(fz), "unfreezes": len(uf),
            "freezes_7d": sum(1 for r in fz if r["t"] >= now - 7 * 86400),
            "freezes_30d": sum(1 for r in fz if r["t"] >= now - 30 * 86400),
            "first": rows[0]["ts"] if rows else None, "last": rows[-1]["ts"] if rows else None,
            "distinct_subjects": len({r["subject"] for r in fz}),
            "amount_known_n": len(known), "amount_sum": sum(known),
            "amount_kind": "at_tx" if src.startswith("sol") else "current",
            "weekly": [{"week": w, **by_week[w]} for w in sorted(by_week)[-60:]],
            "coverage": cov,
        }
    return res


def write_stats(conn, now=None):
    s = compute(conn, now)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from .render import write_if_changed
    write_if_changed(DATA_DIR / "stats.json", json.dumps(s, ensure_ascii=False, indent=1))
    return s
