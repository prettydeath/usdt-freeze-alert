"""Daily Telegram report at 09:00 Europe/Kyiv about the PREVIOUS Kyiv calendar day (00:00-24:00 Kyiv).

Triggered inside the normal run: once local time is >= 09:00 and the report for yesterday has not been sent (meta key
`daily_report:<YYYY-MM-DD>`), it is built and sent once - a missed run only delays it, it is never sent twice and never for a day
older than yesterday. Zero-activity days get a short "no freeze activity" message. `collect()` reads the database,
`fmt_daily()` turns the numbers into the Radar-style HTML message (pure, tested)."""
import json
import logging
import statistics

from . import analytics, kyiv, outflows
from .constants import DESTROY_FUNCS, FREEZE_FUNCS, SUPPLY_FUNCS, UNFREEZE_FUNCS
from .db import meta_get, meta_set
from .util import iso, now_ts

log = logging.getLogger("freezewatch.daily")
SEND_FROM_HOUR = 9
FLIGHT_MIN_USD = 10_000.0


def _in(col, vals):
    return f"{col} IN ({','.join('?' * len(vals))})"


def collect(conn, day):
    """All numbers of the report for the Kyiv calendar day `day` ('YYYY-MM-DD')."""
    s, e = kyiv.day_bounds(day)
    si, ei = iso(s), iso(e)
    d = {"day": day, "start": s, "end": e}
    fr = FREEZE_FUNCS
    d["proposals"] = {r[0]: r[1] for r in conn.execute(
        f"SELECT chain, COUNT(*) FROM proposals WHERE {_in('function', fr)} AND public_proposal=1 AND submitted_at>=? AND submitted_at<? "
        "GROUP BY chain", (*fr, si, ei))}
    usd = analytics.frozen_usd_map(conn)
    ex, usd_by_chain, top = {}, {}, []
    for r in conn.execute(f"SELECT chain, target, executed_at, delay_seconds, public_proposal FROM proposals WHERE executed=1 AND "
                          f"{_in('function', fr)} AND executed_at>=? AND executed_at<?", (*fr, si, ei)):
        ex[r["chain"]] = ex.get(r["chain"], 0) + 1
    for r in conn.execute("SELECT source, COUNT(*) FROM freezes WHERE action='freeze' AND ts>=? AND ts<? GROUP BY source", (si, ei)):
        ex[r[0]] = ex.get(r[0], 0) + r[1]
    for (chain, addr, f_iso), v in usd.items():
        if si <= f_iso < ei and v and v >= 1:
            usd_by_chain[chain] = usd_by_chain.get(chain, 0.0) + v
            top.append((v, chain, addr))
    d["executed"] = ex
    d["frozen_usd"] = {c: round(v, 2) for c, v in usd_by_chain.items()}
    d["top"] = [{"usd": round(v, 2), "chain": c, "address": a} for v, c, a in sorted(top, reverse=True)[:3]]
    uf = tuple(UNFREEZE_FUNCS)
    d["unfreezes"] = sum(r[0] for r in conn.execute(f"SELECT COUNT(*) FROM proposals WHERE executed=1 AND {_in('function', uf)} AND "
                                                    "executed_at>=? AND executed_at<?", (*uf, si, ei))) + \
        conn.execute("SELECT COUNT(*) FROM freezes WHERE action='unfreeze' AND ts>=? AND ts<?", (si, ei)).fetchone()[0]
    de = DESTROY_FUNCS
    b = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(amount),0) FROM proposals WHERE executed=1 AND {_in('function', de)} AND "
                     "executed_at>=? AND executed_at<?", (*de, si, ei)).fetchone()
    d["burned"] = {"n": b[0], "usd": round(b[1], 2)}
    sup = {"issued": 0.0, "redeemed": 0.0, "ni": 0, "nr": 0}
    for r in conn.execute(f"SELECT function, amount, raw_data FROM proposals WHERE executed=1 AND {_in('function', SUPPLY_FUNCS)} "
                          "AND executed_at>=? AND executed_at<?", (*SUPPLY_FUNCS, si, ei)):
        amt = r["amount"] if r["amount"] is not None else (analytics.mint_amount(r["raw_data"]) if r["function"] == "mint" else None)
        k = "i" if r["function"] in ("issue", "mint") else "r"
        sup["n" + k] += 1
        sup["issued" if k == "i" else "redeemed"] += amt or 0.0
    d["supply"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sup.items()}
    delays = [(r["delay_seconds"], r["chain"]) for r in conn.execute(
        f"SELECT chain, delay_seconds FROM proposals WHERE executed=1 AND public_proposal=1 AND delay_seconds IS NOT NULL AND {_in('function', fr)} "
        "AND executed_at>=? AND executed_at<?", (*fr, si, ei))]
    d["delay"] = {c: {"median": int(statistics.median(x for x, ch in delays if ch == c)), "n": sum(1 for _x, ch in delays if ch == c)}
                  for c in ("eth", "tron") if any(ch == c for _x, ch in delays)}
    d["fastest"], d["slowest"] = (min(delays), max(delays)) if delays else (None, None)
    d["escaped"] = _escaped(conn, si, ei, usd)
    mv = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM watch_moves WHERE ts>=? AND ts<? AND amount>=? AND own_proposal='none'", (si, ei, FLIGHT_MIN_USD)).fetchone()
    d["flights"] = {"n": mv[0], "usd": round(mv[1], 2)}
    opened = conn.execute("SELECT COUNT(*) FROM signals WHERE first_seen>=? AND first_seen<?", (si, ei)).fetchone()[0]
    resolved = 0
    for r in conn.execute("SELECT resolutions FROM signals WHERE resolutions IS NOT NULL AND resolutions<>'[]'"):
        resolved += sum(1 for x in json.loads(r[0]) if si <= x.get("blocked_at", "") < ei)
    d["signals"] = {"opened": opened, "resolved": resolved}
    d["ofac"] = _ofac(conn, si, ei)
    p = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(balance_now),0) FROM proposals WHERE status='pending' AND {_in('function', fr)}", fr).fetchone()
    d["pending"] = {"n": p[0], "usd": round(p[1], 2)}
    d["activity"] = bool(sum(d["proposals"].values()) or sum(ex.values()) or d["unfreezes"] or d["burned"]["n"] or sup["ni"] or sup["nr"])
    return d


