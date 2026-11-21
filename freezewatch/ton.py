"""TON: jetton USDT, адмін - multisig_v2 2 із 3 (EQBkQP48...). Ордери та підтвердження - tonapi /v2/multisig/{admin}.
Тіло дії (call_to, op 0x235caf52) не розбирається: зберігаємо як "governance_call" без цілі; час виконання tonapi
не дає, тож затримка N/A (виконання = прапор sent_for_execution)."""
import logging
import os

from .db import add_event, ensure_proposal, update_proposal
from .derive import compute_status
from .util import http_json, iso

log = logging.getLogger("freezewatch.ton")
CHAIN = "ton"
JETTON = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
ADMIN = "EQBkQP48aUEDg5Y5RRc8SxFHm_C5tNcJDlh3e9pYHC-ZmG2M"
API = "https://tonapi.io/v2/multisig/" + ADMIN


def rows_from_multisig(j, stale_days=7, now=None):
    """Чиста функція: відповідь tonapi -> список полів для proposals."""
    out = []
    for o in j.get("orders", []):
        sub = iso(o["creation_date"]) if o.get("creation_date") else None
        conf = int(o.get("approvals_num", 0))
        required = int(o.get("threshold") or j.get("threshold") or 2)
        executed = bool(o.get("sent_for_execution"))
        exp = o.get("expiration_date")
        import time
        expired = (not executed) and exp and exp < (now or time.time())
        status = "executed" if executed else ("revoked" if expired else compute_status(False, False, conf, False, sub, stale_days, now))
        out.append({"tx_id": int(o["order_seqno"]),
                    "vals": dict(destination=o["address"], function="governance_call", target=None, amount=None,
                                 submitted_at=sub, executed=int(executed), confirmations=conf, required=required,
                                 status=status, chain_verified=1),
                    "signers": o.get("signed_by") or []})
    return out


def sync(conn, stale_days=7):
    h = {}
    if os.environ.get("TONAPI_KEY"):
        h["Authorization"] = "Bearer " + os.environ["TONAPI_KEY"]
    j = http_json("GET", API, headers=h, interval=1.0, retries=5, validate=lambda x: "orders" in x)
    for r in rows_from_multisig(j, stale_days):
        ensure_proposal(conn, CHAIN, r["tx_id"])
        update_proposal(conn, CHAIN, r["tx_id"], **r["vals"])
        for s in r["signers"]:
            if r["vals"]["submitted_at"]:
                add_event(conn, CHAIN, r["tx_id"], s, "confirm", r["vals"]["submitted_at"], "")
    conn.commit()
    log.info("TON: ордерів %s", len(j.get("orders", [])))
