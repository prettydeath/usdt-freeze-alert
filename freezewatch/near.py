"""NEAR: token usdt.tether-token.near, owner - multisafe multisig tether.multisafe.near (2 of 3).
Queue: view list_request_ids / get_request (RPC). History and time: nearblocks API (add_request_and_confirm / confirm /
multisafe call -> token). request_id = sequence number of a successful add_request(_and_confirm) by time
(calibrated against open requests via get_request)."""
import base64
import json
import logging

from .constants import USDT_DECIMALS
from .db import add_event, ensure_proposal, meta_get, meta_set, update_proposal
from .derive import compute_status
from .util import HttpError, http_json, iso

log = logging.getLogger("freezewatch.near")
CHAIN = "near"
TOKEN = "usdt.tether-token.near"
MULTISIG = "tether.multisafe.near"
REQUIRED = 2
RPC = "https://rpc.mainnet.near.org"
API = "https://api.nearblocks.io/v1/account/" + MULTISIG + "/txns"
FUNC_MAP = {"add_to_blacklist": "addBlackList", "remove_from_blacklist": "removeBlackList",
            "destroy_black_funds": "destroyBlackFunds", "mint": "mint"}


def view(method, args=None, account=MULTISIG):
    p = {"request_type": "call_function", "finality": "final", "account_id": account, "method_name": method,
         "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode()}
    o = http_json("POST", RPC, json={"jsonrpc": "2.0", "id": 1, "method": "query", "params": p}, interval=0.3,
                  retries=5, validate=lambda x: "result" in x or "error" in x)
    res = o.get("result")
    if not isinstance(res, dict) or "result" not in res:
        raise HttpError(f"NEAR view {method}: {str(o)[:150]}")
    return json.loads(bytes(res["result"]).decode())


def decode_action(action):
    """request actions[0] -> (function, target, amount). args - base64(JSON)."""
    m = action.get("method_name")
    try:
        a = json.loads(base64.b64decode(action.get("args") or "").decode() or "{}")
    except Exception:  # noqa: BLE001
        a = {}
    target = (a.get("account_id") or "").lower() or None
    amt = int(a["amount"]) / 10 ** USDT_DECIMALS if m == "mint" and str(a.get("amount", "")).isdigit() else None
    return FUNC_MAP.get(m, m), target, amt


def parse_history(txns):
    """nearblocks transactions -> (creations, confirms, executions), all ordered by time.
    creations: {ts, tx, signer, receiver, function, target, amount}; confirms: {ts, tx, signer, request_id};
    executions: {ts, tx, function, target}."""
    creations, confirms, execs, seen = [], [], [], set()
    for t in txns:
        if not (t.get("receipt_outcome") or {}).get("status"):
            continue
        acts = t.get("actions") or []
        if not acts:
            continue
        a = acts[0]
        ts = int(t["receipt_block"]["block_timestamp"] if t.get("receipt_block") else t["block_timestamp"]) // 10 ** 9
        if t["receiver_account_id"] == MULTISIG and a.get("method") in ("add_request_and_confirm", "add_request"):
            key = ("c", t["transaction_hash"])
            if key in seen:
                continue
            seen.add(key)
            try:
                req = json.loads(a["args"])["request"]
                act = req["actions"][0]
            except Exception:  # noqa: BLE001
                continue
            fn, target, amt = decode_action(act)
            creations.append({"ts": int(t["block_timestamp"]) // 10 ** 9, "tx": t["transaction_hash"],
                              "signer": t["predecessor_account_id"], "receiver": req.get("receiver_id"),
                              "function": fn, "target": target, "amount": amt})
        elif t["receiver_account_id"] == MULTISIG and a.get("method") == "confirm":
            key = ("f", t["transaction_hash"])
            if key in seen:
                continue
            seen.add(key)
            try:
                rid = int(json.loads(a["args"])["request_id"])
            except Exception:  # noqa: BLE001
                continue
            confirms.append({"ts": int(t["block_timestamp"]) // 10 ** 9, "tx": t["transaction_hash"],
                             "signer": t["predecessor_account_id"], "request_id": rid})
        elif t["predecessor_account_id"] == MULTISIG and t["receiver_account_id"] == TOKEN:
            try:
                args = json.loads(a.get("args") or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            tgt = (args.get("account_id") or "").lower() or None
            execs.append({"ts": ts, "tx": t["transaction_hash"], "function": FUNC_MAP.get(a.get("method"), a.get("method")),
                          "target": tgt})
    for lst in (creations, confirms, execs):
        lst.sort(key=lambda x: x["ts"])
    return creations, confirms, execs


def match_executions(creations, execs):
    """Execution -> creation: the earliest not-yet-executed request with the same (function, target) before the execution time."""
    used, out = set(), {}
    for e in execs:
        for i, c in enumerate(creations):
            if i in used or c["ts"] > e["ts"]:
                continue
            if c["function"] == e["function"] and c["target"] == e["target"]:
                used.add(i)
                out[i] = e
                break
    return out


def executions_by_confirm(creations, confirms, execs):
    """creation index -> execution. An execution (the multisig calling the token) is a receipt of the confirm transaction
    that reached the threshold: the same transaction hash gives the exact request_id (the listing no longer carries call
    arguments, so matching by function/target is only the fallback)."""
    rid_by_tx = {c["tx"]: c["request_id"] for c in confirms}
    out, used = {}, set()
    for e in execs:
        rid = rid_by_tx.get(e["tx"])
        if rid is not None and rid < len(creations) and rid not in out:
            out[rid] = e
            used.add(id(e))
    rest = [e for e in execs if id(e) not in used]
    free = [(i, c) for i, c in enumerate(creations) if i not in out]
    sub = match_executions([c for _i, c in free], rest)
    for j, e in sub.items():
        out[free[j][0]] = e
    return out


def fetch_txns(max_pages=80):
    rows, cur = [], None
    for _ in range(max_pages):
        p = {"per_page": 50}
        if cur:
            p["cursor"] = cur
        o = http_json("GET", API, params=p, interval=1.0, retries=5, validate=lambda x: "txns" in x)
        rows += o["txns"]
        cur = o.get("cursor")
        if not o["txns"] or not cur:
            break
    return rows


DETAIL = "https://api.nearblocks.io/v1/txns/"


def fill_args(conn, txns, budget=300):
    """The nearblocks account listing no longer carries call arguments (args=None): fetch them per transaction from the
    detail endpoint and cache them in `near_args` (arguments never change, so each transaction is fetched once)."""
    import time
    t0 = time.time()
    def wanted(t):
        a0 = (t.get("actions") or [{}])[0]
        if a0.get("args") is not None:
            return False
        if t.get("receiver_account_id") == MULTISIG:
            return a0.get("method") in ("add_request_and_confirm", "add_request", "confirm")
        return t.get("predecessor_account_id") == MULTISIG and t.get("receiver_account_id") == TOKEN  # the executed call
    need = [t for t in txns if wanted(t)]
    got = 0
    for t in need:
        h = t["transaction_hash"]
        r = conn.execute("SELECT args FROM near_args WHERE tx=?", (h,)).fetchone()
        args = r["args"] if r else None
        if args is None:
            if time.time() - t0 > budget:
                continue
            try:
                o = http_json("GET", DETAIL + h, interval=2.5, retries=5, validate=lambda x: "txns" in x)
                args = ((o["txns"] or [{}])[0].get("actions") or [{}])[0].get("args")
            except Exception as e:  # noqa: BLE001
                log.info("NEAR: args of %s unavailable (%s)", h[:8], str(e)[:60])
                continue
            if args:
                conn.execute("INSERT OR IGNORE INTO near_args(tx,args) VALUES(?,?)", (h, args))
                got += 1
        if args:
            t["actions"][0]["args"] = args
    if got:
        conn.commit()
        log.info("NEAR: fetched call arguments of %s transactions", got)


def sync(conn, stale_days=7):
    txns = fetch_txns()
    fill_args(conn, txns)
    creations, confirms, execs = parse_history(txns)
    ex_of = executions_by_confirm(creations, confirms, execs)
    open_ids = set(view("list_request_ids"))
    nonce = view("get_request_nonce")
    log.info("NEAR: creations %s, confirms %s, executions %s, open %s, nonce %s", len(creations), len(confirms),
             len(execs), sorted(open_ids), nonce)
    # calibration: open requests must match the expected rank
    ok = True
    for rid in open_ids:
        if rid >= len(creations):
            ok = False
            continue
        r = view("get_request", {"request_id": rid})
        fn, tg, _ = decode_action(r["actions"][0])
        if (fn, tg) != (creations[rid]["function"], creations[rid]["target"]):
            ok = False
    meta_set(conn, "near_mapping_ok", int(ok))
    if not ok:
        log.warning("NEAR: request_id->creation mapping does not match on-chain, data not written")
        return
    conf_by = {}
    for c in confirms:
        conf_by.setdefault(c["request_id"], []).append(c)
    for rid, c in enumerate(creations):
        ensure_proposal(conn, CHAIN, rid)
        e = ex_of.get(rid)
        sub = iso(c["ts"])
        signers = {c["signer"]} | {x["signer"] for x in conf_by.get(rid, [])}
        executed = e is not None
        conf = len(signers) if not executed else max(len(signers), REQUIRED)
        if executed:
            status = "executed"
        elif rid in open_ids:
            status = compute_status(False, False, conf, False, sub, stale_days)
        else:
            status = "revoked"  # not in the open list and not executed: deleted
        update_proposal(conn, CHAIN, rid, destination=c["receiver"], function=c["function"], target=c["target"],
                        amount=c["amount"], raw_data=None, submitted_at=sub, submit_tx=c["tx"], submitter=c["signer"],
                        executed=int(executed), executed_at=iso(e["ts"]) if e else None, exec_tx=e["tx"] if e else None,
                        confirmations=conf, required=REQUIRED, status=status,
                        delay_seconds=max(0, e["ts"] - c["ts"]) if e else None, chain_verified=1)
        add_event(conn, CHAIN, rid, c["signer"], "submit", sub, c["tx"])
        for x in conf_by.get(rid, []):
            add_event(conn, CHAIN, rid, x["signer"], "confirm", iso(x["ts"]), x["tx"])
    conn.commit()
