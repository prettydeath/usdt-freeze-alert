import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from freezewatch import pulse, reasons
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import iso

NOW = int(time.time())
TEMPLATE = Path(__file__).resolve().parent.parent / "freezewatch" / "template.html"
A1 = "0x" + "11" * 20
A2 = "0x" + "22" * 20


# ---------------- reasons.yaml ----------------
YAML = """# curated
- title: "Example incident"
  category: hack
  date: 2025-01-31
  source_url: https://example.org/post-mortem
  chain: eth
  addresses:
    - 0x1111111111111111111111111111111111111111   # trailing comment
    - 0x2222222222222222222222222222222222222222
- title: No source
  category: scam
  addresses: [0x3333333333333333333333333333333333333333]
- title: Bad category
  category: gossip
  source_url: https://example.org/x
  addresses:
    - 0x4444444444444444444444444444444444444444
- title: No address
  category: other
  source_url: https://example.org/y
"""


def test_yaml_subset_parser_and_validation():
    items = reasons.parse_yaml(YAML)
    assert len(items) == 4 and items[0]["addresses"] == ["0x" + "11" * 20, "0x" + "22" * 20] and items[0]["date"] == "2025-01-31"
    assert items[1]["addresses"] == ["0x" + "33" * 20]
    ok, bad = reasons.validate(items)
    assert [e["title"] for e in ok] == ["Example incident"]                      # only the cited, categorised, addressed entry
    assert len(bad) == 3 and "no public https source_url" in bad[0] and "category" in bad[1] and "no address" in bad[2]


def test_load_ships_empty_and_reads_curated(tmp_path):
    assert reasons.load(tmp_path / "missing.yaml") == {}
    assert reasons.load(Path(reasons.REASONS_FILE)) == {}      # the shipped file has no guessed entries
    f = tmp_path / "r.yaml"
    f.write_text(YAML, encoding="utf-8")
    idx = reasons.load(f)
    assert ("eth", "0x" + "11" * 20) in idx and len(idx) == 2


def test_reason_priority_and_ofac_flag():
    labels = {("ethereum", A1): {"entity": "Some Exploiter", "label": "x", "category": "hack", "source": "etherscan-labels",
                                 "source_url": "https://l/1"},
              ("ethereum", A2): {"entity": "OFAC SDN", "label": "SDN", "category": "sanctioned",
                                 "source": "ofac-sdn+graphsense", "source_url": "https://l/2"}}
    cur = {("eth", A1): {"category": "scam", "title": "Curated", "source_url": "https://c/1", "date": "2025-01-01"}}
    r = reasons.reason_of(labels, cur, "eth", A1, ("ethereum", A1))
    assert r["origin"] == "curated" and r["category"] == "scam"                   # curated beats the hint
    r = reasons.reason_of(labels, {}, "eth", A1, ("ethereum", A1))
    assert r["origin"] == "wallet-attribution" and r["category"] == "hack" and not r["ofac"]
    r = reasons.reason_of(labels, {}, "eth", A2, ("ethereum", A2))
    assert r["ofac"] is True and r["category"] == "sanctions"
    assert reasons.reason_of(labels, {}, "eth", "0x" + "99" * 20, ("ethereum", "0x" + "99" * 20)) is None
    lab_nonofac = {("ethereum", A1): {"category": "sanctioned", "source": "graphsense", "entity": "e", "source_url": "u"}}
    assert reasons.reason_of(lab_nonofac, {}, "eth", A1, ("ethereum", A1))["ofac"] is False   # "sanctioned" alone is not OFAC


def mk(conn, chain, tid, target, executed_at, submitted_at=None, fn="addBlackList", delay=None):
    ensure_proposal(conn, chain, tid)
    update_proposal(conn, chain, tid, destination="x", function=fn, target=target, executed=1, executed_at=iso(executed_at),
                    submitted_at=iso(submitted_at or executed_at), status="executed", confirmations=2, required=3,
                    delay_seconds=delay, public_proposal=1, amount=1000.0)


def test_reasons_analyze_counts_ofac_and_unknown(tmp_path):
    conn = connect(tmp_path / "r.sqlite")
    mk(conn, "eth", 1, A1, NOW - 3600)
    mk(conn, "eth", 2, A2, NOW - 3000)
    mk(conn, "eth", 3, "0x" + "55" * 20, NOW - 2000)
    labels = {("ethereum", A1): {"entity": "Exploit X", "label": "", "category": "hack", "source": "forta", "source_url": "https://l/1"},
              ("ethereum", A2): {"entity": "OFAC SDN", "label": "", "category": "sanctioned", "source": "ofac-sdn", "source_url": "https://l/2"}}
    a = reasons.analyze(conn, labels, curated={})
    assert a["freezes"] == 3 and a["known"] == 2 and a["unknown"] == 1 and a["known_pct"] == 66.7
    assert a["by_category"]["hack"]["n"] == 1 and a["by_category"]["sanctions"]["n"] == 1
    assert a["ofac"]["n"] == 1 and a["ofac"]["top"][0]["address"] == A2
    h = reasons.hints_for([("eth", A1), ("eth", A2), ("eth", "0x" + "55" * 20)], labels, {})
    assert set(h) == {f"eth:{A1}", f"eth:{A2}"} and h[f"eth:{A2}"][4] == 1


