import json

import pytest

from freezewatch import aptos, constants as C, crosschain, near, safe, ton
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.registry import MSW_CHAINS, SAFE_CHAINS, chain_meta
from freezewatch.util import tron_from_hex20

ARB_TOKEN = SAFE_CHAINS["arb1"]["token"]

# Shortened copy of a real Safe transaction (Arbitrum, nonce 185, 2026-11-09).
SAFE_TX = {
    "safe": "0x4DFF9b5b0143E642a3F63a5bcf2d1C328e600bf8", "to": ARB_TOKEN, "nonce": 185,
    "data": "0x3c7c9b90000000000000000000000000e485e7a70b1131216cb47ca4555c4bbb2c742478",
    "executionDate": "2026-11-09T01:35:56Z", "submissionDate": "2026-11-09T00:44:48.872739Z",
    "transactionHash": "0xc059fb1b2885c6aa0c775bf49981ff0a8af3d95f45ed39417cb7ced32e001d40",
    "safeTxHash": "0x8d2c016ed5e7b0d21a86c18d095d946398571dbc3849779972582e0a785a148f",
    "proposer": "0x00F6D2b4B69Ce697f913Da16A9D73283dc4C78F2", "executor": "0x46565eeDB0bb948aE787dD105cCE300f736dA28e",
    "isExecuted": True, "isSuccessful": True, "confirmationsRequired": 3,
    "dataDecoded": {"method": "addToBlockedList"},
    "confirmations": [{"owner": "0x4192D72c281dB0eF10464dD274EE583C33256ac5", "submissionDate": "2026-11-09T00:46:40.646183Z"},
                      {"owner": "0x00F6D2b4B69Ce697f913Da16A9D73283dc4C78F2", "submissionDate": "2026-11-09T00:44:48.9Z"},
                      {"owner": "0x1111111111111111111111111111111111111111", "submissionDate": "2026-11-09T01:30:00Z"}],
}


def test_safe_row_executed_delay():
    rows = safe.row_from_safe_tx(SAFE_TX, ARB_TOKEN, 186)
    assert len(rows) == 1
    v = rows[0]["vals"]
    assert rows[0]["tx_id"] == 18500
    assert v["function"] == "addToBlockedList" and v["target"] == "0xe485e7a70b1131216cb47ca4555c4bbb2c742478"
    assert v["status"] == "executed" and v["executed"] == 1
    assert v["delay_seconds"] == 3068  # 00:44:48 -> 01:35:56
    assert v["confirmations"] == 3 and v["required"] == 3 and v["submit_tx"].startswith("0x8d2c")


def test_safe_pending_revoked_and_multisend():
    pend = {**SAFE_TX, "isExecuted": False, "executionDate": None, "transactionHash": None,
            "confirmations": SAFE_TX["confirmations"][:1], "nonce": 190}
    assert safe.row_from_safe_tx(pend, ARB_TOKEN, 190, now=1794000000)[0]["vals"]["status"] in ("pending", "stale")
    assert safe.row_from_safe_tx({**pend, "nonce": 185}, ARB_TOKEN, 186)[0]["vals"]["status"] == "revoked"
    multi = {**SAFE_TX, "to": "0xMultiSend", "data": "0x", "dataDecoded": {"method": "multiSend", "parameters": [
        {"valueDecoded": [{"to": "0xother", "data": "0x12345678"}, {"to": ARB_TOKEN, "data": SAFE_TX["data"]}]}]}}
    r = safe.row_from_safe_tx(multi, ARB_TOKEN, 186)
    assert len(r) == 1 and r[0]["tx_id"] == 18501  # call index inside multiSend
    assert safe.row_from_safe_tx({**SAFE_TX, "to": "0xnot_token"}, ARB_TOKEN, 186) == []


def test_new_style_selectors_and_topics():
    eu = pytest.importorskip("eth_utils")
    for sel, (name, _) in C.USDT_SELECTORS.items():
        assert eu.keccak(text=C.SIG[name])[:4].hex() == sel, name
    assert "0x" + eu.keccak(text="BlockPlaced(address)").hex() == C.TOPIC_BLOCK_PLACED
    assert "0x" + eu.keccak(text="BlockReleased(address)").hex() == C.TOPIC_BLOCK_RELEASED
    assert "0x" + eu.keccak(text="DestroyedBlockedFunds(address,uint256)").hex() == C.TOPIC_DESTROYED_BLOCKED
    assert eu.keccak(text="isBlocked(address)")[:4].hex() == C.SEL_IS_BLOCKED


def test_registry_complete():
    meta = chain_meta()
    for k in list(MSW_CHAINS) + list(SAFE_CHAINS) + ["eth", "tron", "near", "aptos", "ton"]:
        assert k in meta and meta[k]["mech"] and meta[k]["addr"]
    assert meta["arb1"]["mech"] == "safe_api" and meta["avax"]["mech"] == "onchain"


