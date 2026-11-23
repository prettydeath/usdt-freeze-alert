"""Per-source health: what every run did to every source, shown on the dashboard "Status" tab and in site/health.json.

Stored in the `source_health` table: last success, last error (sanitised: no keys, no URL queries), consecutive failures,
rows added by the last run, and a coverage text built from the cursors that already live in `meta`. A source is
  failing   - 3 or more consecutive failed runs (red header pill)
  degraded  - 1-2 failed runs, or the on-chain count is ahead of our data by more than health.MAX_GAP (amber header pill)
  ok        - everything else; "partial" only says that history is still being back-filled."""
import json
import re

from .db import meta_get
from .health import MAX_GAP
from .util import iso, now_ts, parse_iso

FAIL_AFTER = 3
NOTES = {
    "xlayer": "X Layer has no free log API (OKLink needs a key); the public RPC allows 100-block ranges, so history is "
              "scanned in small resumable steps on every run.",
    "oeth": "Optimism Blockscout times out on wide ranges; events are read with eth_getLogs from the public RPC "
            "(10,000-block ranges, resumable).",
    "sol_usdc": "The public Solana RPC is slow and rate-limited; history is back-filled a few pages per run.",
    "sol_usdt": "The public Solana RPC is slow and rate-limited; history is back-filled a few pages per run.",
    "tron": "TronGrid and Tronscan are rate-limited; the on-chain queue is read first, timestamps may arrive a run later.",
    "inflows": "Transfers to frozen addresses are scanned address by address; TronGrid is the slow part.",
    "outflows": "Outgoing transfers before each freeze (72 h window); TronGrid and Etherscan limits make this a slow, resumable scan.",
    "labels": "Labels come from the public wallet-attribution dataset (MIT) and its upstream sources, refreshed once a day.",
    "signals": "Cross-chain signals: other chains are re-checked on every run while a signal is open.",
    "balances": "Current balances of frozen addresses, refreshed once a day per address.",
}


def sanitize(err, limit=160):
    """Short error text without secrets: URL queries, key=value pairs and long opaque tokens are removed."""
    s = str(err or "").replace("\n", " ")
    s = re.sub(r"(https?://[^\s?'\"]+)\?[^\s'\"]*", r"\1", s)
    s = re.sub(r"(?i)\b(api[_-]?key|apikey|key|token|secret|auth)\b\s*[=:]\s*\S+", r"\1=***", s)
    s = re.sub(r"\b(?!0x)[A-Za-z0-9_\-]{32,}\b", "***", s)
    return s[:limit].strip()


def record(conn, source, ok, error=None, rows_added=0, soft_error=None):
    """Update the health row of `source` after one run. `soft_error` = a non-fatal problem inside an otherwise successful
    run (e.g. token events failed while the Safe API worked): counted as a failed run, last success still moves."""
    now = iso(now_ts())
    r = conn.execute("SELECT * FROM source_health WHERE source=?", (source,)).fetchone()
    fails = r["consec_fail"] if r else 0
    last_ok, last_err, last_err_at = (r["last_ok"], r["last_error"], r["last_error_at"]) if r else (None, None, None)
    if ok:
        last_ok = now
    msg = sanitize(error if not ok else soft_error)
    if not ok or soft_error:
        fails += 1
        last_err, last_err_at = msg, now
    else:
        fails = 0
    conn.execute(
        "INSERT INTO source_health(source,last_ok,last_error,last_error_at,consec_fail,rows_added,updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(source) DO UPDATE SET last_ok=excluded.last_ok, last_error=excluded.last_error, "
        "last_error_at=excluded.last_error_at, consec_fail=excluded.consec_fail, rows_added=excluded.rows_added, "
        "updated_at=excluded.updated_at", (source, last_ok, last_err, last_err_at, fails, rows_added, now))


def _date(ts):
    return iso(int(ts))[:10] if ts else "?"


