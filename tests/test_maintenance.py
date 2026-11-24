import os
import time

from freezewatch import maintenance, outflows
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import iso

NOW = int(time.time())
F = "0x" + "11" * 20


def _rows(conn, n_recipients, per_recipient=3, in_window_n=2, fts=None):
    fts = fts or iso(NOW - 3600)
    k = 0
    for r in range(n_recipients):
        for j in range(per_recipient):
            k += 1
            conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,?,0,?)", (F, f"0xr{r:05d}", 10.0 + r, iso(NOW - 7200 - j), f"0xtx{k}", j, fts))
    for i in range(in_window_n):
        conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,1,?)", (F, "0xwin", 5000.0 + i, iso(NOW - 900 + i), f"0xw{i}", fts))
    conn.execute("INSERT INTO outflow_done(chain,frozen_address,freeze_ts,checked_at,total_out,window_out,n_transfers,truncated) VALUES('eth',?,?,?,0,0,0,0)",
                 (F, fts, fts))
    conn.commit()


def test_giant_wallet_is_compacted_without_changing_totals(tmp_path):
    conn = connect(tmp_path / "m.sqlite")
    _rows(conn, 1000)                                                      # 3,000 small transfers to 1,000 recipients + 2 in-window ones
    tot = lambda: conn.execute("SELECT SUM(amount), SUM(CASE WHEN in_window THEN amount END) FROM outflows").fetchone()   # noqa: E731
    before = tot()
    removed = maintenance.compact_outflows(conn)
    after = tot()
    n = conn.execute("SELECT COUNT(*) FROM outflows").fetchone()[0]
    assert removed > 2000 and n <= maintenance.KEEP_RECIPIENTS + 1 + 2           # biggest recipients + one "other" row + the in-window rows
    assert abs(before[0] - after[0]) < 1e-6 and before[1] == after[1]             # nothing escaped: totals identical
    assert conn.execute("SELECT COUNT(*) FROM outflows WHERE in_window=1").fetchone()[0] == 2   # in-window transfers stay per transfer
    assert conn.execute("SELECT recipient FROM outflows WHERE recipient LIKE '(other recipients%'").fetchone()
    top = conn.execute("SELECT recipient, amount FROM outflows WHERE in_window=0 AND recipient NOT LIKE '(other%' ORDER BY amount DESC LIMIT 1").fetchone()
    assert top["recipient"] == "0xr00999" and top["amount"] == 3 * 1009.0              # per-recipient sums, biggest first
    assert maintenance.compact_outflows(conn) == 0                                    # idempotent
    d = conn.execute("SELECT total_out, window_out FROM outflow_done").fetchone()
    assert abs(d["total_out"] - after[0]) < 1e-6 and d["window_out"] == after[1]       # the done-row totals follow


def test_small_freezes_and_dust(tmp_path):
    conn = connect(tmp_path / "m.sqlite")
    _rows(conn, 5)
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, "0xdust", 0.4, iso(NOW), "0xd", iso(NOW - 3600)))
    assert maintenance.compact_outflows(conn) == 1                                    # only the dust row (< $1) went
    assert conn.execute("SELECT COUNT(*) FROM outflows").fetchone()[0] == 5 * 3 + 2
    # new transfers of a giant wallet are compacted as soon as the scan finishes
    conn2 = connect(tmp_path / "m2.sqlite")
    _rows(conn2, 800)
    outflows.finish(conn2, "eth", F, NOW - 3600, 2402, False)
    assert conn2.execute("SELECT COUNT(*) FROM outflows").fetchone()[0] <= maintenance.KEEP_RECIPIENTS + 3
    d = conn2.execute("SELECT total_out, n_transfers FROM outflow_done").fetchone()
    assert d["n_transfers"] == 2402 and d["total_out"] > 0


def test_label_checked_shrinks_and_prunes_unused(tmp_path):
    conn = connect(tmp_path / "m.sqlite")
    conn.execute("DROP TABLE label_checked")
    conn.execute("CREATE TABLE label_checked (network TEXT NOT NULL, key TEXT NOT NULL, checked_at TEXT, PRIMARY KEY (network, key))")   # old shape
    conn.execute("INSERT INTO outflows VALUES('eth',?,?,?,?,?,0,0,?)", (F, "0x" + "ab" * 20, 50.0, iso(NOW), "0xt", iso(NOW - 3600)))
    for k in ("0x" + "ab" * 20, "0x" + "cd" * 20, "0x" + "ef" * 20):
        conn.execute("INSERT INTO label_checked VALUES('ethereum',?,?)", (k, iso(NOW)))
    assert maintenance.shrink_label_checked(conn) == 3
    assert [r[1] for r in conn.execute("PRAGMA table_info(label_checked)")] == ["network", "key"]
    assert maintenance.shrink_label_checked(conn) == 0                                # already the new shape
    assert maintenance.prune_label_checked(conn) == 2                                 # cd.. and ef.. are no counterparty of anything stored
    assert [r[0] for r in conn.execute("SELECT key FROM label_checked")] == ["0x" + "ab" * 20]


def test_tron_calls_and_vacuum(tmp_path):
    conn = connect(tmp_path / "m.sqlite")
    old_ms = (NOW - 100 * 86400) * 1000
    conn.execute("INSERT INTO tron_calls VALUES('h_done',?,1,'s','submit',NULL,'dest','0xdeadbeef')", (old_ms,))
    conn.execute("INSERT INTO tron_calls VALUES('h_open',?,1,'s','submit',NULL,'dest','0xfeedface')", (old_ms,))
    conn.execute("INSERT INTO tron_calls VALUES('h_new',?,1,'s','submit',NULL,'dest','0xabcdef01')", (NOW * 1000,))
    ensure_proposal(conn, "tron", 1)
    update_proposal(conn, "tron", 1, submit_tx="h_done")
    assert maintenance.prune_tron_calls(conn) == 1                                    # only old calls of ASSEMBLED proposals lose their calldata
    assert conn.execute("SELECT data FROM tron_calls WHERE tx_hash='h_done'").fetchone()[0] is None
    assert conn.execute("SELECT data FROM tron_calls WHERE tx_hash='h_open'").fetchone()[0] == "0xfeedface"
    assert conn.execute("SELECT data FROM tron_calls WHERE tx_hash='h_new'").fetchone()[0] == "0xabcdef01"
    # vacuum only when a fifth of the file is free pages
    assert maintenance.vacuum_if_needed(conn) is False
    conn.execute("CREATE TABLE junk(x)")
    conn.executemany("INSERT INTO junk VALUES(?)", [("x" * 500,)] * 5000)
    conn.commit()
    conn.execute("DROP TABLE junk")
    conn.commit()
    assert maintenance.free_ratio(conn) > 0.2 and maintenance.vacuum_if_needed(conn) is True
    assert maintenance.free_ratio(conn) < 0.05
