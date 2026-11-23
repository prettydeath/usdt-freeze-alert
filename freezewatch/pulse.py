"""Tether pulse: how the multisig signers behave.

signers  per chain and signer: confirmations (also by month), median time from proposal submission to this signer's confirmation,
         and how often this signer gave the LAST signature of an executed proposal
speed    monthly median delay (proposal submitted -> executed) per chain, public proposals only
queue    number of open freeze/unfreeze/destroy proposals at the end of each day (never-executed ones count until they are stale)
The execution heatmap (hour of day x weekday) is computed in the browser from the compact `ex` list, so it follows the chain filter."""
import statistics
import time

from .constants import DESTROY_FUNCS, FREEZE_FUNCS, UNFREEZE_FUNCS
from .util import parse_iso

TRACKED = tuple(FREEZE_FUNCS) + tuple(UNFREEZE_FUNCS) + tuple(DESTROY_FUNCS)
MIN_EVENTS = 20


def month_of(ts):
    return time.strftime("%Y-%m", time.gmtime(ts))


def months_back(now, n):
    y, m = time.gmtime(now).tm_year, time.gmtime(now).tm_mon
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def signers(conn, now=None, months=12, per_chain=8):
    now = now or time.time()
    keep = set(months_back(now, months))
    sub = {}
    exe = {}
    for r in conn.execute("SELECT chain, tx_id, submitted_at, executed, executed_at FROM proposals"):
        if r["submitted_at"]:
            sub[(r["chain"], r["tx_id"])] = parse_iso(r["submitted_at"])
        if r["executed"] and r["executed_at"]:
            exe[(r["chain"], r["tx_id"])] = parse_iso(r["executed_at"])
    ev = {}
    for r in conn.execute("SELECT chain, tx_id, signer, kind, ts FROM confirmations WHERE kind IN ('submit','confirm') AND signer<>''"):
        ev.setdefault((r["chain"], r["tx_id"]), []).append((parse_iso(r["ts"]), r["signer"], r["kind"]))
    stat = {}
    for key, lst in ev.items():
        chain = key[0]
        t0 = sub.get(key)
        last = None
        if key in exe:
            ok = [x for x in lst if x[0] <= exe[key] + 120]
            if len({x[1] for x in ok}) >= 2:
                mx = max(x[0] for x in ok)
                tops = {x[1] for x in ok if x[0] == mx}
                last = tops.pop() if len(tops) == 1 else None
        seen = set()
        for ts, signer, kind in lst:
            d = stat.setdefault(chain, {}).setdefault(signer, {"n": 0, "months": {}, "tc": [], "part": 0, "last": 0})
            d["n"] += 1
            m = month_of(ts)
            if m in keep:
                d["months"][m] = d["months"].get(m, 0) + 1
            if kind == "confirm" and t0 is not None and ts >= t0:
                d["tc"].append(ts - t0)
            if signer not in seen and key in exe:
                d["part"] += 1
                seen.add(signer)
        if last and last in stat.get(chain, {}):
            stat[chain][last]["last"] += 1
    out = {}
    for chain, d in stat.items():
        if sum(v["n"] for v in d.values()) < MIN_EVENTS:
            continue
        rows = []
        for s, v in sorted(d.items(), key=lambda kv: -kv[1]["n"])[:per_chain]:
            rows.append({"signer": s, "n": v["n"], "months": v["months"],
                         "median_confirm_s": int(statistics.median(v["tc"])) if v["tc"] else None,
                         "last_n": v["last"], "last_share": round(v["last"] / v["part"], 3) if v["part"] else None})
        out[chain] = rows
    return out


def speed(conn, now=None, months=18, min_samples=8):
    now = now or time.time()
    keep = set(months_back(now, months))
    qm = ",".join("?" * len(TRACKED))
    by = {}
    for r in conn.execute(f"SELECT chain, executed_at, delay_seconds FROM proposals WHERE executed=1 AND executed_at IS NOT NULL "
                          f"AND delay_seconds IS NOT NULL AND public_proposal=1 AND function IN ({qm})", TRACKED):
        m = month_of(parse_iso(r["executed_at"]))
        if m in keep:
            by.setdefault(r["chain"], {}).setdefault(m, []).append(r["delay_seconds"])
    out = {}
    for chain, d in by.items():
        if sum(len(v) for v in d.values()) >= min_samples:
            out[chain] = {m: int(statistics.median(v)) for m, v in sorted(d.items())}
    return out


def queue(conn, now=None, days=120, stale_days=7):
    now = now or time.time()
    qm = ",".join("?" * len(TRACKED))
    rows = []
    for r in conn.execute(f"SELECT submitted_at, executed, executed_at FROM proposals WHERE submitted_at IS NOT NULL AND function IN ({qm})",
                          TRACKED):
        s = parse_iso(r["submitted_at"])
        e = parse_iso(r["executed_at"]) if (r["executed"] and r["executed_at"]) else None
        rows.append((s, e if e else s + stale_days * 86400))
    day0 = int(now // 86400) * 86400
    out = []
    for i in range(days, -1, -1):
        t = day0 - i * 86400 + 86399 if i else now
        out.append([day0 - i * 86400, sum(1 for s, e in rows if s <= t < e)])
    return out


def compute(conn, now=None):
    return {"signers": signers(conn, now), "speed": speed(conn, now), "queue": queue(conn, now)}