# ---------------- pulse ----------------
def conf(conn, chain, tid, signer, kind, ts):
    conn.execute("INSERT INTO confirmations(chain,tx_id,signer,kind,ts,tx_hash) VALUES(?,?,?,?,?,?)",
                 (chain, tid, signer, kind, iso(ts), f"h{tid}{signer}{kind}"))


def test_pulse_signers_median_and_last_share(tmp_path):
    conn = connect(tmp_path / "p.sqlite")
    base = NOW - 5 * 86400
    for i in range(12):                                   # 12 executed proposals: S1 submits, S2 confirms after 60 s, S3 last after 600 s
        t = base + i * 3600
        mk(conn, "eth", i, A1, t + 700, submitted_at=t, delay=700)
        conf(conn, "eth", i, "S1", "submit", t)
        conf(conn, "eth", i, "S2", "confirm", t + 60)
        conf(conn, "eth", i, "S3", "confirm", t + 600)
    s = {x["signer"]: x for x in pulse.signers(conn, now=NOW)["eth"]}
    assert s["S2"]["median_confirm_s"] == 60 and s["S3"]["median_confirm_s"] == 600 and s["S1"]["median_confirm_s"] is None
    assert s["S3"]["last_n"] == 12 and s["S3"]["last_share"] == 1.0 and s["S1"]["last_share"] == 0.0 and s["S2"]["n"] == 12
    assert sum(s["S2"]["months"].values()) == 12


