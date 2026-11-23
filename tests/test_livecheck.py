import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from freezewatch import livecheck as LC
from freezewatch.registry import EVENT_CHAINS, MSW_CHAINS, SAFE_CHAINS

ADDR = "0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c"
TEMPLATE = Path(__file__).resolve().parent.parent / "freezewatch" / "template.html"


def test_selectors_match_keccak():
    eu = pytest.importorskip("eth_utils")
    for sig, sel in [("isBlackListed(address)", LC.SEL_IS_BLACKLISTED), ("isBlacklisted(address)", LC.SEL_IS_BLACKLISTED_USDC),
                     ("isBlocked(address)", LC.SEL_IS_BLOCKED), ("balanceOf(address)", LC.SEL_BALANCE_OF)]:
        assert eu.keccak(text=sig)[:4].hex() == sel, sig


def test_calldata_and_decoders():
    d = LC.evm_calldata(LC.SEL_IS_BLOCKED, ADDR.upper().replace("0X", "0x"))
    assert d == "0xfbac3951" + "0" * 24 + ADDR[2:] and len(d) == 2 + 8 + 64
    assert LC.tron_param(ADDR) == "0" * 24 + ADDR[2:]
    assert LC.decode_bool("0x" + "0" * 63 + "1") is True and LC.decode_bool("0x" + "0" * 64) is False
    assert LC.decode_bool("0x") is None and LC.decode_bool(None) is None
    assert LC.decode_units("0x" + f"{1_175_464_712_876_362:064x}") == 1_175_464_712.876362


def test_targets_cover_registry_and_are_https():
    t = LC.targets()
    ids = {x["id"] for x in t["evm"]}
    assert {"eth_usdt", "eth_usdc"} <= ids
    from freezewatch.registry import ALIAS_CHAINS
    assert ids >= (set(MSW_CHAINS) | set(SAFE_CHAINS) | set(EVENT_CHAINS)) - ALIAS_CHAINS
    assert not (ids & ALIAS_CHAINS)  # arb_usdt shares arb1's token: one live check
    for x in t["evm"]:
        assert x["rpcs"] and all(u.startswith("https://") for u in x["rpcs"]), x["id"]
        assert re.fullmatch(r"0x[0-9a-fA-F]{40}", x["contract"]) and x["sel"] in (LC.SEL_IS_BLACKLISTED, LC.SEL_IS_BLACKLISTED_USDC, LC.SEL_IS_BLOCKED)
    assert t["tron"]["contract"] == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" and set(t["solana"]["mints"]) == {"sol_usdc", "sol_usdt"}
    assert "api.trongrid.io" in t["providers"] and "ethereum-rpc.publicnode.com" in t["providers"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_builders_match_python_mirror():
    html = TEMPLATE.read_text(encoding="utf-8")
    block = html.split("// LIVE-BEGIN")[1].split("// LIVE-END")[0]
    script = block + """
const out = {
  data: evmData('fbac3951', '0x5227F603C1EE34CE3C06EFE6477003BDFCCB2F8C'),
  tron: tronParam('0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c'),
  topic: padTopic('5227f603c1ee34ce3c06efe6477003bdfccb2f8c'),
  t: decBool('0x' + '0'.repeat(63) + '1'), f: decBool('0x' + '0'.repeat(64)), n: decBool('0x'),
  bal: decUnits('0x' + (1175464712876362n).toString(16).padStart(64, '0')),
};
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["data"] == LC.evm_calldata(LC.SEL_IS_BLOCKED, ADDR)
    assert out["tron"] == LC.tron_param(ADDR)
    assert out["topic"] == "0x" + "0" * 24 + ADDR[2:]
    assert (out["t"], out["f"], out["n"]) == (True, False, None)
    assert out["bal"] == LC.decode_units("0x" + f"{1_175_464_712_876_362:064x}")


def test_template_never_auto_runs_live_checks():
    html = TEMPLATE.read_text(encoding="utf-8")
    # live checks start only from button handlers (runLive/liveOne), never from load or the #watch= deep link
    assert html.count("runLive(") == 2  # definition + the single call inside liveOne
    assert "liveOne(" in html and "does send the address" in html
    open_hash = html.split("function openWatchFromHash(){")[1].split("\n}")[0]
    assert "liveOne" not in open_hash and "runLive" not in open_hash
