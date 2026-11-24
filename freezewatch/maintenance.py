"""Keeping the published database small (GitHub's file limit is 100 MB; the raw DB had reached 98 MB).

- compact_outflows: giant wallets (exchange hot wallets: 10,000+ transfers in 72 h) kept every transfer. Per freeze, transfers that
  were not made between proposal and execution are summed per recipient, only the KEEP_RECIPIENTS biggest recipients stay and the
  rest becomes one "(N other recipients)" row; in-window transfers (the front-run evidence) stay per transfer. Totals do not change.
- shrink_label_checked: the "address already matched against wallet-attribution" markers need no timestamp and no rowid.
- prune_tron_calls: the raw calldata of old TRON submit calls is only needed until the proposal is assembled.
- vacuum_if_needed: rewrite the file when more than a fifth of it is free pages (VACUUM needs a quiet connection).
All steps are idempotent and cheap when there is nothing to do."""
import logging
import os

from .db import DB_PATH
from .util import iso, now_ts

log = logging.getLogger("freezewatch.maintenance")
KEEP_RECIPIENTS = 400
MAX_WINDOW_ROWS = 300
DUST_USD = 1.0
OTHER_PREFIX = "(other recipients"


def _freezes_with_rows(conn, min_rows):
    return conn.execute("SELECT chain, frozen_address, freeze_ts, COUNT(*) n FROM outflows GROUP BY chain, frozen_address, freeze_ts "
                        "HAVING n>?", (min_rows,)).fetchall()


def compact_freeze(conn, chain, addr, fts):
    """Shrink the stored transfers of one freeze (see module doc). -> rows removed."""
    rows = conn.execute("SELECT recipient, amount, ts, tx_hash, log_index, in_window FROM outflows WHERE chain=? AND frozen_address=? "
                        "AND freeze_ts=?", (chain, addr, fts)).fetchall()
    before = len(rows)

    def squash(items, in_window):
        by = {}
        for r in items:
            d = by.setdefault(r["recipient"], {"amount": 0.0, "ts": r["ts"], "tx": r["tx_hash"], "big": 0.0, "n": 0})
            d["amount"] += r["amount"]
            d["n"] += 1
            if r["ts"] > d["ts"]:
                d["ts"] = r["ts"]
            if r["amount"] > d["big"]:
                d["big"], d["tx"] = r["amount"], r["tx_hash"]
        ranked = sorted(by.items(), key=lambda kv: -kv[1]["amount"])
        keep = ranked[:KEEP_RECIPIENTS]
        out = [(rcp, d["amount"], d["ts"], d["tx"], -1 if d["n"] > 1 else 0, in_window) for rcp, d in keep]
        rest = ranked[KEEP_RECIPIENTS:]
        if rest:
            out.append((f"{OTHER_PREFIX}: {len(rest)})", sum(d["amount"] for _r, d in rest), max(d["ts"] for _r, d in rest), "", -2, in_window))
        return out
    inw = [r for r in rows if r["in_window"] and r["amount"] >= DUST_USD]
    rest = [r for r in rows if not r["in_window"] and r["amount"] >= DUST_USD]
    new = ([(r["recipient"], r["amount"], r["ts"], r["tx_hash"], r["log_index"], 1) for r in inw] if len(inw) <= MAX_WINDOW_ROWS
           else squash(inw, 1)) + squash(rest, 0)
    conn.execute("DELETE FROM outflows WHERE chain=? AND frozen_address=? AND freeze_ts=?", (chain, addr, fts))
    conn.executemany("INSERT OR IGNORE INTO outflows(chain,frozen_address,recipient,amount,ts,tx_hash,log_index,in_window,freeze_ts) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", [(chain, addr, rcp, amt, ts, tx, li, inw, fts) for rcp, amt, ts, tx, li, inw in new])
    return before - len(new)