def coverage_text(conn, source, health=None):
    """-> (text, partial: bool, gap: int|None) built from the cursors in meta / table counts."""
    g = lambda k: meta_get(conn, f"{source}_{k}")  # noqa: E731
    n = conn.execute("SELECT COUNT(*), MAX(executed_at) FROM proposals WHERE chain=?", (source,)).fetchone()
    parts, partial, gap = [], False, None
    if source in ("eth", "tron") and health and isinstance(health.get(source), dict) and "gap" in health[source]:
        h = health[source]
        gap = h["gap"]
        parts.append(f"on-chain {h['onchain_count']} / DB newest id {h['max_id']} (gap {h['gap']})")
    if source in ("sol_usdc", "sol_usdt"):
        if g("complete") == "1":
            parts.append("full history")
        else:
            partial = True
            parts.append("history back to " + _date(g("oldest_ts")) if g("oldest_ts") else "history not started")
    if source == "near" and g("mapping_ok") == "0":
        partial = True
        parts.append("request-id to creation mapping does not match on-chain: queue history not written")
    if g("scan_lo") is not None and g("scan_hi") is not None:
        lo, hi, first = int(g("scan_lo")), int(g("scan_hi")), int(g("scan_first") or 0)
        latest = int(g("scan_latest") or hi)
        span = max(1, latest - first)
        pct = min(100.0, 100.0 * (hi - lo + 1) / span)
        if lo > first:
            partial = True
            when = g("scan_lo_ts")
            parts.append(f"log scan {pct:.1f}% (back to block {lo:,}" + (f", {_date(when)})" if when else ")"))
        else:
            parts.append("log scan complete")
    elif g("ev_source"):
        parts.append(f"token events via {g('ev_source')}")
    if g("safe_full") == "1":
        parts.append("Safe history complete")
    if n and n[0] and source not in ("sol_usdc", "sol_usdt") and not parts:
        parts.append(f"{n[0]} proposals" + (f", newest executed {n[1][:10]}" if n[1] else ""))
    elif n and n[0] and source not in ("sol_usdc", "sol_usdt"):
        parts.append(f"{n[0]} rows")
    return "; ".join(parts) or "-", partial, gap


def pseudo_sources(conn):
    """Derived pipelines shown next to the real sources: inflows scan and balance refresh (N/M)."""
    from . import inflows
    out = {}
    try:
        done, total = inflows.coverage(conn)
        per = []
        cands = inflows.candidates(conn)
        tot = {}
        for c in cands:
            tot[c[0]] = tot.get(c[0], 0) + 1
        have = {}
        for r in conn.execute("SELECT chain, COUNT(*) n FROM inflow_cursor GROUP BY chain"):
            have[r["chain"]] = r["n"]
        for c in sorted(tot):
            per.append(f"{c} {min(have.get(c, 0), tot[c])}/{tot[c]}")
        out["inflows"] = (f"inflows {done}/{total}" + (" (" + ", ".join(per) + ")" if per else ""), done < total)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import outflows
        done, total = outflows.coverage(conn)
        out["outflows"] = (f"outflows {done}/{total} freezes scanned (newest first)", done < total)
        n = conn.execute("SELECT COUNT(*) FROM label_checked").fetchone()[0]
        out["labels"] = (f"{n} addresses matched against wallet-attribution", False)
        s = conn.execute("SELECT SUM(status='open'), COUNT(*) FROM signals").fetchone()
        out["signals"] = (f"{s[0] or 0} open / {s[1]} cross-chain signals", False)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import analytics
        targets = analytics.open_targets(conn)
        have = {(r["chain"], r["address"]) for r in conn.execute("SELECT chain, address FROM balances")}
        k = sum(1 for t in targets if t in have)
        out["balances"] = (f"balances {k}/{len(targets)}", k < len(targets))
    except Exception:  # noqa: BLE001
        pass
    return out


def status_of(fails, gap, partial):
    if fails >= FAIL_AFTER:
        return "failing"
    if fails >= 1 or (gap is not None and gap > MAX_GAP):
        return "degraded"
    return "ok"


def build(conn, health=None, labels=None):
    """-> {overall, bad, sources: [{source,label,status,last_ok,last_error,last_error_at,fails,rows_added,
    coverage,partial,gap,note}]}"""
    labels = labels or {}
    rows = {r["source"]: dict(r) for r in conn.execute("SELECT * FROM source_health")}
    pseudo = pseudo_sources(conn)
    out = []
    for src in sorted(set(rows) | set(pseudo)):
        r = rows.get(src, {})
        if src in pseudo:
            cov, partial = pseudo[src]
            gap = None
        else:
            cov, partial, gap = coverage_text(conn, src, health)
        fails = r.get("consec_fail") or 0
        out.append({"source": src, "label": labels.get(src, src), "status": status_of(fails, gap, partial),
                    "last_ok": r.get("last_ok"), "last_error": r.get("last_error"), "last_error_at": r.get("last_error_at"),
                    "fails": fails, "rows_added": r.get("rows_added") or 0, "coverage": cov, "partial": partial, "gap": gap,
                    "note": NOTES.get(src)})
    order = {"failing": 0, "degraded": 1, "ok": 2}
    out.sort(key=lambda x: (order[x["status"]], x["source"]))
    states = {x["status"] for x in out}
    overall = "failing" if "failing" in states else "degraded" if "degraded" in states else "ok"
    return {"overall": overall, "bad": [x["source"] for x in out if x["status"] != "ok"],
            "sources": out}


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = ["record", "build", "sanitize", "status_of", "parse_iso"]
