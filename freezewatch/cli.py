"""CLI: python -m freezewatch <backfill|update|stats|render|run>."""
import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from . import (alerts, analytics, crosschain, deadline, exposure, flight, trongrid, txinfo, eth, evmmsw, health, inflows, labels, others, outflows, safe, signals,
               status, tokenevents, tron)
from .constants import BALANCE_FUNCS
from .db import connect, meta_get, meta_set, update_proposal
from .derive import derive_all, rebuild_blacklist
from .render import render_all
from .stats import write_stats
from .util import env, iso, now_ts

log = logging.getLogger("freezewatch")
CHAINS = {"eth": eth, "tron": tron}
AUTH_CHANGES = []


def parse_since(s):
    if not s:
        return None
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def stale_days():
    return int(env("STALE_DAYS", "7"))


from .registry import EVENT_CHAINS, MSW_CHAINS, NON_EVM, SAFE_CHAINS  # noqa: E402

BASE = ["eth", "tron", "usdc_eth", "sol_usdc", "sol_usdt"]
GROUPS = {"all": BASE + list(MSW_CHAINS) + list(SAFE_CHAINS) + list(EVENT_CHAINS) + list(NON_EVM), "usdc": ["usdc_eth"],
          "usdt0": list(SAFE_CHAINS) + list(EVENT_CHAINS), "events": list(EVENT_CHAINS),
          "sol": ["sol_usdc", "sol_usdt"], "msw": list(MSW_CHAINS), "safe": list(SAFE_CHAINS),
          "core": BASE}
CHOICES = sorted(set(GROUPS["all"]) | set(GROUPS))


def chains_of(arg):
    return GROUPS.get(arg, [arg])


def refresh_balances(conn):
    """Target USDT balance for open addBlackList/destroyBlackFunds (stale - only if none yet)."""
    q = ("SELECT chain,tx_id,target,status,balance_now,balance_at_detect FROM proposals "
         "WHERE status IN ('pending','stale') AND target IS NOT NULL AND function IN (%s)" % ",".join("?" * len(BALANCE_FUNCS)))
    for r in conn.execute(q, BALANCE_FUNCS).fetchall():
        if r["status"] == "stale" and r["balance_now"] is not None:
            continue
        try:
            if r["chain"] in MSW_CHAINS:
                bal = evmmsw.balance_of(r["chain"], r["target"])
            elif r["chain"] in SAFE_CHAINS or r["chain"] in EVENT_CHAINS:
                bal = crosschain.balance_on(r["chain"], r["target"])
            elif r["chain"] in CHAINS:
                bal = CHAINS[r["chain"]].balance_of(r["target"])
            else:
                continue
        except Exception as e:  # noqa: BLE001
            log.warning("balance %s %s: %s", r["chain"], r["target"], e)
            continue
        vals = {}
        if bal != r["balance_now"]:
            vals.update(balance_now=bal, balance_checked_at=iso(now_ts()))
        if r["balance_at_detect"] is None:
            vals["balance_at_detect"] = bal
        update_proposal(conn, r["chain"], r["tx_id"], **vals)
    conn.commit()


def _row_count(conn, chain):
    q = ("SELECT (SELECT COUNT(*) FROM proposals WHERE chain=?1) + (SELECT COUNT(*) FROM token_events WHERE chain=?1) "
         "+ (SELECT COUNT(*) FROM freezes WHERE source=?1)")
    return conn.execute(q, (chain,)).fetchone()[0]


# chains whose long history is optional work: in `run` the core pass only reads what is new, the history pass back-fills
# within what is left of the run deadline
HISTORY_CHAINS = ("sol_usdc", "sol_usdt", "xlayer", "oeth")


def do_sync(conn, chain, since, budget, phase="all"):
    """Sync a single chain; a failure does not stop the others. Every run is recorded in source_health.
    phase: "all" (backfill command), "core" (new data only for HISTORY_CHAINS) or "history" (the back-fill part only)."""
    before = _row_count(conn, chain)
    soft = []
    ok = _do_sync(conn, chain, since, budget, soft, phase)
    if phase == "history" and ok is True and not soft:
        return True   # the core pass already recorded this source; the back-fill only reports problems
    try:
        status.record(conn, chain, ok is True, error=ok if ok is not True else None,
                      rows_added=max(0, _row_count(conn, chain) - before), soft_error="; ".join(soft) or None)
        conn.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("health record %s: %s", chain, str(e)[:80])
    return ok is True


