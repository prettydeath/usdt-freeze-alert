"""Optional Telegram alerts (HTML). No TG_BOT_TOKEN / TG_CHAT_ID -> nothing is sent.

Event types (priority order): new freeze proposal, one signature left, frozen (executed), funds destroyed,
cross-chain balance, unfrozen, SPL multisig change. Each event is sent at most once (table `alerts`), at most
MAX_PER_RUN messages are sent per run (the rest become one summary line), and history is never replayed:
nothing is sent on the first run against an empty DB, and only events younger than the freshness window qualify."""
import html
import json
import logging
import time
from urllib.parse import urlparse

import requests

from .constants import DESTROY_FUNCS, FREEZE_FUNCS, UNFREEZE_FUNCS
from .db import iso
from .util import env, now_ts, parse_iso

log = logging.getLogger("freezewatch.alerts")
DASHBOARD = "https://prettydeath.github.io/usdt-freeze-alert/"
MAX_PER_RUN = 20
FRESH_SEC = 3 * 3600          # event time must be this recent
NEAR_FRESH_SEC = 24 * 3600    # "one signature left" may come later than the proposal
MIN_XCHAIN_USD = 1000
TRACKED = FREEZE_FUNCS + UNFREEZE_FUNCS + DESTROY_FUNCS
PRIO = {"new": 0, "near": 1, "frozen": 2, "flight": 3, "destroy": 3, "inflow": 4, "xchain": 5, "unfrozen": 6, "authority": 7}
NL = chr(10)


def configured():
    return bool(env("TG_BOT_TOKEN") and env("TG_CHAT_ID"))