def _escaped(conn, si, ei, usd):
    """Escaped funds of the freezes executed in the day: total, front-run count, and how many of those freezes were scanned yet."""
    rows = [r for r in outflows.classify(conn, si) if si <= r["freeze_ts"] < ei]
    tot = outflows.freeze_totals(rows)
    freezes = len({(c, a) for (c, a, f) in usd if si <= f < ei})
    scanned = conn.execute("SELECT COUNT(*) FROM outflow_done WHERE freeze_ts>=? AND freeze_ts<?", (si, ei)).fetchone()[0]
    front = 0
    for k, v in tot.items():
        rem = usd.get(k)
        if v["window"] > 0 and rem is not None and v["window"] / (v["window"] + rem) >= 0.5:
            front += 1
    return {"usd": round(sum(v["escaped"] for v in tot.values()), 2), "front_run": front, "scanned": scanned, "freezes": freezes}


def _ofac(conn, si, ei):
    from .labels import NETWORK_OF, key_of, load_map
    from .reasons import is_ofac
    lab = load_map(conn)
    n = 0
    for r in conn.execute(f"SELECT chain, target FROM proposals WHERE executed=1 AND {_in('function', FREEZE_FUNCS)} AND target IS NOT NULL "
                          "AND executed_at>=? AND executed_at<?", (*FREEZE_FUNCS, si, ei)):
        net = NETWORK_OF.get(r["chain"])
        lb = lab.get((net, key_of(net, r["target"]))) if net else None
        n += int(bool(lb) and is_ofac(lb))
    return n


