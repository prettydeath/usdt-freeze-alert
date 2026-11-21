"""TON: jetton USDT, admin = multisig_v2 2-of-3 (EQBkQP48...). Orders and approvals come from tonapi
(/v2/multisig/{admin}); real timestamps come from the order contract's own transactions on toncenter v3:
  first tx   = order creation (new_order from the multisig),
  later txs  = approvals (in_msg.source = signer),
  the tx that emits a message to the multisig = threshold reached = execution trigger.
The order body is decoded from toncenter's `decoded` field: a call_to (op 0x235caf52) whose inner message is
jetton set_status (op 0xeed236d3, status 1 = frozen, 0 = unfrozen) is reported as freeze/unfreeze of that jetton wallet."""
import base64
import logging
import os
import time

from .db import add_event, ensure_proposal, update_proposal
from .derive import compute_status
from .util import http_json, iso, key_pool

log = logging.getLogger("freezewatch.ton")
CHAIN = "ton"
JETTON = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
ADMIN = "EQBkQP48aUEDg5Y5RRc8SxFHm_C5tNcJDlh3e9pYHC-ZmG2M"
ADMIN_RAW = "0:6440fe3c69410383963945173c4b11479bf0b9b4d7090e58777bda581c2f9998"
API = "https://tonapi.io/v2/multisig/" + ADMIN
TONCENTER = "https://toncenter.com/api/v3/transactions"
OP_CALL_TO = 0x235CAF52
OP_SET_STATUS = 0xEED236D3


# ---------- minimal BOC / cell reader ----------
def parse_boc(b: bytes):
    """Bag of cells -> list of (data_bits:str, refs:list[int]); root is index 0 of the returned root list."""
    if b[:4] != bytes.fromhex("b5ee9c72"):
        raise ValueError("not a BOC")
    flags = b[4]
    size = flags & 7
    has_idx = flags & 0x80
    off = b[5]
    n = int.from_bytes(b[6:6 + size], "big")
    r = int.from_bytes(b[6 + size:6 + 2 * size], "big")
    pos = 6 + 3 * size + off
    roots = [int.from_bytes(b[pos + i * size:pos + (i + 1) * size], "big") for i in range(r)]
    pos += r * size
    if has_idx:
        pos += n * off
    cells = []
    for _ in range(n):
        d1, d2 = b[pos], b[pos + 1]
        pos += 2
        refs = d1 & 7
        ln = (d2 + 1) // 2
        data = b[pos:pos + ln]
        pos += ln
        bits = "".join(f"{x:08b}" for x in data)
        if d2 & 1:  # incomplete last byte: strip the completion tag
            bits = bits.rstrip("0")[:-1]
        cells.append([bits, [int.from_bytes(b[pos + i * size:pos + (i + 1) * size], "big") for i in range(refs)]])
        pos += refs * size
    return cells, roots


def decode_call_to(body_b64: str):
    """-> {"dest": raw address, "inner_op": int, "status": int|None} for a call_to body, else None."""
    cells, roots = parse_boc(base64.b64decode(body_b64))
    bits, refs = cells[roots[0]]
    if len(bits) < 96 + 267 or int(bits[:32], 2) != OP_CALL_TO:
        return None
    a = bits[96:]
    if a[:2] != "10":
        return None
    wc = int(a[3:11], 2)
    wc = wc - 256 if wc > 127 else wc
    dest = f"{wc}:{int(a[11:267], 2):064x}"
    out = {"dest": dest, "inner_op": None, "status": None}
    if refs:
        ibits = cells[refs[0]][0]
        k = ibits.find(f"{OP_SET_STATUS:032b}")
        if k >= 0:
            out["inner_op"] = OP_SET_STATUS
            s = ibits[k + 32 + 64:k + 32 + 68]
            out["status"] = int(s, 2) if len(s) == 4 else None
        elif len(ibits) >= 32:
            out["inner_op"] = int(ibits[:32], 2)
    return out


def find_bodies(obj):
    """All order message bodies (base64) in toncenter's decoded order."""
    found = []
    if isinstance(obj, dict):
        b = obj.get("body")
        if isinstance(b, dict) and b.get("@type") == "first_from_either" and isinstance(b.get("value"), str):
            found.append(b["value"])
        for v in obj.values():
            found += find_bodies(v)
    elif isinstance(obj, list):
        for v in obj:
            found += find_bodies(v)
    return found