def compact_outflows(conn, min_rows=KEEP_RECIPIENTS + MAX_WINDOW_ROWS // 3 + 50):
    """Compact every freeze that stores more than `min_rows` transfers, and drop dust rows. -> rows removed."""
    removed = conn.execute("DELETE FROM outflows WHERE amount<?", (DUST_USD,)).rowcount
    for r in _freezes_with_rows(conn, min_rows):
        removed += compact_freeze(conn, r["chain"], r["frozen_address"], r["freeze_ts"])
        conn.execute("UPDATE outflow_done SET total_out=(SELECT COALESCE(SUM(amount),0) FROM outflows o WHERE o.chain=outflow_done.chain AND "
                     "o.frozen_address=outflow_done.frozen_address AND o.freeze_ts=outflow_done.freeze_ts), window_out=(SELECT "
                     "COALESCE(SUM(CASE WHEN in_window THEN amount END),0) FROM outflows o WHERE o.chain=outflow_done.chain AND "
                     "o.frozen_address=outflow_done.frozen_address AND o.freeze_ts=outflow_done.freeze_ts) WHERE chain=? AND frozen_address=? "
                     "AND freeze_ts=?", (r["chain"], r["frozen_address"], r["freeze_ts"]))
    conn.commit()
    if removed:
        log.info("maintenance: %s outflow rows compacted", removed)
    return removed


def shrink_label_checked(conn):
    """label_checked(network, key, checked_at) -> label_checked(network, key) WITHOUT ROWID (about a third of the size)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(label_checked)")]
    if "checked_at" not in cols:
        return 0
    n = conn.execute("SELECT COUNT(*) FROM label_checked").fetchone()[0]
    conn.execute("ALTER TABLE label_checked RENAME TO label_checked_old")
    conn.execute("CREATE TABLE label_checked (network TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY (network, key)) WITHOUT ROWID")
    conn.execute("INSERT OR IGNORE INTO label_checked SELECT network, key FROM label_checked_old")
    conn.execute("DROP TABLE label_checked_old")
    conn.commit()
    return n


def prune_label_checked(conn):
    """Markers of addresses that are no longer counterparties of anything we store (their transfers were compacted away) can go."""
    from . import labels
    keep = labels.counterparties(conn)
    n = 0
    for net, in [(r[0],) for r in conn.execute("SELECT DISTINCT network FROM label_checked")]:
        have = keep.get(net, set())
        stale = [(net, r[0]) for r in conn.execute("SELECT key FROM label_checked WHERE network=?", (net,)) if r[0] not in have]
        conn.executemany("DELETE FROM label_checked WHERE network=? AND key=?", stale)
        n += len(stale)
    conn.commit()
    return n


def prune_tron_calls(conn, days=60):
    """Raw calldata of old TRON submit calls: only needed until the proposal row was assembled."""
    cut = (now_ts() - days * 86400) * 1000
    n = conn.execute("UPDATE tron_calls SET data=NULL WHERE data IS NOT NULL AND method='submit' AND ts_ms<? AND tx_hash IN "
                     "(SELECT submit_tx FROM proposals WHERE chain='tron' AND submit_tx IS NOT NULL)", (cut,)).rowcount
    conn.commit()
    return n


def free_ratio(conn):
    pc = conn.execute("PRAGMA page_count").fetchone()[0]
    fl = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return fl / pc if pc else 0.0


def vacuum_if_needed(conn, ratio=0.2):
    if free_ratio(conn) < ratio:
        return False
    conn.commit()
    conn.execute("VACUUM")
    return True


def sizes(path=None):
    p = path or DB_PATH
    return os.path.getsize(p) if os.path.exists(p) else 0


def run(conn, budget_sec=30):
    """All maintenance steps. -> dict for the log. The caller makes sure no other thread uses the connection."""
    import time
    t0 = time.time()
    out = {"before": sizes()}
    out["label_checked"] = shrink_label_checked(conn)
    out["outflows_removed"] = compact_outflows(conn)
    out["label_checked_pruned"] = prune_label_checked(conn)
    out["tron_calls"] = prune_tron_calls(conn)
    if time.time() - t0 < budget_sec:
        out["vacuumed"] = vacuum_if_needed(conn)
    out["after"] = sizes()
    log.info("maintenance: db %.1f -> %.1f MB (%s)", out["before"] / 1e6, out["after"] / 1e6,
             ", ".join(f"{k}={v}" for k, v in out.items() if k not in ("before", "after") and v))
    return out