def _do_sync(conn, chain, since, budget, soft, phase="all"):
    """-> True or the error text."""
    hist = None if phase == "all" else (0 if phase == "core" else max(1, int(budget)))   # back-fill seconds for HISTORY_CHAINS
    try:
        if chain == "eth":
            eth.sync(conn, since, stale_days())
        elif chain == "tron":
            tron.sync(conn, since, stale_days(), int(deadline.cap(budget)) if deadline.started() else budget)
        elif chain == "usdc_eth":
            others.sync_usdc_eth(conn, since)
        elif chain in MSW_CHAINS:
            evmmsw.sync_chain(conn, chain, stale_days())
        elif chain in SAFE_CHAINS:
            if phase != "history":   # the history pass only continues the token-event scan
                safe.sync_chain(conn, chain, stale_days(), full=since is not None)
                conn.commit()
            try:  # token events are the source of truth; a failure here must not undo the Safe data
                tokenevents.sync_chain(conn, chain, stale_days(), backfill_sec=hist)
            except Exception as e:  # noqa: BLE001
                log.warning("%s: token events failed: %s", chain, str(e)[:100])
                soft.append("token events: " + str(e))
        elif chain in EVENT_CHAINS:
            tokenevents.sync_chain(conn, chain, stale_days(), backfill_sec=hist)
        elif chain in NON_EVM:
            import importlib
            importlib.import_module(f".{chain}", __package__).sync(conn, stale_days())
        else:
            if hist is None or hist > 0 or phase == "core":
                AUTH_CHANGES.extend(others.sync_solana(conn, chain, since, hist if hist else budget, history=hist != 0))
        meta_set(conn, f"{chain}_last_ok", iso(now_ts()))
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        conn.commit()
        log.error("%s: sync failed: %s", chain.upper(), e, exc_info=log.isEnabledFor(logging.DEBUG))
        return str(e) or type(e).__name__


def cmd_backfill(args):
    conn = connect()
    since = parse_since(args.since)
    ok = True
    for c in chains_of(args.chain):
        ok &= do_sync(conn, c, since, args.budget)
    rebuild_blacklist(conn)
    conn.commit()
    return 0 if ok else 1


class _Run:
    abandoned = False   # an optional step hung and was abandoned: its thread may still touch the old connection


def _optional(conn_box, name, fn, own):
    """One optional step: budget = min(own, time left - reserve); runs in a guard thread so a hung socket cannot hold the run."""
    st = deadline.step(name, own)
    if _Run.abandoned or st.skipped:
        deadline.note(name, 0.0, "skipped")
        log.info("step %s skipped (run deadline: %.0fs of optional time left)", name, deadline.optional_remaining())
        return
    with st:
        done = deadline.run_guarded(lambda: fn(conn_box[0], st.budget), name, st.budget)
    if not done:
        _Run.abandoned = True
        conn_box[0] = connect()   # the abandoned thread may still hold the old connection


def cmd_update(args, conn=None):
    own_conn = conn is None
    conn = conn or connect()
    deadline.start()
    trongrid.reset_usage()
    _Run.abandoned = False
    box = [conn]
    before = alerts.snapshot(conn)
    before_pairs = crosschain.pairs(conn)
    max_fr = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM freezes").fetchone()[0]
    max_inflow = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM inflows_after_freeze").fetchone()[0]
    # 1. core: new proposals / freezes of every chain (always runs; bounded only by the per-call HTTP timeouts)
    results = []
    # TRON first: its pending proposals are the time-critical ones (FREEZE INCOMING alerts)
    for c in sorted(chains_of(getattr(args, "chain", "all")), key=lambda c: c != "tron"):
        with deadline.step(c, optional=False):
            results.append(do_sync(conn, c, None, args.budget, phase="core"))
    ok = any(results)  # one failing source must not fail the whole run (rate limits, missing optional keys)
    with deadline.step("balances-open", optional=False):
        refresh_balances(conn)
    rebuild_blacklist(conn)
    conn.commit()
    try:
        health.check(conn)
    except Exception as e:  # noqa: BLE001
        log.warning("health check failed: %s", str(e)[:80])
    # who can still move the funds of pending targets (allowances, EIP-7702): known before the FREEZE INCOMING alert is built
    with deadline.step("exposure", 25, optional=True) as st_exp:
        if not st_exp.skipped:
            try:
                exposure.refresh(conn, min(25, st_exp.budget))
            except Exception as e:  # noqa: BLE001
                log.warning("exposure failed: %s", str(e)[:80])
    # alerts for new / executed proposals go out NOW, before any slow optional step (inflow / cross-chain alerts follow at the end;
    # the `alerts` table dedupes, so nothing is sent twice)
    with deadline.step("alerts-early", optional=False):
        try:
            alerts.run_alerts(conn, before, max_fr, before_pairs, AUTH_CHANGES, None)
        except Exception as e:  # noqa: BLE001
            log.warning("early alerts failed: %s", str(e)[:80])
    _log_detect_lag(conn)
    # 2. optional work, each step capped by what is left of the run deadline (reserve kept for render + publish)
    _optional(box, "signals", lambda c, b: (signals.detect(c, budget_sec=min(35, b)), signals.resolve(c, budget_sec=min(20, b))), 45)
    budget = int(env("ANALYTICS_BUDGET_SEC", "120"))
    for c in HISTORY_CHAINS:
        if c in chains_of(getattr(args, "chain", "all")):
            _optional(box, f"history:{c}", lambda cn, b, c=c: do_sync(cn, c, None, b, phase="history"), min(budget, 90))
    for name, fn in (("destroy-amounts", analytics.enrich_destroy_amounts), ("balances", analytics.refresh_balances),
                     ("inflows", inflows.refresh), ("outflows", outflows.refresh), ("txinfo", txinfo.resolve),
                     ("labels", labels.refresh), ("flight-watch", _flight_step)):
        _optional(box, name, lambda cn, b, fn=fn: fn(cn, b), budget)
    conn = box[0]
    with deadline.step("alerts", optional=False):
        n = alerts.run_alerts(conn, before, max_fr, before_pairs, AUTH_CHANGES, max_inflow)
    if n:
        log.info("alerts sent: %s", n)
    _save_timings(conn)
    if own_conn:
        log.info(deadline.summary())
    return 0 if ok else 1


