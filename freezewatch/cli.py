"""CLI: python -m freezewatch <backfill|update|stats|render|run>."""
import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from . import alerts, analytics, crosschain, eth, evmmsw, inflows, others, safe, tokenevents, tron
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


def do_sync(conn, chain, since, budget):
    """Sync a single chain; a failure does not stop the others."""
    try:
        if chain == "eth":
            eth.sync(conn, since, stale_days())
        elif chain == "tron":
            tron.sync(conn, since, stale_days(), budget)
        elif chain == "usdc_eth":
            others.sync_usdc_eth(conn, since)
        elif chain in MSW_CHAINS:
            evmmsw.sync_chain(conn, chain, stale_days())
        elif chain in SAFE_CHAINS:
            safe.sync_chain(conn, chain, stale_days(), full=since is not None)
            conn.commit()
            try:  # token events are the source of truth; a failure here must not undo the Safe data
                tokenevents.sync_chain(conn, chain, stale_days())
            except Exception as e:  # noqa: BLE001
                log.warning("%s: token events failed: %s", chain, str(e)[:100])
        elif chain in EVENT_CHAINS:
            tokenevents.sync_chain(conn, chain, stale_days())
        elif chain in NON_EVM:
            import importlib
            importlib.import_module(f".{chain}", __package__).sync(conn, stale_days())
        else:
            AUTH_CHANGES.extend(others.sync_solana(conn, chain, since, budget))
        meta_set(conn, f"{chain}_last_ok", iso(now_ts()))
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        conn.commit()
        log.error("%s: sync failed: %s", chain.upper(), e, exc_info=log.isEnabledFor(logging.DEBUG))
        return False


def cmd_backfill(args):
    conn = connect()
    since = parse_since(args.since)
    ok = True
    for c in chains_of(args.chain):
        ok &= do_sync(conn, c, since, args.budget)
    rebuild_blacklist(conn)
    conn.commit()
    return 0 if ok else 1


def cmd_update(args, conn=None):
    conn = conn or connect()
    before = alerts.snapshot(conn)
    before_pairs = crosschain.pairs(conn)
    max_fr = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM freezes").fetchone()[0]
    max_inflow = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM inflows_after_freeze").fetchone()[0]
    results = [do_sync(conn, c, None, args.budget) for c in chains_of(getattr(args, "chain", "all"))]
    ok = any(results)  # one failing source must not fail the whole run (rate limits, missing optional keys)
    refresh_balances(conn)
    rebuild_blacklist(conn)
    conn.commit()
    budget = int(env("ANALYTICS_BUDGET_SEC", "120"))
    for step in (analytics.enrich_destroy_amounts, analytics.refresh_balances, inflows.refresh):
        try:
            step(conn, budget)
        except Exception as e:  # noqa: BLE001
            log.warning("analytics step %s failed: %s", step.__name__, str(e)[:100])
    n = alerts.run_alerts(conn, before, max_fr, before_pairs, AUTH_CHANGES, max_inflow)
    if n:
        log.info("alerts sent: %s", n)
    return 0 if ok else 1


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
    cmd_render(args, conn)
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
