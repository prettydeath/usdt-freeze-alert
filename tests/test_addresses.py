import csv
import json

from freezewatch import addresses as AD, render
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import iso, tron_from_hex20

HEX = "bca5d8e9fc4e31c72631a144e7f1563736f09061"
TRON = tron_from_hex20(HEX)
EVM = "0x" + HEX


def test_canon_forms():
    assert AD.canon(EVM.upper().replace("0X", "0x")) == EVM          # EVM is case-insensitive
    assert AD.canon(TRON) == EVM                                      # TRON matches its 20-byte hex
    assert AD.canon("0x" + "ab" * 32) == "0x" + "ab" * 32             # Aptos
    assert AD.canon("0:" + "CD" * 32) == "0:" + "cd" * 32             # TON raw
    assert AD.canon("usdt.tether-token.near") == "usdt.tether-token.near"
    assert AD.canon("Aw6YNM6EZMpAUtEi3HJK75DBP6UAa2U4natSnVtKBiMU") == "Aw6YNM6EZMpAUtEi3HJK75DBP6UAa2U4natSnVtKBiMU"  # Solana
    assert AD.canon("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u") is None     # bad checksum
    assert AD.canon("not an address") is None and AD.canon("") is None and AD.canon("0x1234") is None


def prop(conn, chain, tid, fn, target, **kw):
    ensure_proposal(conn, chain, tid)
    base = dict(destination="x", function=fn, target=target, confirmations=1, required=2, status="pending",
                submitted_at="2026-11-20T00:00:00Z", executed=0)
    base.update(kw)
    update_proposal(conn, chain, tid, **base)


def test_build_merges_tron_and_evm_and_excludes_sinks(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    prop(conn, "tron", 11957, "addBlackList", TRON)                                           # live pending
    prop(conn, "tron", 11000, "addBlackList", TRON, status="stale", submitted_at="2026-01-01T00:00:00Z")
    prop(conn, "eth", 5, "addBlackList", EVM.upper().replace("0X", "0x"), executed=1, status="executed",
         executed_at="2026-11-01T00:00:00Z", exec_tx="0xaa", delay_seconds=0)
    prop(conn, "eth", 6, "destroyBlackFunds", EVM, executed=1, status="executed", executed_at="2026-11-02T00:00:00Z",
         exec_tx="0xbb", amount=500.0, delay_seconds=60)
    prop(conn, "eth", 7, "addBlackList", "0x" + "0" * 40, executed=1, status="executed", executed_at="2026-11-01T00:00:00Z",
         exec_tx="0xcc")                                                                      # zero address: excluded
    prop(conn, "eth", 8, "issue", None)                                                       # not a freeze action
    conn.execute("INSERT INTO balances(chain,address,token,balance,checked_at,frozen_balance) VALUES('eth',?,?,?,?,?)",
                 (EVM, "USDT", 12.345, "2026-11-22", 12.345))
    conn.execute("INSERT INTO inflows_after_freeze(chain,token,frozen_address,sender,amount,ts,tx_hash,first_seen_at) "
                 "VALUES('eth','USDT',?,?,?,?,?,?)", (EVM, "0x" + "11" * 20, 40.0, iso(1_790_000_000), "0xdd", iso(1_790_000_001)))
    conn.execute("INSERT INTO freezes(source,subject,owner,action,ts,tx_hash,amount,amount_kind,first_seen_at) "
                 "VALUES('sol_usdc','TokAcc','OwnerWallet','freeze','2026-11-07T01:40:37Z','sig',50.0,'at_tx','x')")
    conn.commit()
    idx = AD.build(conn)
    assert "0x" + "0" * 40 not in idx and set(idx) == {EVM, "TokAcc", "OwnerWallet"}
    r = idx[EVM]
    assert [p[:3] for p in r["p"]] == [["tron", 11957, "addBlackList"], ["tron", 11000, "addBlackList"]]
    assert [p[6] for p in r["p"]] == ["pending", "stale"]
    assert [e[:2] for e in r["e"]] == [["eth", "f"], ["eth", "b"]] and r["e"][1][4] == 500.0
    assert r["b"] == {"eth": 12.35} or r["b"] == {"eth": 12.34}
    assert r["i"]["eth"][0] == 1 and r["i"]["eth"][1] == 40.0 and r["i"]["eth"][2][0][0] == "0x" + "11" * 20
    assert r["s"] == {"eth": "burned"}
    assert idx["OwnerWallet"]["e"][0][6] == "TokAcc"  # Solana owner wallet finds the frozen token account


def test_write_single_file_and_shards(tmp_path, monkeypatch):
    idx = {"0xa1": {"p": []}, "0xb2": {"p": []}, "0xa3": {"e": []}, "SolAddr": {"e": []}}
    sizes = AD.write(tmp_path, idx)
    assert list(sizes) == ["addresses.json"] and json.loads((tmp_path / "addresses.json").read_text())["a"].keys() == idx.keys()
    monkeypatch.setattr(AD, "SHARD_LIMIT", 10)
    sizes = AD.write(tmp_path, idx)
    man = json.loads((tmp_path / "addresses.json").read_text())
    assert man["sharded"] and man["shards"] == ["_", "a", "b"]
    assert set(json.loads((tmp_path / "addr" / "a.json").read_text())["a"]) == {"0xa1", "0xa3"}
    assert "addr/_.json" in sizes
    monkeypatch.setattr(AD, "SHARD_LIMIT", 5_000_000)
    AD.write(tmp_path, idx)
    assert not list((tmp_path / "addr").glob("*.json"))  # shards removed when it fits again


def test_inflows_csv_export(tmp_path, monkeypatch):
    conn = connect(tmp_path / "a.sqlite")
    conn.execute("INSERT INTO inflows_after_freeze(chain,token,frozen_address,sender,amount,ts,tx_hash,first_seen_at) "
                 "VALUES('tron','USDT','TFrozen','TSender',123.45,'2026-11-01T00:00:00Z','abc','x')")
    conn.commit()
    monkeypatch.setattr(render, "DATA_DIR", tmp_path)
    render.export_files(conn)
    rows = list(csv.DictReader(open(tmp_path / "inflows.csv", encoding="utf-8")))
    assert rows == [{"chain": "tron", "token": "USDT", "frozen_address": "TFrozen", "sender": "TSender",
                     "amount": "123.45", "ts": "2026-11-01T00:00:00Z", "tx_hash": "abc"}]