def test_pulse_speed_monthly_median_and_queue(tmp_path):
    conn = connect(tmp_path / "p.sqlite")
    for i, d in enumerate([100, 200, 300, 400, 500, 600, 700, 800, 900]):
        mk(conn, "tron", i, A1, NOW - 86400 + i, submitted_at=NOW - 86400 - d + i, delay=d)
    sp = pulse.speed(conn, now=NOW)
    assert list(sp["tron"].values()) == [500]                       # median of the nine delays, one month
    ensure_proposal(conn, "eth", 99)                                  # never executed: open for 7 days, then gone
    update_proposal(conn, "eth", 99, function="addBlackList", target=A2, submitted_at=iso(NOW - 10 * 86400), status="stale")
    q = dict(map(tuple, pulse.queue(conn, now=NOW, days=12)))
    day0 = int(NOW // 86400) * 86400
    assert q[day0 - 9 * 86400] == 1 and q[day0 - 1 * 86400] == 0 and q[day0] >= 0


# ---------------- report builder (JS, run with node) ----------------
def _extract(marker_a, marker_b):
    t = TEMPLATE.read_text(encoding="utf-8")
    return t[t.index(marker_a) + len(marker_a):t.index(marker_b)]


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_report_builder_markdown(tmp_path):
    body = _extract("// REPORT-BEGIN", "// REPORT-END")
    stubs = """
const CH = {pol: {label: 'USDT0 - Polygon'}, eth: {label: 'USDT - Ethereum', tx: 'https://etherscan.io/tx/', addr: 'https://etherscan.io/address/'}};
const D = {generated_at: '2026-11-23T10:00:00Z'};
const usd = x => '$' + Math.round(x).toLocaleString('en-US'); const dur = s => s + 's'; const dstr = ts => 'T' + ts;
const statusOf = r => ({word: 'BLOCKED', summary: 'on 1 chain', amount: '$5', also: ''});
const timelineOf = r => [{ts: 1, chain: 'eth', label: 'frozen', tx: 'TXHASH'}];
""".replace("TXHASH", "0x" + "ab" * 32)
    ADDR = "0x" + "11" * 20
    rec = {"l": {"eth": ["Binance", "Hot wallet", "exchange", "cex-list", "https://l/x", "high"]},
           "r": {"eth": ["sanctions", "OFAC SDN", "https://ofac/src", "wallet-attribution", 1]},
           "e": [["eth", "f", 1795000000, "0x" + "ab" * 32, None, 120, None, 7, 3, 3, 1794999880, 1]],
           "o": {"eth": [[1795000000, 5000, 4000, [["0x" + "99" * 20, 5000, 4000, ["Gate.io", "", "exchange", "s", "u", "high"], 0]], 250, 1, 0]]},
           "c": ["b-1234abcd"], "x": [["eth", "frozen", 1795000000, [["pol", "USDT0", 5000, 4900]]]],
           "s": {"eth": "unfrozen", "pol": "frozen"}, "b": {"eth": 239942.5, "pol": 1200.0, "avax": 0}, "bc": {"eth": "2026-11-21"}}
    js = stubs + body + "\nconst r = " + json.dumps({"input": ADDR, "key": ADDR, "rec": rec, "live": None}) + \
        ";\nprocess.stdout.write(buildReport(r, '2026-11-23T10:05:00Z'));"
    f = tmp_path / "r.js"
    f.write_text(js, encoding="utf-8")
    md = subprocess.run(["node", str(f)], capture_output=True, text=True, check=True).stdout
    assert md.startswith("# Address report: " + ADDR) and f"`{ADDR}`" in md                      # full address, never truncated
    assert "Generated: 2026-11-23T10:05:00Z (UTC)" in md and "Data as of: 2026-11-23T10:00:00Z" in md
    assert "OFAC SDN listed" in md and "Label from wallet-attribution (eth): Binance / Hot wallet / exchange" in md and "https://ofac/src" in md
    assert "https://etherscan.io/tx/0x" + "ab" * 32 in md                                       # tx hash and explorer link
    assert "escaped $5,000 ($4,000 between proposal and execution)" in md and "moved between addresses that were also frozen" in md
    assert "TRUNCATED (lower bound)" in md and "[label from wallet-attribution: Gate.io / exchange]" in md
    assert "## Balances" in md and "| State | As of |" in md                                          # balances as a table with a state column
    assert "| USDT - Ethereum | $239,943 | Unfrozen - funds can move again | 2026-11-21 |" in md
    assert "| USDT0 - Polygon | $1,200 | Frozen - cannot move | last scan |" in md
    assert "avax" not in md.split("## Balances")[1].split("##")[0].lower()                          # zero balances are not listed
    assert "`b-1234abcd`" in md and "still unblocked: $4,900 on" in md and "not financial or legal advice" in md.lower()



@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_balance_state_per_chain_js(tmp_path):
    """blocked chain -> frozen; unfrozen (index or live says not blocked after a freeze) -> movable; never blocked -> not blocked."""
    body = _extract("// REPORT-BEGIN", "// REPORT-END")
    live_ok = {"results": [{"chain": "eth", "blocked": False, "balance": 350, "checked_at": "2026-11-23T10:00:00Z"}]}
    cases = {
        "index_frozen": {"rec": {"s": {"eth": "frozen"}, "b": {"eth": 100}}, "live": None},
        "index_unfrozen": {"rec": {"s": {"eth": "unfrozen"}, "b": {"eth": 200}, "bc": {"eth": "2026-11-01"}}, "live": None},
        "live_says_open_after_freeze": {"rec": {"s": {"eth": "frozen"}, "b": {"eth": 300}}, "live": live_ok},
        "live_clean": {"rec": None, "live": {"results": [
            {"chain": "pol", "blocked": False, "balance": 50, "checked_at": "2026-11-23T10:00:00Z"},
            {"chain": "eth", "blocked": None, "balance": None, "checked_at": "x"}]}},
        "live_blocked": {"rec": {"s": {"eth": "frozen"}}, "live": {"results": [
            {"chain": "eth", "blocked": True, "balance": 900, "checked_at": "2026-11-23T10:00:00Z"}]}},
    }
    js = (body + "\nconst cases = " + json.dumps(cases) + ";\nconst out = {};\n"
          "for(const [k, r] of Object.entries(cases)) out[k] = balanceRows({...r, input: 'x'}).map(b => [b.c, b.v, b.state, b.asof, b.src]);\n"
          "process.stdout.write(JSON.stringify(out));")
    f = tmp_path / "s.js"
    f.write_text(js, encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, check=True).stdout)
    assert out["index_frozen"] == [["eth", 100, "blocked", None, "index"]]
    assert out["index_unfrozen"] == [["eth", 200, "unfrozen", "2026-11-01", "index"]]
    assert out["live_says_open_after_freeze"][0][:3] == ["eth", 350, "unfrozen"] and out["live_says_open_after_freeze"][0][4] == "live"
    assert out["live_clean"] == [["pol", 50, "clear", "2026-11-23 10:00 UTC", "live"]]
    assert out["live_blocked"][0][2] == "blocked"


def test_recently_unfrozen_balance_is_read_without_touching_frozen_balance(tmp_path, monkeypatch):
    from freezewatch import analytics
    conn = connect(tmp_path / "u.sqlite")
    mk(conn, "eth", 1, A1, NOW - 10 * 86400)                               # frozen 10 days ago ...
    mk(conn, "eth", 2, A1, NOW - 3600, fn="removeBlackList")               # ... unfrozen an hour ago
    mk(conn, "eth", 3, A2, NOW - 90 * 86400)
    mk(conn, "eth", 4, A2, NOW - 80 * 86400, fn="removeBlackList")         # unfrozen long ago: not re-read
    conn.execute("INSERT INTO balances(chain,address,token,balance,checked_at,frozen_balance,frozen_checked_at) "
                 "VALUES('eth',?,'USDT',5.0,'2026-01-01',777.0,'2026-01-01')", (A1,))
    assert analytics.recent_unfrozen(conn) == [("eth", A1)]
    monkeypatch.setattr(analytics, "fetch_balances", lambda chain, addrs: {a: 1234.5 for a in addrs})
    analytics.refresh_balances(conn, 30)
    row = conn.execute("SELECT balance, frozen_balance FROM balances WHERE address=?", (A1,)).fetchone()
    assert row["balance"] == 1234.5 and row["frozen_balance"] == 777.0      # frozen_balance (balance while frozen) is preserved