def send_message(text):
    r = requests.post(f"https://api.telegram.org/bot{env('TG_BOT_TOKEN')}/sendMessage",
                      json={"chat_id": env("TG_CHAT_ID"), "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=(5, 20))
    r.raise_for_status()


# ---------- formatting ----------
def e(x):
    return html.escape(str(x), quote=False)


def num(x):
    """Thousands separators; no decimals from 1000 up."""
    if x is None:
        return "n/a"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def human(sec, approx=True):
    if sec is None:
        return "n/a"
    sec = int(sec)
    p = "~" if approx else ""
    if sec < 60:
        return f"{p}{max(sec, 1)} s"
    if sec < 5400:
        return f"{p}{round(sec / 60)} min"
    if sec < 172800:
        h, m = divmod(sec // 60, 60)
        return f"{p}{h} h" + (f" {m} min" if m else "")
    return f"{p}{round(sec / 86400)} d"


def meta_of(chain):
    from .registry import chain_meta
    return chain_meta().get(chain, {"label": chain})


TAGS = {"arb1": "ARB", "pol": "POL", "oeth": "OP", "usdc_eth": "USDC-ETH", "sol_usdc": "USDC-SOL",
        "sol_usdt": "USDT-SOL", "hyper": "HYPER", "berachain": "BERA", "arb_usdt": "ARB-USDT"}


def short(chain):
    """Uppercase short chain name: TRON, ETH, AVAX, ARB, POL ..."""
    return TAGS.get(chain, chain.upper())


def tag(s):
    import re
    return "#" + re.sub(r"\W", "", str(s))


def token_of(chain):
    from .registry import EVENT_CHAINS, SAFE_CHAINS
    if chain in SAFE_CHAINS or chain in EVENT_CHAINS:
        return "USDT0"
    return "USDC" if chain in ("usdc_eth", "sol_usdc") else "USDT"


def bar(conf, req, cells=10):
    if not req:
        return "▱" * cells
    n = max(0, min(cells, round(cells * conf / req)))
    return "▰" * n + "▱" * (cells - n)


def link(url_base, value, text=None):
    if not url_base or not value:
        return ""
    host = urlparse(url_base).netloc.split(".")
    name = text or (host[-2] if len(host) >= 2 else host[0])
    return f'<a href="{html.escape(url_base + str(value), quote=True)}">{e(name)}</a>'


def site_link(addr):
    """Link to the address page of the dashboard (#addr=<address>); every alert carries it next to the explorer link."""
    from urllib.parse import quote
    if not addr:
        return ""
    return f'<a href="{html.escape(DASHBOARD + "#addr=" + quote(str(addr), safe=""), quote=True)}">site</a>'


def footer_links(chain, target):
    lk = link(meta_of(chain).get("addr"), target, text="explorer")
    return " · ".join(x for x in (lk, site_link(target), f'<a href="{DASHBOARD}">dashboard</a>') if x)


def median_30d(conn, chain, now=None):
    now = now or now_ts()
    d = sorted(r[0] for r in conn.execute(
        "SELECT delay_seconds FROM proposals WHERE chain=? AND executed=1 AND delay_seconds IS NOT NULL "
        "AND executed_at>=?", (chain, iso(now - 30 * 86400))))
    if not d:
        return None
    n = len(d)
    return d[n // 2] if n % 2 else (d[n // 2 - 1] + d[n // 2]) / 2


def proposal_ref(r):
    from .registry import SAFE_CHAINS
    return f"Safe nonce {r['tx_id'] // 100}" if r["chain"] in SAFE_CHAINS else f"proposal #{r['tx_id']}"


def fmt_new(r, med=None, seen=None, exposure=()):
    from .registry import SAFE_CHAINS
    ch, bal = r["chain"], r["balance_now"]
    seen = seen or time.strftime("%H:%M:%S", time.gmtime())
    lines = [f"🚨 <b>FREEZE INCOMING</b> · {e(short(ch))}", f"<code>{e(r['target'])}</code>"]
    if bal is not None:
        lines.append(f"💰 <b>{num(bal)} {token_of(ch)}</b> on the line")
    lines.append(f"✍️ {bar(r['confirmations'], r['required'])} {r['confirmations']}/{r['required']} signed")
    if med is not None:
        lines.append(f"⏱ usually lands in <b>{human(med)}</b>")
    lines += [e(x) for x in exposure]   # allowances / EIP-7702 delegation: who can still move the funds
    detail = f"{proposal_ref(r)} · {e(r['function'])}" + (" · via Safe API (off-chain queue)" if ch in SAFE_CHAINS else "")
    lines.append(f"<blockquote>{detail}{NL}seen {seen} UTC</blockquote>")
    tags = f"{tag(short(ch))} {tag(r['function'])}" + (" #whale" if (bal or 0) >= 100_000 else "")
    lines += [tags, footer_links(ch, r["target"])]
    return NL.join(lines)


def _exposure_lines(conn, r):
    if r["chain"] not in ("eth", "tron") or not r["target"]:
        return ()
    try:
        from . import exposure
        return exposure.lines(conn, r["chain"], r["target"])
    except Exception:  # noqa: BLE001
        return ()


def fmt_near(r):
    ch, bal = r["chain"], r["balance_now"]
    first = f"<code>{e(r['target'])}</code>" + (f" · 💰 {num(bal)}" if bal is not None else "")
    return NL.join([f"⚠️ <b>LAST SIGNATURE PENDING</b> · {e(short(ch))}", first,
                    f"✍️ {bar(r['confirmations'], r['required'])} {r['confirmations']}/{r['required']} signed",
                    f"{tag(short(ch))} {tag(r['function'])}", footer_links(ch, r["target"])])


def fmt_frozen(r, advance=True):
    ch = r["chain"]
    bal = r["balance_at_detect"] if r["balance_at_detect"] is not None else r["balance_now"]
    lines = [f"🧊 <b>FROZEN</b> · {e(short(ch))}", f"<code>{e(r['target'])}</code>"]
    if bal is not None:
        lines.append(f"💰 {num(bal)} {token_of(ch)} locked")
    nopub = ("public_proposal" in r.keys()) and r["public_proposal"] == 0
    if nopub:
        lines.append("⚡ <b>no public proposal</b>")
    elif r["delay_seconds"] in (None, 0) or not advance:
        if r["delay_seconds"] is not None or not advance:
            lines.append("⚡ <b>no advance notice</b>")
    else:
        lines.append(f"⚡ executed in <b>{human(r['delay_seconds'], approx=False)}</b>")
    signed = f"{r['confirmations']}/{r['required']} signed · " if r["required"] else ""
    lines.append(f"<blockquote>{signed}tx {e(r['exec_tx'] or 'n/a')}</blockquote>")
    lines += [f"{tag(short(ch))} #frozen", footer_links(ch, r["target"]).replace("explorer", "explorer")]
    return NL.join(lines)


def fmt_destroy(r):
    ch = r["chain"]
    bal = r["balance_at_detect"] if r["balance_at_detect"] is not None else r["balance_now"]
    lines = [f"🔥 <b>FUNDS BURNED</b> · {e(short(ch))}", f"<code>{e(r['target'])}</code>"]
    if bal is not None:
        lines.append(f"💸 <b>{num(bal)} {token_of(ch)}</b> destroyed")
    lines.append(f"<blockquote>tx {e(r['exec_tx'] or 'n/a')}</blockquote>")
    lines += [f"{tag(short(ch))} #burned", footer_links(ch, r["target"])]
    return NL.join(lines)


def fmt_unfrozen(r):
    ch = r["chain"]
    return NL.join([f"🟢 <b>UNFROZEN</b> · {e(short(ch))}", f"<code>{e(r['target'])}</code>",
                    f"{tag(short(ch))} #unfrozen", footer_links(ch, r["target"])])


def fmt_freeze_row(f):
    """Executed-only freezes (USDC Ethereum, Solana): there is no queue, so no delay line."""
    src = f["source"]
    lines = [f"🧊 <b>FROZEN</b> · {e(short(src))}", f"<code>{e(f['subject'])}</code>"]
    if f["owner"] and f["owner"] != f["subject"]:
        lines.append(f"owner <code>{e(f['owner'])}</code>")
    if f["amount"] is not None:
        lines.append(f"💰 {num(f['amount'])} {token_of(src)} locked")
    lines.append("⚡ <b>no advance notice</b>")
    lines.append(f"<blockquote>tx {e(f['tx_hash'])}</blockquote>")
    lines.append(f"{tag(short(src))} #frozen")
    return NL.join(lines)


def fmt_xchain(addr, chain, have, elsewhere):
    lines = ["🔗 <b>CROSS-CHAIN SIGNAL</b>", f"<code>{e(addr)}</code>",
             f"frozen on {e(', '.join(sorted(short(c) for c in have)))}"]
    for c, b in sorted(elsewhere.items()):
        lines.append(f"still holds 💰 {num(b)} {token_of(c)} on {e(short(c))}")
    lines += ["#crosschain", site_link(addr)]
    return NL.join(lines)


def fmt_signal(s):
    """Cross-chain signal row (signals table) -> Radar message."""
    from .signals import display
    risk = json.loads(s["at_risk"]) if isinstance(s["at_risk"], str) else s["at_risk"]
    when = (parse_iso(s["frozen_at"]) and time.strftime("%H:%M", time.gmtime(parse_iso(s["frozen_at"])))) or "?"
    verb = "frozen on" if s.get("stage", "frozen") == "frozen" else "freeze proposed on"
    lines = ["🔗 <b>CROSS-CHAIN SIGNAL</b>", f"<code>{e(display(s['address'], s['frozen_on']))}</code>",
             f"{verb} <b>{e(short(s['frozen_on']))}</b> at {when} UTC"]
    held = ", ".join(f"<b>${num(x[3])}</b> {e(x[1])} on {e(short(x[0]))}" for x in sorted(risk, key=lambda x: -x[3]))
    lines.append(f"still holds 💰 {held} — not blocked there")
    lines += [f"#crosschain {tag(short(s['frozen_on']))}", site_link(display(s["address"], s["frozen_on"]))]
    return NL.join(lines)


def fmt_flight(m):
    """Watched address (recipient of funds that left a frozen address) moved money before any proposal on it."""
    chain = m["chain"]
    meta = meta_of(chain)
    when = time.strftime("%H:%M", time.gmtime(parse_iso(m["ts"]))) if parse_iso(m["ts"]) else "?"
    lines = [f"🏃 <b>PRE-PROPOSAL FLIGHT</b> · {e(short(chain))}",
             f"<code>{e(m['address'])}</code>",
             f"sent 💰 <b>${num(m['amount'])} USDT</b> at {when} UTC → <code>{e(m['recipient'])}</code>",
             f"it received funds from frozen <code>{e(m['source'])}</code> (hop {m['hop']}); no freeze proposal on it yet",
             f"{tag(short(chain))} #preproposalflight"]
    lk = link(meta.get("tx"), m["tx_hash"], text="explorer")
    return NL.join(lines + [" · ".join(x for x in (lk, site_link(m["address"])) if x)])


def fmt_authority(ch):
    return (f"⚠️ <b>SPL MULTISIG CHANGED</b> · {e(ch['source'])}" + NL +
            f"was {e(ch['old'])}" + NL + f"now {e(ch['new'])}")


def fmt_inflow(r):
    ch = r["chain"]
    meta = meta_of(ch)
    lines = [f"💸 <b>SENT TO A FROZEN ADDRESS</b> · {e(short(ch))}",
             f"<b>{num(r['amount'])} {r['token'] or token_of(ch)}</b>",
             f"from <code>{e(r['sender'])}</code> → to <code>{e(r['frozen_address'])}</code>",
             "🔒 funds are now locked", f"<blockquote>tx {e(r['tx_hash'])}</blockquote>",
             f"{tag(short(ch))} #lockedfunds"]
    lk = link(meta.get("tx"), r["tx_hash"], text="explorer")
    return NL.join(lines + [" · ".join(x for x in (lk, site_link(r["frozen_address"])) if x)])


def fmt_summary(n):
    return f'📊 <b>+{n} more events</b> → <a href="{DASHBOARD}">dashboard</a>'


# ---------- event collection ----------
def snapshot(conn):
    """{(chain,id): (executed, confirmations, required, function, status)} before an update."""
    return {(r["chain"], r["tx_id"]): (r["executed"], r["confirmations"], r["required"], r["function"], r["status"])
            for r in conn.execute("SELECT chain,tx_id,executed,confirmations,required,function,status FROM proposals")}


def collect_events(conn, before, max_freeze_rowid, before_pairs, auth_changes=(), now=None, min_balance=None,
                   balances=None, max_inflow_rowid=None, min_inflow=None):
    """Candidate events after an update. Returns a list of dicts: kind, chain, id, key_kind, text."""
    now = now or now_ts()
    if min_balance is None:
        min_balance = float(env("ALERT_MIN_BALANCE", "0") or 0)
    if not before and max_freeze_rowid == 0:  # first run against an empty DB: initialise, do not announce history
        return []
    ev = []
    new_keys = set()
    qm = ",".join("?" * len(TRACKED))
    for r in conn.execute(f"SELECT * FROM proposals WHERE function IN ({qm})", TRACKED).fetchall():
        key = (r["chain"], r["tx_id"])
        prev = before.get(key)
        sub = parse_iso(r["submitted_at"]) or parse_iso(r["first_seen_at"])
        exe = parse_iso(r["executed_at"])
        fn = r["function"]
        if fn in FREEZE_FUNCS and r["status"] == "pending" and prev is None and sub and now - sub <= FRESH_SEC \
                and (r["balance_now"] or 0) >= min_balance:
            ev.append({"kind": "new", "chain": r["chain"], "id": r["tx_id"], "key_kind": "new",
                       "text": fmt_new(r, median_30d(conn, r["chain"], now),
                                           exposure=_exposure_lines(conn, r))})
            new_keys.add(key)
        if fn in FREEZE_FUNCS and r["status"] == "pending" and r["required"] and r["required"] > 1 \
                and r["confirmations"] == r["required"] - 1 and (prev is None or prev[1] < r["confirmations"]) \
                and sub and now - sub <= NEAR_FRESH_SEC and not (key in new_keys and r["confirmations"] == 1)                 and (r["balance_now"] or 0) >= min_balance:
            ev.append({"kind": "near", "chain": r["chain"], "id": r["tx_id"], "key_kind": "near", "text": fmt_near(r)})
        if r["executed"] and (prev is None or not prev[0]) and exe and now - exe <= FRESH_SEC and r["target"]:
            if fn in FREEZE_FUNCS:
                ev.append({"kind": "frozen", "chain": r["chain"], "id": r["tx_id"], "key_kind": "frozen",
                           "text": fmt_frozen(r, advance=prev is not None)})
            elif fn in DESTROY_FUNCS:
                ev.append({"kind": "destroy", "chain": r["chain"], "id": r["tx_id"], "key_kind": "destroy",
                           "text": fmt_destroy(r)})
            elif fn in UNFREEZE_FUNCS:
                ev.append({"kind": "unfrozen", "chain": r["chain"], "id": r["tx_id"], "key_kind": "unfrozen",
                           "text": fmt_unfrozen(r)})
    for f in conn.execute("SELECT rowid AS rid, * FROM freezes WHERE rowid>? AND action='freeze' ORDER BY rowid",
                          (max_freeze_rowid,)).fetchall():
        if now - (parse_iso(f["ts"]) or 0) <= FRESH_SEC:
            ev.append({"kind": "frozen", "chain": f["source"], "id": f["rid"], "key_kind": "freeze",
                       "text": fmt_freeze_row(f)})
    if max_inflow_rowid is not None:
        if min_inflow is None:
            min_inflow = float(env("ALERT_MIN_INFLOW", "1000") or 1000)
        for r in conn.execute("SELECT rowid AS rid, * FROM inflows_after_freeze WHERE rowid>? AND amount>=? ORDER BY rowid",
                              (max_inflow_rowid, min_inflow)).fetchall():
            if now - (parse_iso(r["ts"]) or 0) <= FRESH_SEC:
                ev.append({"kind": "inflow", "chain": r["chain"], "id": r["rid"], "key_kind": "inflow", "text": fmt_inflow(r)})
    if balances is None:
        from . import flight
        ev += flight.events(conn, now)
    if balances is not None:  # legacy path (tests inject balances); production uses the signals table
        ev += crosschain_events(conn, before_pairs, now, balances)
    else:
        from . import signals
        ev += signals.events(conn, now)
    for ch in auth_changes:
        ev.append({"kind": "authority", "chain": "authority", "id": int(now) // 3600, "key_kind": "authority:" + ch["source"],
                   "text": fmt_authority(ch)})
    ev.sort(key=lambda x: PRIO[x["kind"]])
    return ev


def crosschain_events(conn, before_pairs, now, balances=None):
    """Address newly seen on a chain that still holds USDT (>= $1000) on chains where it is not yet frozen."""
    from . import crosschain
    balances = balances or crosschain.balances_elsewhere
    out = []
    cur = crosschain.collect(conn)
    for addr, items in cur.items():
        have = {it["chain"] for it in items}
        for ch in have:
            if (addr, ch) in before_pairs:
                continue
            ts = max((parse_iso(it["ts"]) or 0) for it in items if it["chain"] == ch)
            if now - ts > FRESH_SEC:
                continue
            elsewhere = {c: b for c, b in balances(addr, have).items() if b >= MIN_XCHAIN_USD}
            if elsewhere:
                out.append({"kind": "xchain", "chain": "xchain", "id": int(addr[2:16], 16), "key_kind": "xchain:" + ch,
                            "text": fmt_xchain(addr, ch, have, elsewhere)})
    return out


# ---------- dispatch ----------
def dispatch(conn, events, send, limit=MAX_PER_RUN):
    """Send events once each (dedupe via `alerts`), flood-guarded. Returns the number of messages sent."""
    sent = overflow = 0
    for ev in events:
        if conn.execute("SELECT 1 FROM alerts WHERE chain=? AND tx_id=? AND kind=?",
                        (ev["chain"], ev["id"], ev["key_kind"])).fetchone():
            continue
        if sent >= limit:
            overflow += 1
            conn.execute("INSERT OR IGNORE INTO alerts(chain,tx_id,kind,sent_at) VALUES(?,?,?,?)",
                         (ev["chain"], ev["id"], ev["key_kind"], iso(now_ts())))
            continue
        try:
            send(ev["text"])
        except Exception as ex:  # noqa: BLE001
            log.warning("Telegram: %s", ex)
            continue
        conn.execute("INSERT OR IGNORE INTO alerts(chain,tx_id,kind,sent_at) VALUES(?,?,?,?)",
                     (ev["chain"], ev["id"], ev["key_kind"], iso(now_ts())))
        sent += 1
    if overflow:
        try:
            send(fmt_summary(overflow))
            sent += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("Telegram: %s", ex)
    conn.commit()
    return sent


def run_alerts(conn, before, max_freeze_rowid, before_pairs, auth_changes=(), max_inflow_rowid=None):
    """Entry point used by `update`. No-op unless Telegram is configured."""
    changes = list(auth_changes)
    if hasattr(auth_changes, "clear"):
        auth_changes.clear()
    if not configured():
        return 0
    return dispatch(conn, collect_events(conn, before, max_freeze_rowid, before_pairs, changes,
                                         max_inflow_rowid=max_inflow_rowid), send_message)


# ---------- fixtures for `alert-test` ----------
def sample_messages():
    base = {"chain": "tron", "tx_id": 11957, "function": "addBlackList", "target": "TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd",
            "balance_now": 355678.98, "balance_at_detect": 355678.98, "confirmations": 1, "required": 2,
            "delay_seconds": 6564, "exec_tx": "9830c04b5c02cb5279b3c4f855a528b4c619f8ced34dbdbb8678b97645595d60"}
    eth = {**base, "chain": "eth", "tx_id": 5870, "function": "destroyBlackFunds",
           "target": "0xe3d1143fccc957186b013fec87c492c31fb15e14", "balance_now": 5680262.52,
           "balance_at_detect": 5680262.52, "confirmations": 2,
           "required": 3, "exec_tx": "0x" + "ab" * 32}
    safe = {**base, "chain": "arb1", "tx_id": 18500, "function": "addToBlockedList",
            "target": "0xe485e7a70b1131216cb47ca4555c4bbb2c742478", "confirmations": 1, "required": 3,
            "balance_now": 12000.0}
    return [fmt_new(base, 60), fmt_new(safe, 5413), fmt_near(eth), fmt_frozen(base), fmt_frozen({**base, "delay_seconds": 0}),
            fmt_destroy(eth), fmt_unfrozen({**base, "function": "removeBlackList"}),
            fmt_inflow({"chain": "tron", "token": "USDT", "amount": 48250.0, "sender": "TXyz5S4fRrYJm3LPrAfF2wGZvz8xA1aBcD",
                        "frozen_address": "TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd", "tx_hash": "ab" * 32}),
            fmt_xchain("0xe485e7a70b1131216cb47ca4555c4bbb2c742478", "pol", {"pol", "arb1"}, {"eth": 25000.0}),
            fmt_signal({"address": "0xe485e7a70b1131216cb47ca4555c4bbb2c742478", "frozen_on": "eth", "stage": "frozen",
                        "frozen_at": iso(now_ts()), "at_risk": [["pol", "USDT0", 25000.0, 25000.0], ["arb1", "USDT0", 3100.0, 3100.0]]}),
            fmt_summary(7), _daily_sample()]


def _daily_sample():
    from .daily import sample_report
    return sample_report()
