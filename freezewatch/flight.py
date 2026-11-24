"""Hop-ahead watch ("pre-proposal flight").

Funds leave a freeze target in the hours before its proposal. The recipients of those funds (hop 1) - and the recipients of
those (hop 2) - are the next addresses that may be frozen, and they often move the money on BEFORE their own proposal exists
(reference case 2026-10-29: 0x5227f603... -> 0x0ddf0480... -> 0x8d539036..., which sent $130M on 48 s before its own submission).

1. refresh_watch(): recipients of escaped outflows of recent freezes (>= MIN_WATCH_USD, no exchange / bridge / DEX / fee collector,
   no sink) become `watched` (hop 1); recipients of watched addresses' moves become hop 2.
2. scan(): watched addresses are polled for outgoing transfers; each new move is stored with the status of the address' OWN
   proposal: none yet / already there / came later (so a flight is confirmed after the fact).
3. events(): a move of >= ALERT_MIN_USD by a watched address with no proposal yet -> "PRE-PROPOSAL FLIGHT" alert (fresh only, deduped).
Ethereum and TRON only (the chains with outflow fetchers). Network access is injectable for tests."""
import logging
import time

from . import analytics, deadline, outflows, trongrid
from .addresses import canon_for
from .evasion import DUST_USD, load_watchlist, watch_entry
from .labels import NETWORK_OF, is_connector, key_of
from .util import HttpError, iso, now_ts, parse_iso

log = logging.getLogger("freezewatch.flight")
MIN_WATCH_USD = 5_000.0
ALERT_MIN_USD = 10_000.0
WATCH_DAYS = 7
MAX_HOP = 2
RESCAN_SEC = 240
MAX_SCANS = 40
FREEZE_FNS = ("addBlackList", "addToBlockedList", "freeze_accounts", "freeze_wallet")
SKIP_KINDS = ("dex", "bridge", "fee_collector")


def _skip(chain, addr, labels, watch):
    if addr in analytics.special_sinks(chain):
        return True
    w = watch_entry(watch, chain, canon_for(chain, addr))
    if w and w["kind"] in SKIP_KINDS:
        return True
    net = NETWORK_OF.get(chain)
    return bool(net and is_connector(labels.get((net, key_of(net, addr)))))


def refresh_watch(conn, labels, watch=None, now=None, days=WATCH_DAYS, min_usd=MIN_WATCH_USD):
    """Hop-1 watch entries from escaped outflows of freezes of the last `days`. -> number of new watched addresses."""
    now = now or now_ts()
    watch = watch if watch is not None else load_watchlist()
    frozen = {(r[0], r[1]) for r in conn.execute("SELECT chain, target FROM proposals WHERE target IS NOT NULL AND function IN (?,?,?,?) "
                                                 "AND (executed=1 OR status='pending')", FREEZE_FNS)}
    frozen = {(c, analytics.norm(c, a)) for c, a in frozen}
    new = 0
    for r in conn.execute("SELECT chain, frozen_address, recipient, SUM(amount) usd, MIN(ts) first FROM outflows WHERE freeze_ts>=? "
                          "AND chain IN ('eth','tron') GROUP BY chain, frozen_address, recipient", (iso(now - days * 86400),)):
        if r["usd"] < min_usd or _skip(r["chain"], r["recipient"], labels, watch) or (r["chain"], analytics.norm(r["chain"], r["recipient"])) in frozen:
            continue
        new += conn.execute("INSERT OR IGNORE INTO watched(chain,address,source,hop,received_usd,since_ts) VALUES(?,?,?,?,?,?)",
                            (r["chain"], r["recipient"], r["frozen_address"], 1, r["usd"], r["first"])).rowcount
    conn.commit()
    return new


def own_proposal_state(conn, chain, addr, move_ts):
    """'none' no freeze proposal on this address yet, 'before' one was submitted at or before the move, 'after' it came later."""
    first = None
    for r in conn.execute("SELECT submitted_at, first_seen_at, target, chain FROM proposals WHERE chain=? AND function IN (?,?,?,?) "
                          "AND target IS NOT NULL", (chain, *FREEZE_FNS)):
        if analytics.norm(chain, r["target"]) == analytics.norm(chain, addr):
            t = parse_iso(r["submitted_at"] or r["first_seen_at"])
            first = t if first is None else min(first, t)
    if first is None:
        return "none"
    return "before" if first <= move_ts else "after"