def b64(s):
    import base64
    return base64.b64encode(s.encode()).decode()


def test_near_history_pairing():
    def tx(h, ts, pred, recv, method, args, ok=True):
        return {"transaction_hash": h, "block_timestamp": str(ts * 10 ** 9), "predecessor_account_id": pred,
                "receiver_account_id": recv, "receipt_outcome": {"status": ok},
                "receipt_block": {"block_timestamp": ts * 10 ** 9}, "actions": [{"method": method, "args": args}]}
    req = json.dumps({"request": {"receiver_id": near.TOKEN, "actions": [{
        "method_name": "add_to_blacklist", "args": b64(json.dumps({"account_id": "0x4ad0aaaee18a66b12caff45df515c0047a8dac0c"}))}]}})
    txns = [
        tx("EXEC", 1781640973, near.MULTISIG, near.TOKEN, "add_to_blacklist",
           json.dumps({"account_id": "0x4ad0aaaee18a66b12caff45df515c0047a8dac0c"})),
        tx("CONF", 1781640972, "member2", near.MULTISIG, "confirm", json.dumps({"request_id": 34})),
        tx("CREATE", 1781625826, "member1", near.MULTISIG, "add_request_and_confirm", req),
        tx("CREATE", 1781625826, "member1", near.MULTISIG, "add_request_and_confirm", req),  # duplicate receipt
        tx("FAIL", 1781625000, "member1", near.MULTISIG, "add_request_and_confirm", req, ok=False),
    ]
    cr, cf, ex = near.parse_history(txns)
    assert len(cr) == 1 and len(cf) == 1 and len(ex) == 1
    assert cr[0]["function"] == "addBlackList" and cr[0]["target"] == "0x4ad0aaaee18a66b12caff45df515c0047a8dac0c"
    m = near.match_executions(cr, ex)
    assert m[0]["ts"] - cr[0]["ts"] == 1781640973 - 1781625826  # ~4.2 hours


def test_aptos_bcs_payload():
    addr = bytes.fromhex(aptos.PKG[2:])
    s = lambda x: bytes([len(x)]) + x.encode()
    victim = bytes.fromhex("11" * 32)
    arg = bytes([1]) + victim  # vector<address> with 1 element
    payload = bytes([0]) + addr + s("usdt") + s("freeze_accounts") + bytes([0]) + bytes([1]) + bytes([len(arg)]) + arg
    d = aptos.decode_payload(payload.hex())
    assert d["function"] == "freeze_accounts" and d["module"].endswith("::usdt")
    assert aptos.addresses_arg(d["args"][0]) == ["0x" + "11" * 32]
    assert aptos.decode_payload("01") is None


def test_aptos_table_items_exact_matching():
    """Regression: seq 32 (revoke) created 2026-11-03 22:55:57, executed in version 7891749160 at 03:11:49 (~4h15m);
    seq 31 (freeze_accounts) created 2026-07-23 and executed in version 7891743722 - matched by sequence, not by order."""
    addr = bytes.fromhex(aptos.PKG[2:])
    s = lambda x: bytes([len(x)]) + x.encode()
    victim = bytes.fromhex("48" * 32)
    freeze = bytes([0]) + addr + s("usdt") + s("freeze_accounts") + bytes([0, 1]) + bytes([33]) + bytes([1]) + victim
    revoke = bytes([0]) + addr + s("usdt") + s("revoke") + bytes([0, 1, 8]) + bytes(8)

    def row(ver, key, payload, creator="0xa51", created=0, votes=1):
        if payload is None:
            return {"transaction_version": ver, "decoded_key": str(key), "decoded_value": None}
        return {"transaction_version": ver, "decoded_key": str(key), "decoded_value": {
            "creator": creator, "creation_time_secs": str(created), "payload": {"vec": ["0x" + payload.hex()]},
            "votes": {"data": [{"key": f"0x{n}", "value": True} for n in range(votes)]}}}
    c31, c32 = 1784813889, 1793746557
    items = [row(6416873145, 31, freeze, created=c31), row(7888717485, 32, revoke, created=c32),
             row(7891734231, 31, freeze, created=c31, votes=2), row(7891743722, 31, None),
             row(7891747184, 32, revoke, created=c32, votes=2), row(7891749160, 32, None)]
    seqs = aptos.build_from_items(items)
    assert seqs[31]["delete_version"] == 7891743722 and seqs[32]["delete_version"] == 7891749160
    assert seqs[31]["votes"] == 2 and seqs[31]["created"] == c31
    assert aptos.decode_target(seqs[31]["payload"]) == ("freeze_accounts", "0x" + "48" * 32)
    assert aptos.decode_target(seqs[32]["payload"]) == ("revoke", None)
    # 03:11:49 - 22:55:57 = 4h15m52s -> delay computed in sync() as exec_ts - created
    from freezewatch.util import parse_iso
    assert parse_iso("2026-11-04T03:11:49Z") - c32 == 15352
    with pytest.raises(ValueError):
        aptos.addresses_arg(bytes(8))  # u64 must not be read as vector<address>


