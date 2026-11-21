"""Необов'язкові Telegram-сповіщення. Без TG_BOT_TOKEN/TG_CHAT_ID - нічого не робить."""
import logging

import requests

from .constants import BALANCE_FUNCS
from .db import iso
from .util import env, now_ts

log = logging.getLogger("freezewatch.alerts")
TRACKED = ("addBlackList", "removeBlackList", "destroyBlackFunds", "addToBlockedList", "removeFromBlockedList",
           "destroyBlockedFunds")
NL = chr(10)
EXPLORER = {"eth": "https://etherscan.io/address/", "tron": "https://tronscan.org/#/address/"}


def explorer(chain):
    from .registry import chain_meta
    return EXPLORER.get(chain) or chain_meta().get(chain, {}).get("addr", "")


def configured():
    return bool(env("TG_BOT_TOKEN") and env("TG_CHAT_ID"))


def snapshot(conn):
    """{(chain,id): (executed, confirmations, required, function, status)} - для порівняння до/після update."""
    return {(r["chain"], r["tx_id"]): (r["executed"], r["confirmations"], r["required"], r["function"], r["status"])
            for r in conn.execute("SELECT chain,tx_id,executed,confirmations,required,function,status FROM proposals")}


def _send(text):
    r = requests.post(f"https://api.telegram.org/bot{env('TG_BOT_TOKEN')}/sendMessage",
                      json={"chat_id": env("TG_CHAT_ID"), "text": text, "disable_web_page_preview": True}, timeout=20)
    r.raise_for_status()


def _fmt(r, head):
    bal = f"{r['balance_now']:,.2f} USDT" if r["balance_now"] is not None else "n/a"
    t = f"{head}\n{r['chain'].upper()} #{r['tx_id']} {r['function']}\n"
    if r["target"]:
        t += f"Target: {r['target']}\n{explorer(r['chain'])}{r['target']}\n"
    if r["function"] in BALANCE_FUNCS:
        t += f"Balance: {bal}\n"
    t += f"Signatures: {r['confirmations']}/{r['required']}"
    if r["delay_seconds"] is not None:
        t += f"\nDelay proposal->execution: {r['delay_seconds']} s"
    return t


def process(conn, before, max_age_days=3):
    """Порівнює стан до/після update і шле по одному сповіщенню на подію (таблиця alerts)."""
    if not configured():
        return 0
    sent = 0
    cutoff = now_ts() - max_age_days * 86400
    for r in conn.execute("SELECT * FROM proposals WHERE function IN (?,?,?)", TRACKED):
        key = (r["chain"], r["tx_id"])
        prev = before.get(key)
        events = []
        if prev is None and r["function"] in ("addBlackList", "addToBlockedList") and r["status"] == "pending":
            events.append(("new", "New freeze proposal"))
        if r["required"] and r["required"] >= 3 and r["status"] == "pending" and r["confirmations"] >= r["required"] - 1 \
                and (prev is None or prev[1] < r["required"] - 1):
            events.append(("near", "Proposal reached required-1 signatures"))
        if r["executed"] and (prev is None or not prev[0]) and prev is not None:
            events.append(("executed", "Executed"))
        for kind, head in events:
            from .util import parse_iso
            sub = parse_iso(r["submitted_at"])
            if kind != "executed" and sub and sub < cutoff:
                continue
            if conn.execute("SELECT 1 FROM alerts WHERE chain=? AND tx_id=? AND kind=?", (r["chain"], r["tx_id"], kind)).fetchone():
                continue
            try:
                _send(_fmt(r, head))
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram: %s", e)
                continue
            conn.execute("INSERT INTO alerts(chain,tx_id,kind,sent_at) VALUES(?,?,?,?)",
                         (r["chain"], r["tx_id"], kind, iso(now_ts())))
            sent += 1
    conn.commit()
    return sent


def process_freezes(conn, max_rowid_before, auth_changes=()):
    """Нові виконані заморожування USDC/Solana (лише з update, не з backfill) + зміни складу multisig Solana."""
    if not configured():
        return 0
    from .constants import SOURCE_LABELS
    from .util import parse_iso
    sent = 0
    cutoff = now_ts() - 3 * 86400
    for r in conn.execute("SELECT rowid AS rid, * FROM freezes WHERE rowid>? AND action='freeze' ORDER BY rowid",
                          (max_rowid_before,)).fetchall():
        if (parse_iso(r["ts"]) or 0) < cutoff:
            continue
        if conn.execute("SELECT 1 FROM alerts WHERE chain=? AND tx_id=? AND kind='freeze'", (r["source"], r["rid"])).fetchone():
            continue
        t = f"Frozen ({SOURCE_LABELS.get(r['source'], r['source'])})" + NL + f"Account/address: {r['subject']}" + NL
        if r["owner"] and r["owner"] != r["subject"]:
            t += f"Owner: {r['owner']}" + NL
        if r["amount"] is not None:
            kind = "at tx time" if r["amount_kind"] == "at_tx" else "current balance"
            t += f"Amount: {r['amount']:,.2f} ({kind})" + NL
        t += f"Tx: {r['tx_hash']}" + NL + f"Time: {r['ts']}"
        try:
            _send(t)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram: %s", e)
            continue
        conn.execute("INSERT INTO alerts(chain,tx_id,kind,sent_at) VALUES(?,?,?,?)",
                     (r["source"], r["rid"], "freeze", iso(now_ts())))
        sent += 1
    for ch in auth_changes:
        try:
            _send(f"WARNING: SPL multisig freeze authority changed: {ch['source']}" + NL
                  + f"Was: {ch['old']}" + NL + f"Now: {ch['new']}")
            sent += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram: %s", e)
    auth_changes.clear() if hasattr(auth_changes, "clear") else None
    conn.commit()
    return sent


def process_crosschain(conn, before_pairs, max_age_days=3):
    """Нова поява адреси в мережі (пропозиція або виконання): повідомляє, де ще вона є і де на ній ще лежить USDT."""
    if not configured():
        return 0
    from . import crosschain
    from .util import parse_iso
    sent = 0
    now = crosschain.collect(conn)
    cutoff = now_ts() - max_age_days * 86400
    for addr, items in now.items():
        have = {it["chain"] for it in items}
        for ch in have:
            if (addr, ch) in before_pairs:
                continue
            ts = max((parse_iso(it["ts"]) or 0) for it in items if it["chain"] == ch)
            if ts < cutoff:
                continue
            key = int(addr[2:16], 16)
            kind = "xchain:" + ch
            if conn.execute("SELECT 1 FROM alerts WHERE chain='xchain' AND tx_id=? AND kind=?", (key, kind)).fetchone():
                continue
            elsewhere = crosschain.balances_elsewhere(addr, have)
            lines = [f"Address {addr} first seen on {ch}", "Already on: " + ", ".join(sorted(have))]
            if elsewhere:
                lines.append("not frozen there yet, but holds USDT: " + ", ".join(f"{c} {b:,.2f}" for c, b in elsewhere.items()))
            try:
                _send(NL.join(lines))
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram: %s", e)
                continue
            conn.execute("INSERT INTO alerts(chain,tx_id,kind,sent_at) VALUES('xchain',?,?,?)", (key, kind, iso(now_ts())))
            sent += 1
    conn.commit()
    return sent
