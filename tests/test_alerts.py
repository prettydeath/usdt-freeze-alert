import time

from freezewatch import alerts, constants as C
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import iso

NOW = int(time.time())
NOBAL = lambda addr, have: {}  # tests never touch the network
ADDR = "TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd"


def mk(conn, chain, tid, **vals):
    ensure_proposal(conn, chain, tid)
    base = dict(destination="x", function="addBlackList", target=ADDR, executed=0, confirmations=1, required=2,
                status="pending", submitted_at=iso(NOW - 300), balance_now=355678.98)
    base.update(vals)
    update_proposal(conn, chain, tid, **base)


def row(conn, chain, tid):
    return conn.execute("SELECT * FROM proposals WHERE chain=? AND tx_id=?", (chain, tid)).fetchone()


def test_new_proposal_radar_format(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    mk(conn, "tron", 11957)
    txt = alerts.fmt_new(row(conn, "tron", 11957), 60, seen="12:34:56")
    lines = txt.split(chr(10))
    assert lines[0] == "🚨 <b>FREEZE INCOMING</b> · TRON"
    assert lines[1] == f"<code>{ADDR}</code>"  # full address, never truncated
    assert "💰 <b>355,679 USDT</b> on the line" in txt
    assert "✍️ ▰▰▰▰▰▱▱▱▱▱ 1/2 signed" in txt
    assert "⏱ usually lands in <b>~1 min</b>" in txt
    assert "<blockquote>proposal #11957 · addBlackList" + chr(10) + "seen 12:34:56 UTC</blockquote>" in txt
    assert "#TRON #addBlackList #whale" in txt
    assert txt.endswith(f'<a href="https://tronscan.org/#/address/{ADDR}">explorer</a> · <a href="{alerts.DASHBOARD}#addr={ADDR}">site</a> · <a href="{alerts.DASHBOARD}">dashboard</a>')
    assert alerts.e("<b>&") == "&lt;b&gt;&amp;"
    assert "usually lands" not in alerts.fmt_new(row(conn, "tron", 11957), None)  # no stats -> line omitted


def test_safe_note_tags_and_token(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    mk(conn, "arb1", 18500, function="addToBlockedList", target="0xe485e7a70b1131216cb47ca4555c4bbb2c742478",
       required=3, balance_now=12000.0)
    txt = alerts.fmt_new(row(conn, "arb1", 18500), None, seen="00:00:01")
    assert "Safe nonce 185 · addToBlockedList · via Safe API (off-chain queue)" in txt
    assert "💰 <b>12,000 USDT0</b>" in txt and "#ARB #addToBlockedList" in txt and "#whale" not in txt
    assert "▰▰▰▱▱▱▱▱▱▱ 1/3" in txt
    assert alerts.bar(0, 0) == "▱" * 10 and alerts.bar(3, 3) == "▰" * 10


def test_other_formats(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    mk(conn, "tron", 1, executed=1, status="executed", delay_seconds=0, exec_tx="ab" * 32, confirmations=2)
    f = alerts.fmt_frozen(row(conn, "tron", 1))
    assert f.startswith("🧊 <b>FROZEN</b> · TRON") and "⚡ <b>no advance notice</b>" in f
    assert f"<blockquote>2/2 signed · tx {'ab' * 32}</blockquote>" in f and "#TRON #frozen" in f
    update_proposal(conn, "tron", 1, delay_seconds=6564)
    assert "⚡ executed in <b>1 h 49 min</b>" in alerts.fmt_frozen(row(conn, "tron", 1))
    mk(conn, "eth", 2, function="destroyBlackFunds", executed=1, status="executed", exec_tx="0x" + "cd" * 32,
       balance_at_detect=4242287.28)
    d = alerts.fmt_destroy(row(conn, "eth", 2))
    assert "🔥 <b>FUNDS BURNED</b> · ETH" in d and "💸 <b>4,242,287 USDT</b> destroyed" in d and "#ETH #burned" in d
    n = alerts.fmt_near(row(conn, "tron", 1))
    assert n.startswith("⚠️ <b>LAST SIGNATURE PENDING</b> · TRON")
    assert alerts.fmt_unfrozen(row(conn, "tron", 1)).startswith("🟢 <b>UNFROZEN</b> · TRON")
    x = alerts.fmt_xchain("0xe485e7a70b1131216cb47ca4555c4bbb2c742478", "pol", {"pol"}, {"eth": 25000.0})
    assert "🔗 <b>CROSS-CHAIN SIGNAL</b>" in x and "still holds 💰 25,000 USDT on ETH" in x and "#crosschain" in x
    assert alerts.num(999.5) == "999.50" and alerts.num(1234567.8) == "1,234,568"


def test_collect_events_rules(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    assert alerts.collect_events(conn, {}, 0, set(), balances=NOBAL) == []  # first run on empty DB: nothing
    before = {("tron", 99): (0, 0, 2, "addBlackList", "pending")}
    mk(conn, "tron", 11957)                                           # new, fresh
    mk(conn, "tron", 11000, submitted_at=iso(NOW - 86400 * 40))       # old history: skipped
    mk(conn, "eth", 5870, required=3, confirmations=2, function="addBlackList", submitted_at=iso(NOW - 3600))
    mk(conn, "tron", 99, executed=1, status="executed", executed_at=iso(NOW - 60), delay_seconds=500)
    mk(conn, "tron", 98, function="destroyBlackFunds", executed=1, status="executed", executed_at=iso(NOW - 60))
    mk(conn, "tron", 97, function="issue")                            # admin op: never alerted
    ev = alerts.collect_events(conn, before, 0, set(), now=NOW, balances=NOBAL)
    kinds = sorted((e["kind"], e["id"]) for e in ev)
    assert ("new", 11957) in kinds and ("near", 5870) in kinds and ("frozen", 99) in kinds and ("destroy", 98) in kinds
    assert not any(i in (11000, 97) for _, i in kinds)
    assert ("near", 11957) not in kinds  # TRON 1/2 is the initial state, already covered by "new"
    assert [e["kind"] for e in ev] == sorted((e["kind"] for e in ev), key=lambda k: alerts.PRIO[k])


def test_min_balance_filter(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    mk(conn, "tron", 1, balance_now=500)
    ev = alerts.collect_events(conn, {("x", 1): (0, 0, 2, "f", "p")}, 0, set(), now=NOW, min_balance=1000, balances=NOBAL)
    assert ev == []


def test_dedupe_and_flood_guard(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    sent = []
    events = [{"kind": "new", "chain": "tron", "id": i, "key_kind": "new", "text": f"msg{i}"} for i in range(25)]
    n = alerts.dispatch(conn, events, sent.append, limit=20)
    assert n == 21 and len(sent) == 21                       # 20 events + 1 summary
    assert sent[-1] == alerts.fmt_summary(5) and sent[-1].startswith("📊 <b>+5 more events</b>")
    assert alerts.DASHBOARD in sent[-1]
    sent.clear()
    assert alerts.dispatch(conn, events, sent.append) == 0 and sent == []  # never twice (overflow ones too)


def test_failed_send_is_retried(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    ev = [{"kind": "new", "chain": "tron", "id": 1, "key_kind": "new", "text": "x"}]

    def boom(_):
        raise RuntimeError("net")
    assert alerts.dispatch(conn, ev, boom) == 0
    ok = []
    assert alerts.dispatch(conn, ev, ok.append) == 1 and ok == ["x"]


def test_run_alerts_noop_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    conn = connect(tmp_path / "a.sqlite")
    mk(conn, "tron", 1)
    assert alerts.run_alerts(conn, {("x", 1): (0, 0, 2, "f", "p")}, 0, set()) == 0
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_sample_messages_render():
    msgs = alerts.sample_messages()
    assert len(msgs) == 11 and all(isinstance(m, str) and m for m in msgs)


def test_new_proposal_without_tronscan_submit_still_alerts(tmp_path):
    """TRON is on-chain first: a brand-new proposal may not be in Tronscan yet (no submitted_at) - it must still alert."""
    conn = connect(tmp_path / "a.sqlite")
    ensure_proposal(conn, "tron", 12000)
    update_proposal(conn, "tron", 12000, destination="x", function="addBlackList", target=ADDR, executed=0,
                    confirmations=1, required=2, status="pending", submitted_at=None, balance_now=1000.0)
    conn.execute("UPDATE proposals SET first_seen_at=? WHERE tx_id=12000", (iso(NOW - 120),))
    conn.commit()
    ev = alerts.collect_events(conn, {("x", 1): (0, 0, 2, "f", "p")}, 0, set(), now=NOW, balances=NOBAL)
    assert [e["kind"] for e in ev] == ["new"]