# ---------------- message ----------------
def fmt_daily(d):
    """Radar-style HTML message for the numbers of collect()."""
    from .alerts import DASHBOARD, NL, e, human, num, short, site_link
    lines = [f"📊 <b>DAILY REPORT</b> · {e(d['day'])}"]
    pend = d["pending"]
    pending = f"⏳ Pending now: <b>{pend['n']}</b> freeze proposal{'s' if pend['n'] != 1 else ''}" + (f" · ${num(pend['usd'])} at risk" if pend["usd"] else "")
    if not d["activity"]:
        lines += ["😴 no freeze activity yesterday", pending]
    else:
        chains = lambda m: ", ".join(f"{short(c)} {n}" for c, n in sorted(m.items(), key=lambda kv: -kv[1])) or "none"   # noqa: E731
        lines.append(f"🚨 New freeze proposals: <b>{sum(d['proposals'].values())}</b> ({chains(d['proposals'])})")
        ex = sum(d["executed"].values())
        usd_by = ", ".join(f"{short(c)} ${num(v)}" for c, v in sorted(d["frozen_usd"].items(), key=lambda kv: -kv[1])[:6])
        lines.append(f"🧊 Executed freezes: <b>{ex}</b> ({chains(d['executed'])})" + (f"\n    💰 frozen where known: {usd_by}" if usd_by else ""))
        lines.append(f"🟢 Unfreezes: <b>{d['unfreezes']}</b> · 🔥 Burned: <b>{d['burned']['n']}</b>" + (f" (${num(d['burned']['usd'])})" if d["burned"]["usd"] else ""))
        sp = d["supply"]
        if sp["ni"] or sp["nr"]:
            lines.append(f"🏦 Supply (not freezes): issued ${num(sp['issued'])} ({sp['ni']}) · redeemed ${num(sp['redeemed'])} ({sp['nr']})")
        dl = [f"{short(c)} {human(v['median'], approx=False)} (n={v['n']})" for c, v in d["delay"].items()]
        if dl:
            lines.append("⏱ Median proposal → execution: " + " · ".join(dl))
        if d["fastest"]:
            lines.append(f"    fastest {human(d['fastest'][0], approx=False)} ({short(d['fastest'][1])}) · slowest {human(d['slowest'][0], approx=False)} ({short(d['slowest'][1])})")
        es = d["escaped"]
        lines.append(f"💸 Moved out in the 72 h before these freezes: <b>${num(es['usd'])}</b> · front-run: <b>{es['front_run']}</b>"
                     + (f" <i>({es['scanned']} of {es['freezes']} scanned)</i>" if es["scanned"] < es["freezes"] else ""))
        fl, sg = d["flights"], d["signals"]
        from .constants import FLIGHT_PUBLIC
        lines.append((f"🏃 Pre-proposal flights (≥ $10k): <b>{fl['n']}</b>" + (f" · ${num(fl['usd'])}" if fl["n"] else "") + " · " if FLIGHT_PUBLIC else "")
                     + f"🔗 cross-chain signals: {sg['opened']} opened, {sg['resolved']} resolved · ⚖ OFAC-listed freezes: <b>{d['ofac']}</b>")
        if d["top"]:
            lines.append("🏆 Top freezes by amount:")
            for t in d["top"]:
                lines.append(f"  ${num(t['usd'])} · {e(short(t['chain']))} · <code>{e(t['address'])}</code> · {site_link(t['address'])}")
        lines.append(pending)
    lines.append(f'<a href="{DASHBOARD}#analytics">dashboard</a> · glossary: Analytics tab, bottom')
    lines.append("#daily #report")
    return NL.join(lines)


# ---------------- trigger ----------------
def due_day(conn, now=None):
    """The Kyiv day to report now, or None: only yesterday, only from 09:00 Kyiv, only if not sent yet."""
    now = now or now_ts()
    if kyiv.local_dt(now).hour < SEND_FROM_HOUR:
        return None
    day = kyiv.yesterday(now)
    return None if meta_get(conn, f"daily_report:{day}") else day


def maybe_send(conn, now=None, send=None, configured=None):
    """Send the report when due. -> the day sent or None. A failed send is retried on the next run (nothing is marked)."""
    from . import alerts
    now = now or now_ts()
    day = due_day(conn, now)
    if not day:
        return None
    if not (configured if configured is not None else alerts.configured()):
        return None
    text = fmt_daily(collect(conn, day))
    try:
        (send or alerts.send_message)(text)
    except Exception as ex:  # noqa: BLE001
        log.warning("daily report not sent: %s", str(ex)[:80])
        return None
    meta_set(conn, f"daily_report:{day}", iso(now))
    meta_set(conn, "daily_report_last", day)
    conn.commit()
    log.info("daily report for %s sent", day)
    return day


def sample_report():
    """Example message with made-up numbers (alert-test)."""
    return fmt_daily({
        "day": "2026-11-23", "activity": True, "proposals": {"tron": 9, "eth": 3, "arb1": 1}, "executed": {"tron": 7, "eth": 2},
        "frozen_usd": {"tron": 412_300.5, "eth": 1_250_000.0}, "unfreezes": 1, "burned": {"n": 1, "usd": 88_000.0},
        "supply": {"issued": 250_000_000.0, "redeemed": 0.0, "ni": 1, "nr": 0},
        "delay": {"eth": {"median": 5400, "n": 2}, "tron": {"median": 6564, "n": 7}},
        "fastest": (610, "tron"), "slowest": (31_000, "eth"), "escaped": {"usd": 52_000.0, "front_run": 1, "scanned": 9, "freezes": 9},
        "flights": {"n": 2, "usd": 180_000.0}, "signals": {"opened": 1, "resolved": 1}, "ofac": 1,
        "top": [{"usd": 1_250_000.0, "chain": "eth", "address": "0xe3d1143fccc957186b013fec87c492c31fb15e14"},
                {"usd": 355_678.98, "chain": "tron", "address": "TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd"}],
        "pending": {"n": 4, "usd": 91_000.0}})