def _fetch_out(chain, addr, from_ts, to_ts, stop):
    """Outgoing transfers [(recipient, usd, ts, tx, log_index)] of `addr` in [from_ts, to_ts]."""
    if chain == "tron":
        rows, _src = trongrid.transfers(addr, "out", from_ts * 1000, to_ts * 1000, stop=stop)
        return [p for p in (outflows.parse_tron_out(x) for x in rows) if p]
    rows, _t, pend, _u = outflows.fetch_evm_out(chain, addr, outflows.block_at(chain, from_ts), outflows.block_at(chain, to_ts + 30),
                                                stop=stop, max_queries=20)
    return rows


def scan(conn, labels, watch=None, budget_sec=60, now=None, fetch_out=_fetch_out):
    """Poll watched addresses for new outgoing moves. -> number of new moves."""
    now = now or now_ts()
    watch = watch if watch is not None else load_watchlist()
    t0 = time.time()
    n = 0
    due = conn.execute("SELECT * FROM watched WHERE hop<=? AND (scanned_at IS NULL OR scanned_at<?) ORDER BY hop, since_ts DESC LIMIT ?",
                       (MAX_HOP, iso(now - RESCAN_SEC), MAX_SCANS)).fetchall()
    for w in due:
        if time.time() - t0 > budget_sec or deadline.http_stop():
            break
        since = max(parse_iso(w["since_ts"]), parse_iso(w["scanned_at"]) - 120 if w["scanned_at"] else 0)
        stop = lambda: time.time() - t0 > budget_sec or deadline.http_stop()  # noqa: E731
        try:
            rows = fetch_out(w["chain"], w["address"], since, now, stop)
        except Exception as e:  # noqa: BLE001
            log.debug("flight scan %s: %s", w["address"][:10], str(e)[:60])
            continue
        sk = analytics.special_sinks(w["chain"])
        for rcp, usd, ts, tx, li in rows:
            if usd < DUST_USD or rcp in sk or rcp == w["address"]:
                continue
            st = own_proposal_state(conn, w["chain"], w["address"], ts)
            n += conn.execute("INSERT OR IGNORE INTO watch_moves(chain,address,recipient,amount,ts,tx_hash,log_index,own_proposal) "
                              "VALUES(?,?,?,?,?,?,?,?)", (w["chain"], w["address"], rcp, usd, iso(ts), tx, li, st)).rowcount
            if w["hop"] < MAX_HOP and usd >= MIN_WATCH_USD and not _skip(w["chain"], rcp, labels, watch):
                conn.execute("INSERT OR IGNORE INTO watched(chain,address,source,hop,received_usd,since_ts) VALUES(?,?,?,?,?,?)",
                             (w["chain"], rcp, w["source"], w["hop"] + 1, usd, iso(ts)))
        conn.execute("UPDATE watched SET scanned_at=? WHERE chain=? AND address=?", (iso(now), w["chain"], w["address"]))
    # a flight is confirmed afterwards when the address' own proposal shows up later
    for m in conn.execute("SELECT chain, address, ts, tx_hash, log_index FROM watch_moves WHERE own_proposal='none'").fetchall():
        st = own_proposal_state(conn, m["chain"], m["address"], parse_iso(m["ts"]))
        if st != "none":
            conn.execute("UPDATE watch_moves SET own_proposal=? WHERE chain=? AND tx_hash=? AND log_index=? AND address=?",
                         (st, m["chain"], m["tx_hash"], m["log_index"], m["address"]))
    conn.commit()
    if n:
        log.info("flight: %s new moves by watched addresses", n)
    return n


