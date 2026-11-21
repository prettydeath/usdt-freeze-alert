"""USDT0 (Tier B): Safe 3/5. Черга видима ЛИШЕ у Safe Transaction Service (поза ланцюгом): підписанти можуть
її не використовувати. Для кожної мережі беремо submissionDate / confirmations / executionDate, рахуємо затримку;
затримка 0 (<= 60 с) - ознака "не опубліковано заздалегідь"."""
import logging

from . import abi
from .constants import USDT_SELECTORS
from .db import add_event, ensure_proposal, update_proposal
from .derive import compute_status
from .registry import SAFE_CHAINS
from .util import env, http_json, now_ts, parse_iso

log = logging.getLogger("freezewatch.safe")
BASE = "https://api.safe.global/tx-service"
NOT_ADVANCE_SEC = 60


def _headers():
    k = env("SAFE_API_KEY")
    return {"Authorization": f"Bearer {k}"} if k else {}


def _get(url, params=None):
    return http_json("GET", url, params=params, headers=_headers(), interval=0.4, retries=6,
                     validate=lambda o: isinstance(o, dict))


def _iso(s):
    return s[:19] + "Z" if s else None


def extract_calls(t, token):
    """Виклики до токена всередині Safe-транзакції (включно з multiSend). -> [{idx,data,method}]"""
    dd = t.get("dataDecoded") or {}
    calls = []
    if dd.get("method") == "multiSend":
        try:
            inner = dd["parameters"][0]["valueDecoded"] or []
        except (KeyError, IndexError, TypeError):
            inner = []
        for c in inner:
            calls.append({"to": c.get("to"), "data": c.get("data")})
    else:
        calls.append({"to": t.get("to"), "data": t.get("data")})
    out = []
    for n, c in enumerate(calls):
        if c["to"] and c["to"].lower() == token.lower() and c.get("data"):
            out.append({"idx": n, "data": c["data"]})
    return out


def row_from_safe_tx(t, token, safe_nonce, stale_days=7, now=None):
    """Чиста функція (тестується офлайн): Safe-транзакція -> список полів пропозицій для таблиці proposals."""
    out = []
    confs = t.get("confirmations") or []
    sub = _iso(t.get("submissionDate"))
    exe = _iso(t.get("executionDate"))
    executed = bool(t.get("isExecuted"))
    ok = executed and t.get("isSuccessful") is not False
    for c in extract_calls(t, token):
        data = c["data"].removeprefix("0x")
        dec = abi.decode_calldata(data, True, lambda h: "0x" + h)
        delay = (parse_iso(exe) - parse_iso(sub)) if (executed and exe and sub) else None
        if executed:
            status = "executed" if ok else "failed"
        elif safe_nonce is not None and t["nonce"] < safe_nonce:
            status = "revoked"  # витіснено: nonce уже зайнятий іншою транзакцією
        else:
            status = compute_status(False, False, len(confs), False, sub, stale_days, now)
        out.append({
            "tx_id": t["nonce"] * 100 + c["idx"],
            "vals": dict(destination=token.lower(), function=dec["function"], target=dec["target"],
                         amount=dec["amount"], raw_data="0x" + data, submitted_at=sub, submit_tx=t.get("safeTxHash"),
                         submitter=(t.get("proposer") or None), executed=int(executed and ok),
                         executed_at=exe if executed else None, exec_tx=t.get("transactionHash"),
                         executor=t.get("executor"), confirmations=len(confs),
                         required=t.get("confirmationsRequired"), status=status,
                         delay_seconds=max(0, delay) if delay is not None else None, chain_verified=1),
            "events": [("submit", t.get("proposer") or "", sub)] +
                      [("confirm", cf["owner"], _iso(cf["submissionDate"])) for cf in confs
                       if cf["owner"] != t.get("proposer")],
        })
    return out


def sync_chain(conn, chain, stale_days=7, full=False):
    cfg = SAFE_CHAINS[chain]
    net = chain
    safe = cfg["safe"]
    info = _get(f"{BASE}/{net}/api/v1/safes/{safe}/")
    safe_nonce = int(info["nonce"])
    url = f"{BASE}/{net}/api/v1/safes/{safe}/multisig-transactions/"
    have_any = conn.execute("SELECT 1 FROM proposals WHERE chain=? LIMIT 1", (chain,)).fetchone()
    pages = []
    # відкриті - завжди; історія - повністю при першому запуску, далі лише свіжа сторінка
    pages.append(_get(url, {"executed": "false", "ordering": "-nonce", "limit": 100}))
    o = _get(url, {"ordering": "-nonce", "limit": 100})
    pages.append(o)
    while (full or not have_any) and o.get("next"):
        o = _get(o["next"])
        pages.append(o)
    n = 0
    seen = set()
    for pg in pages:
        for t in pg.get("results", []):
            if t["nonce"] in seen and not t.get("isExecuted"):
                pass
            seen.add(t["nonce"])
            for r in row_from_safe_tx(t, cfg["token"], safe_nonce, stale_days):
                ensure_proposal(conn, chain, r["tx_id"])
                update_proposal(conn, chain, r["tx_id"], **r["vals"])
                for kind, signer, ts in r["events"]:
                    if ts:
                        add_event(conn, chain, r["tx_id"], signer, kind, ts, t.get("safeTxHash") or "")
                n += 1
    conn.commit()
    log.info("%s: Safe-транзакцій до токена оброблено %s (nonce safe=%s)", cfg["label"], n, safe_nonce)
