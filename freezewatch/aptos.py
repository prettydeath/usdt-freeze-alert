"""Aptos: USDt (package 0xf73e...::usdt), admin = 0x1::multisig_account 2-of-3.
Source of truth: the multisig `transactions` table history in the Aptos Indexer (table_items). Each sequence_number
is written at creation (payload, creator, creation_time_secs), re-written on every vote and deleted on execution
(decoded_value = null). So creation/votes/execution are matched by sequence_number exactly - no ordering guesses -
and the BCS payload (target address) is available for executed transactions too."""
import logging

from .db import add_event, ensure_proposal, update_proposal
from .derive import compute_status
from .util import http_json, iso, parse_iso

log = logging.getLogger("freezewatch.aptos")
CHAIN = "aptos"
ADMIN = "0x76ff3deac6723625606b11dfca7817fe1266fffcfc1fb26f5ccc9411f3def6fd"
PKG = "0xf73e887a8754f540ee6e1a93bdc6dde2af69fc7ca5de32013e89dd44244473cb"
REST = "https://api.mainnet.aptoslabs.com/v1"
GQL = "https://api.mainnet.aptoslabs.com/v1/graphql"
Q = """query($a:String,$o:Int){ account_transactions(where:{account_address:{_eq:$a}},
 order_by:{transaction_version:asc}, limit:100, offset:$o){ transaction_version
 user_transaction{ timestamp sender entry_function_id_str } } }"""
CREATE_FUNCS = ("0x1::multisig_account::create_transaction", "0x1::multisig_account::create_transaction_with_hash")


def uleb(b, i):
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7


def decode_payload(hexstr):
    """BCS MultisigTransactionPayload::EntryFunction -> {module, function, args:[bytes]} (or None)."""
    b = bytes.fromhex(hexstr.removeprefix("0x"))
    v, i = uleb(b, 0)
    if v != 0:
        return None
    addr = "0x" + b[i:i + 32].hex()
    i += 32
    n, i = uleb(b, i)
    mod = b[i:i + n].decode()
    i += n
    n, i = uleb(b, i)
    fn = b[i:i + n].decode()
    i += n
    nt, i = uleb(b, i)
    if nt:
        return {"module": f"{addr}::{mod}", "function": fn, "args": [], "note": "type args not decoded"}
    na, i = uleb(b, i)
    args = []
    for _ in range(na):
        ln, i = uleb(b, i)
        args.append(b[i:i + ln])
        i += ln
    return {"module": f"{addr}::{mod}", "function": fn, "args": args}


def addresses_arg(arg):
    """BCS vector<address>."""
    n, i = uleb(arg, 0)
    if n > 256 or i + 32 * n != len(arg):
        raise ValueError("not a vector<address>")
    return ["0x" + arg[i + 32 * k:i + 32 * (k + 1)].hex() for k in range(n)]


ITEMS_Q = """query($h:String,$o:Int){ table_items(where:{table_handle:{_eq:$h}},
 order_by:{transaction_version:asc, write_set_change_index:asc}, limit:100, offset:$o){
 transaction_version decoded_key decoded_value } }"""
TS_Q = """query($v:[bigint!]){ user_transactions(where:{version:{_in:$v}}){ version timestamp sender
 entry_function_id_str } }"""


def _gql(query, variables):
    return http_json("POST", GQL, json={"query": query, "variables": variables}, interval=0.5, retries=5,
                     validate=lambda x: "data" in x)["data"]


def fetch_items(handle):
    rows, off = [], 0
    while True:
        part = _gql(ITEMS_Q, {"h": handle, "o": off})["table_items"]
        rows += part
        if len(part) < 100:
            return rows
        off += 100


def fetch_timestamps(versions):
    out = {}
    versions = sorted(set(versions))
    for i in range(0, len(versions), 100):
        for r in _gql(TS_Q, {"v": versions[i:i + 100]})["user_transactions"]:
            out[int(r["version"])] = {"ts": _ts(r["timestamp"]), "sender": r["sender"], "fn": r["entry_function_id_str"]}
    return out


def _ts(s):
    return parse_iso(s[:19] + "Z")


def build_from_items(items):
    """table_items -> {seq: {create_version, creator, created, payload, votes, delete_version}}.
    A row with decoded_value == None is the deletion = execution (or rejection) of that sequence."""
    seqs = {}
    for it in items:
        seq = int(it["decoded_key"])
        v = it["decoded_value"]
        s = seqs.setdefault(seq, {"create_version": int(it["transaction_version"]), "delete_version": None,
                                  "votes": 0, "payload": None, "creator": None, "created": None})
        if v is None:
            s["delete_version"] = int(it["transaction_version"])
            continue
        s["creator"] = v.get("creator")
        s["created"] = int(v["creation_time_secs"])
        pl = (v.get("payload") or {}).get("vec") or []
        s["payload"] = pl[0] if pl else None
        s["votes"] = len([x for x in v.get("votes", {}).get("data", []) if x.get("value")])
    return seqs


def decode_target(payload_hex):
    """(function, target, raw) from BCS payload of a multisig transaction."""
    if not payload_hex:
        return "unknown", None
    d = decode_payload(payload_hex)
    if not d:
        return "unknown", None
    target = None
    if d["args"] and d["function"] in ("freeze_accounts", "unfreeze_accounts"):
        try:
            target = addresses_arg(d["args"][0])[0]
        except Exception:  # noqa: BLE001
            target = None
    elif d["args"] and d["function"] == "burn_from" and len(d["args"][0]) == 32:
        target = "0x" + d["args"][0].hex()
    return d["function"], target


def sync(conn, stale_days=7):
    res = http_json("GET", f"{REST}/accounts/{ADMIN}/resource/0x1::multisig_account::MultisigAccount", interval=0.5,
                    validate=lambda x: "data" in x)["data"]
    required = int(res["num_signatures_required"])
    seqs = build_from_items(fetch_items(res["transactions"]["handle"]))
    dels = [s["delete_version"] for s in seqs.values() if s["delete_version"]]
    tsmap = fetch_timestamps(dels)
    log.info("Aptos: %s multisig transactions, %s executed/removed", len(seqs), len(dels))
    for seq, s in sorted(seqs.items()):
        if s["created"] is None:
            continue
        ensure_proposal(conn, CHAIN, seq)
        fn, target = decode_target(s["payload"])
        sub = iso(s["created"])
        vals = dict(destination=PKG + "::usdt", function=fn, target=target, raw_data=s["payload"], submitted_at=sub,
                    submitter=s["creator"], required=required, chain_verified=1)
        if s["delete_version"]:
            e = tsmap.get(s["delete_version"])
            # the deletion row is written by the executing tx (usdt::* call) or by execute_rejected_transaction
            rejected = bool(e) and e["fn"] == "0x1::multisig_account::execute_rejected_transaction"
            vals.update(executed=0 if rejected else 1, exec_tx=str(s["delete_version"]),
                        confirmations=s["votes"] if rejected else max(s["votes"], required),
                        status="revoked" if rejected else "executed")
            if e and not rejected:
                vals.update(executed_at=iso(e["ts"]), delay_seconds=max(0, e["ts"] - s["created"]), executor=e["sender"])
        else:
            vals.update(executed=0, confirmations=s["votes"],
                        status=compute_status(False, False, s["votes"], False, sub, stale_days))
        update_proposal(conn, CHAIN, seq, **vals)
        add_event(conn, CHAIN, seq, s["creator"] or "", "submit", sub, str(s["create_version"]))
    conn.commit()