def events(conn, now=None):
    """Telegram events for fresh moves of >= ALERT_MIN_USD by a watched address with no proposal of its own yet."""
    from .alerts import FRESH_SEC, fmt_flight
    now = now or now_ts()
    out = []
    for m in conn.execute("SELECT m.*, w.source, w.hop FROM watch_moves m JOIN watched w ON w.chain=m.chain AND w.address=m.address "
                          "WHERE m.amount>=? AND m.own_proposal='none'", (ALERT_MIN_USD,)):
        if now - (parse_iso(m["ts"]) or 0) > FRESH_SEC:
            continue
        out.append({"kind": "flight", "chain": "flight", "id": int(m["tx_hash"].replace("0x", "")[:12], 16) % (10 ** 12) + m["log_index"],
                    "key_kind": f"flight:{m['chain']}", "text": fmt_flight(dict(m))})
    return out


def recent(conn, labels, now=None, hours=24, limit=200, min_usd=1000.0):
    """Moves (>= min_usd; smaller ones are noise) of watched addresses in the last `hours`, newest first, for the Live tab. status: watching (no proposal on the address
    yet) / proposal existed (it had one already) / confirmed (its own proposal appeared later) / frozen (and it was executed)."""
    now = now or now_ts()
    out = []
    for m in conn.execute("SELECT m.*, w.source, w.hop FROM watch_moves m JOIN watched w ON w.chain=m.chain AND w.address=m.address "
                          "WHERE m.ts>=? AND m.amount>=? ORDER BY m.ts DESC LIMIT ?", (iso(now - hours * 3600), min_usd, limit)).fetchall():
        st = {"none": "watching", "before": "proposal existed", "after": "confirmed"}.get(m["own_proposal"], m["own_proposal"])
        if m["own_proposal"] == "after":
            for r in conn.execute("SELECT target, chain FROM proposals WHERE chain=? AND executed=1 AND function IN (?,?,?,?) AND target IS NOT NULL",
                                  (m["chain"], *FREEZE_FNS)):
                if analytics.norm(m["chain"], r["target"]) == analytics.norm(m["chain"], m["address"]):
                    st = "frozen"
                    break
        fz = conn.execute("SELECT MAX(freeze_ts) FROM outflows WHERE chain=? AND frozen_address=?", (m["chain"], m["source"])).fetchone()[0]
        net = NETWORK_OF.get(m["chain"])
        lb = labels.get((net, key_of(net, m["recipient"]))) if net else None
        out.append({"ts": parse_iso(m["ts"]), "chain": m["chain"], "address": m["address"], "amount": round(m["amount"], 2),
                    "recipient": m["recipient"], "label": [lb["entity"], lb["label"], lb["category"], lb["source"], lb["source_url"],
                                                           lb["confidence"]] if lb else None,
                    "source": m["source"], "hop": m["hop"], "freeze_ts": parse_iso(fz) if fz else None, "status": st, "tx": m["tx_hash"],
                    "alerted": m["amount"] >= ALERT_MIN_USD and m["own_proposal"] != "before"})
    return out


def alerts_sent(conn, hours=24, now=None):
    now = now or now_ts()
    return conn.execute("SELECT COUNT(*) FROM alerts WHERE chain='flight' AND sent_at>=?", (iso(now - hours * 3600),)).fetchone()[0]


def stats(conn):
    """For the dashboard: watched addresses, moves, confirmed pre-proposal flights (the address was frozen later)."""
    w = conn.execute("SELECT COUNT(*), SUM(hop=1), SUM(hop=2) FROM watched").fetchone()
    m = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0), SUM(own_proposal='after'), "
                     "COALESCE(SUM(CASE WHEN own_proposal='after' THEN amount END),0), SUM(own_proposal='none') FROM watch_moves").fetchone()
    now = now_ts()
    m24 = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM watch_moves WHERE ts>=?", (iso(now - 86400),)).fetchone()
    return {"watched": w[0] or 0, "hop1": w[1] or 0, "hop2": w[2] or 0, "moves": m[0] or 0, "moved_usd": round(m[1], 2),
            "confirmed": m[2] or 0, "confirmed_usd": round(m[3], 2), "unfrozen_yet": m[4] or 0, "moves_24h": m24[0], "moved_24h_usd": round(m24[1], 2),
            "alerts_24h": alerts_sent(conn)}