def test_ton_orders():
    j = {"threshold": 2, "orders": [
        {"address": "0:e842", "order_seqno": "37", "threshold": 2, "sent_for_execution": True, "approvals_num": 2,
         "creation_date": 1787320775, "expiration_date": 1789912762, "signed_by": ["0:aa"]},
        {"address": "0:63bd", "order_seqno": "38", "threshold": 2, "sent_for_execution": False, "approvals_num": 1,
         "creation_date": 1794000000, "expiration_date": 1796000000, "signed_by": []}]}
    rows = ton.rows_from_multisig(j, now=1794001000)
    assert rows[0]["vals"]["status"] == "executed" and rows[0]["vals"]["function"] == "governance_call"
    assert rows[1]["vals"]["status"] == "pending" and rows[1]["vals"]["confirmations"] == 1


def test_crosschain_tron_matches_evm(tmp_path):
    hex20 = "bca5d8e9fc4e31c72631a144e7f1563736f09061"
    t = tron_from_hex20(hex20)
    assert crosschain.norm_addr("tron", t) == "0x" + hex20
    assert crosschain.norm_addr("eth", "0x" + hex20.upper()) == "0x" + hex20
    assert crosschain.norm_addr("sol_usdc", "Aw6Y") is None
    conn = connect(tmp_path / "x.sqlite")
    for chain, tid, fn, tgt in [("tron", 1, "addBlackList", t), ("eth", 2, "addBlackList", "0x" + hex20),
                                ("avax", 3, "addToBlockedList", "0x" + hex20), ("eth", 4, "issue", None)]:
        ensure_proposal(conn, chain, tid)
        update_proposal(conn, chain, tid, destination="x", function=fn, target=tgt, executed=1,
                        executed_at="2026-11-01T00:00:00Z", status="executed")
    tab, total = crosschain.table(conn)
    assert total == 1 and tab[0]["n_chains"] == 3 and tab[0]["address"] == "0x" + hex20
    assert ("0x" + hex20, "tron") in crosschain.pairs(conn)


def test_ingest_logs_generic_chain(tmp_path):
    """MSW events for an arbitrary network (avax): Submission+Confirmation in the same tx => submit; Execution => executed."""
    from freezewatch import eth
    from freezewatch.derive import derive_all
    t32 = lambda n: "0x" + f"{n:064x}"
    a32 = lambda a: "0x" + "0" * 24 + a[2:]
    owner1, owner2, owner3 = "0x" + "a1" * 20, "0x" + "b2" * 20, "0x" + "c3" * 20
    ts = lambda s: hex(s)
    logs = [
        {"topics": [C.TOPIC_SUBMISSION, t32(7)], "transactionHash": "0xs", "timeStamp": ts(1000), "blockNumber": "0x1"},
        {"topics": [C.TOPIC_CONFIRMATION, a32(owner1), t32(7)], "transactionHash": "0xs", "timeStamp": ts(1000), "blockNumber": "0x1"},
        {"topics": [C.TOPIC_CONFIRMATION, a32(owner2), t32(7)], "transactionHash": "0xc2", "timeStamp": ts(1300), "blockNumber": "0x2"},
        {"topics": [C.TOPIC_CONFIRMATION, a32(owner3), t32(7)], "transactionHash": "0xc3", "timeStamp": ts(1900), "blockNumber": "0x3"},
        {"topics": [C.TOPIC_EXECUTION, t32(7)], "transactionHash": "0xc3", "timeStamp": ts(1900), "blockNumber": "0x3"},
    ]
    conn = connect(tmp_path / "m.sqlite")
    eth.ingest_logs(conn, logs, "avax")
    update_proposal(conn, "avax", 7, destination="0xtok", function="addToBlockedList", target="0xabc", required=3)
    derive_all(conn, "avax")
    r = conn.execute("SELECT * FROM proposals WHERE chain='avax' AND tx_id=7").fetchone()
    assert r["executed"] == 1 and r["delay_seconds"] == 900 and r["confirmations"] == 3
    assert r["submitter"] == owner1 and r["exec_tx"] == "0xc3" and r["status"] == "executed"
    assert conn.execute("SELECT COUNT(*) FROM proposals WHERE chain='eth'").fetchone()[0] == 0