def _flight_step(conn, budget):
    """Hop-ahead watch: refresh the watched set from recent outflows and poll those addresses for moves."""
    from . import labels as _labels
    lab = _labels.load_map(conn)
    flight.refresh_watch(conn, lab)
    flight.scan(conn, lab, budget_sec=budget)


def _log_detect_lag(conn):
    d = status.detect_lag(conn)
    if d:
        log.info("detected after submission (7 d): median %ss, p90 %ss over %s proposals (TRON)", d["median_s"], d["p90_s"], d["n"])


def _save_timings(conn):
    import json
    try:
        meta_set(conn, "run_timings", json.dumps({"at": iso(now_ts()), "run_s": round(deadline.elapsed(), 1),
                                                  "deadline_s": deadline._S["total"], "steps": deadline.timings(),
                                                  "tron_keys": trongrid.usage_report()}))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("timings not saved: %s", str(e)[:60])


def cmd_alert_test(args):
    msgs = alerts.sample_messages()
    for m in msgs:
        print(m)
        print("-" * 60)
    if args.send:
        if not alerts.configured():
            print("TG_BOT_TOKEN / TG_CHAT_ID not set; nothing sent")
            return 1
        alerts.send_message(msgs[0])
        print("sent 1 test message")
    return 0


def cmd_stats(args, conn=None):
    conn = conn or connect()
    write_stats(conn)
    return 0


def cmd_render(args, conn=None):
    conn = conn or connect()
    s = write_stats(conn)
    render_all(conn, s, stale_days())
    return 0


def cmd_run(args):
    conn = connect()
    # if there is no history yet - first run = backfill (time-budgeted; continues on the next run)
    rc = cmd_update(args, conn)
    try:
        with deadline.step("render", optional=False):
            cmd_render(args, connect() if _Run.abandoned else conn)
    finally:
        log.info(deadline.summary())
        if trongrid.usage_report():
            log.info(trongrid.usage_report())
        deadline.stop()
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="freezewatch", description="USDT Freeze Alert: Tether multisig freeze monitor")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="full history (resumable)")
    b.add_argument("--chain", choices=CHOICES, default="all")
    b.add_argument("--since", help="YYYY-MM-DD (UTC)")
    b.add_argument("--budget", type=int, default=None, help="time budget in seconds per source")
    u = sub.add_parser("update", help="incremental update")
    u.add_argument("--chain", choices=CHOICES, default="all")
    u.add_argument("--budget", type=int, default=int(env("FREEZEWATCH_BUDGET_SEC", "1200")))
    at = sub.add_parser("alert-test", help="print sample Telegram alerts (no sending unless --send)")
    at.add_argument("--send", action="store_true", help="send ONE test message (manual only)")
    sub.add_parser("stats", help="recompute data/stats.json")
    sub.add_parser("render", help="stats + site/index.html + badges")
    r = sub.add_parser("run", help="update + stats + render")
    r.add_argument("--chain", choices=CHOICES, default="all")
    r.add_argument("--budget", type=int, default=int(env("FREEZEWATCH_BUDGET_SEC", "1200")))
    r.add_argument("--loop", action="store_true", help="daemon: repeat")
    r.add_argument("--interval", type=int, default=60, help="pause between cycles, seconds")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    from .util import key_report
    summary, warns = key_report()
    if summary:
        log.info("API keys: %s", summary)
    for w in warns:
        log.warning(w)
    fn = {"backfill": cmd_backfill, "update": cmd_update, "stats": cmd_stats, "render": cmd_render, "run": cmd_run,
          "alert-test": cmd_alert_test}[args.cmd]
    if args.cmd == "run" and args.loop:
        while True:
            try:
                fn(args)
            except Exception:  # noqa: BLE001
                log.exception("loop iteration failed")
            time.sleep(args.interval)
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
