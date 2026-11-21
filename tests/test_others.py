import pytest

from freezewatch import constants as C
from freezewatch.db import connect
from freezewatch.others import add_freeze, extract_freezes, parse_multisig
from freezewatch.util import b58decode, b58encode

USDC_MINT = C.SOLANA_SOURCES["sol_usdc"]["mint"]
ACCT = "BqC7pfAJgdDDU4BQDgNSXw1bjM4SM4575BTErpsvfGUv"
OWNER = "Aw6YNM6EZMpAUtEi3HJK75DBP6UAa2U4natSnVtKBiMU"
S1, S2 = "Af4XfMtaHRR68pfHFoDGVP7nKmywrAuDQWjhHpbbtFjM", "8xZVZQV3yb1kqzwX5Zdte4yuFncWcEn6A1GjjF7oL1M5"
AUTH = C.SOLANA_SOURCES["sol_usdc"]["authority"]
SIG = "nXm2cEKwZSNqNbs3FLexj4ZYD9bnso7Gkt4o4qHqSK8cAA7CtCtRFW3nsbFhBYJKnRFkxAznF1Tq5dDWsUtc6pk"


def fixture_tx(mint=USDC_MINT, typ="freezeAccount"):
    """Shortened copy of a real getTransaction(jsonParsed) structure for the USDC freeze of 2026-11-07."""
    return {
        "blockTime": 1794015637, "slot": 466330911,
        "transaction": {"message": {
            "accountKeys": [{"pubkey": k} for k in (S1, S2, ACCT, "DYFUcJDfN3w1byfPtdy5xzQmHo7fEHKopgQFBSD93X1g",
                                                    "11111111111111111111111111111111", AUTH)],
            "instructions": [
                {"program": "system", "parsed": {"type": "advanceNonce", "info": {}}},
                {"program": "spl-token", "parsed": {"type": typ, "info": {
                    "account": ACCT, "mint": mint, "multisigFreezeAuthority": AUTH, "signers": [S1, S2]}}},
            ]},
            "signatures": [SIG]},
        "meta": {"err": None, "innerInstructions": [],
                 "postTokenBalances": [{"accountIndex": 2, "mint": mint, "owner": OWNER,
                                        "uiTokenAmount": {"uiAmountString": "50000.155556"}}]},
    }


def test_extract_freeze_usdc_fixture():
    f = extract_freezes(fixture_tx(), USDC_MINT)
    assert len(f) == 1
    assert f[0]["action"] == "freeze" and f[0]["account"] == ACCT and f[0]["owner"] == OWNER
    assert f[0]["amount"] == 50000.155556 and f[0]["signers"] == [S1, S2]


def test_extract_filters_other_mint_and_failed_and_thaw():
    assert extract_freezes(fixture_tx(mint="OtherMint"), USDC_MINT) == []
    bad = fixture_tx()
    bad["meta"]["err"] = {"InstructionError": [1, "x"]}
    assert extract_freezes(bad, USDC_MINT) == []
    assert extract_freezes(fixture_tx(typ="thawAccount"), USDC_MINT)[0]["action"] == "unfreeze"
    assert extract_freezes(fixture_tx(typ="transfer"), USDC_MINT) == []


def test_parse_multisig():
    keys = [S1, S2, ACCT, OWNER]
    raw = bytes([2, 4, 1]) + b"".join(b58decode(k) for k in keys) + bytes(32 * 7)
    assert len(raw) == 355
    ms = parse_multisig(raw)
    assert (ms["m"], ms["n"]) == (2, 4) and ms["signers"] == keys and ms["initialized"]
    assert b58encode(b58decode(S1)) == S1
    with pytest.raises(ValueError):
        parse_multisig(b"\x00" * 10)


def test_freezes_idempotent(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    args = ("sol_usdc", ACCT, OWNER, "freeze", "2026-11-07T01:40:37Z", SIG)
    assert add_freeze(conn, *args, amount=1.0, amount_kind="at_tx") is True
    assert add_freeze(conn, *args, amount=1.0, amount_kind="at_tx") is False
    assert conn.execute("SELECT COUNT(*) FROM freezes").fetchone()[0] == 1


def test_usdc_constants():
    eu = pytest.importorskip("eth_utils")
    for topic, sig in C.TOPIC_USDC_SIGS.items():
        assert "0x" + eu.keccak(text=sig).hex() == topic
    for name, sel in [("blacklist", C.SEL_USDC_BLACKLIST), ("unBlacklist", C.SEL_USDC_UNBLACKLIST),
                      ("isBlacklisted", C.SEL_USDC_IS_BLACKLISTED)]:
        assert eu.keccak(text=C.USDC_SIGS[name])[:4].hex() == sel