def describe_order(init_decoded):
    """-> (function, target) from the decoded new_order message."""
    for body in find_bodies(init_decoded.get("order") or {}):
        try:
            d = decode_call_to(body)
        except Exception:  # noqa: BLE001
            continue
        if not d:
            continue
        if d["inner_op"] == OP_SET_STATUS and d["status"] is not None:
            return ("freeze_wallet" if d["status"] else "unfreeze_wallet"), d["dest"]
        return (f"call_to 0x{d['inner_op']:08x}" if d["inner_op"] is not None else "call_to"), d["dest"]
    return "governance_call", None


# ---------- order facts ----------
def order_facts(txs, multisig_raw=ADMIN_RAW):
    """Order-contract transactions (asc) -> (created, executed_at, executed_tx, approvals[(signer, ts, hash)], init_decoded)."""
    created = exec_ts = exec_tx = None
    approvals, init = [], None
    for t in txs:
        ts = int(t["now"])
        msg = t.get("in_msg") or {}
        if created is None:
            created = ts
            init = (msg.get("message_content") or {}).get("decoded") or {}
        elif msg.get("source"):
            approvals.append((msg["source"].lower(), ts, t.get("hash")))
        if exec_ts is None and any((m.get("destination") or "").lower() == multisig_raw for m in t.get("out_msgs") or []):
            exec_ts, exec_tx = ts, t.get("hash")
    return created, exec_ts, exec_tx, approvals, init or {}


def rows_from_multisig(j, stale_days=7, now=None):
    """Pure: tonapi response -> proposal fields without timestamps beyond creation_date."""
    out = []
    for o in j.get("orders", []):
        sub = iso(o["creation_date"]) if o.get("creation_date") else None
        conf = int(o.get("approvals_num", 0))
        required = int(o.get("threshold") or j.get("threshold") or 2)
        executed = bool(o.get("sent_for_execution"))
        exp = o.get("expiration_date")
        expired = (not executed) and exp and exp < (now or time.time())
        status = "executed" if executed else ("revoked" if expired else compute_status(False, False, conf, False, sub, stale_days, now))
        out.append({"tx_id": int(o["order_seqno"]), "address": o["address"],
                    "vals": dict(destination=o["address"], function="governance_call", target=None, amount=None,
                                 submitted_at=sub, executed=int(executed), confirmations=conf, required=required,
                                 status=status, chain_verified=1),
                    "signers": o.get("signed_by") or []})
    return out


def sync(conn, stale_days=7):
    pool = key_pool("TONAPI_KEY")  # comma-separated list allowed; rotated per request

    def hdr():
        k = pool.next()
        return {"Authorization": "Bearer " + k} if k else {}
    j = http_json("GET", API, headers=hdr(), interval=1.0, retries=5, validate=lambda x: "orders" in x)
    rows = rows_from_multisig(j, stale_days)
    log.info("TON: %s orders", len(rows))
    for r in rows:
        ensure_proposal(conn, CHAIN, r["tx_id"])
        cur = conn.execute("SELECT executed_at, function, status FROM proposals WHERE chain=? AND tx_id=?",
                           (CHAIN, r["tx_id"])).fetchone()
        vals = dict(r["vals"])
        done = cur and cur["executed_at"] and cur["function"] != "governance_call"
        if not done:
            try:
                txs = http_json("GET", TONCENTER, params={"account": r["address"], "limit": 50, "sort": "asc"},
                                interval=1.2, retries=5, validate=lambda x: "transactions" in x)["transactions"]
                created, exec_ts, exec_tx, approvals, init = order_facts(txs)
                if created:
                    vals["submitted_at"] = iso(created)
                fn, target = describe_order(init)
                vals.update(function=fn, target=target)
                if exec_ts and vals["executed"]:
                    vals.update(executed_at=iso(exec_ts), exec_tx=exec_tx,
                                delay_seconds=max(0, exec_ts - (created or exec_ts)))
                for signer, ts, hx in approvals:
                    add_event(conn, CHAIN, r["tx_id"], signer, "confirm", iso(ts), hx or "")
            except Exception as e:  # noqa: BLE001
                log.warning("TON order %s: toncenter failed (%s)", r["tx_id"], str(e)[:80])
        else:
            vals.pop("function", None)
            vals.pop("target", None)
            vals.pop("submitted_at", None)
        update_proposal(conn, CHAIN, r["tx_id"], **vals)
    conn.commit()
