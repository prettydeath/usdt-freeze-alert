from datetime import datetime, timezone

from freezewatch import daily, kyiv
from freezewatch.db import connect, ensure_proposal, meta_get, update_proposal
from freezewatch.util import iso


def ts(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def test_kyiv_day_bounds_plain_days():
    # winter: UTC+2, summer: UTC+3
    assert kyiv.day_bounds("2026-01-15") == (ts("2026-01-14 22:00"), ts("2026-01-15 22:00"))
    assert kyiv.day_bounds("2026-07-15") == (ts("2026-07-14 21:00"), ts("2026-07-15 21:00"))
    assert kyiv.day_of(ts("2026-07-14 20:59")) == "2026-07-14" and kyiv.day_of(ts("2026-07-14 21:00")) == "2026-07-15"
    assert kyiv.local_dt(ts("2026-01-15 07:00")).hour == 9


def test_kyiv_dst_change_days():
    # last Sunday of March 2026 = 29th (23 h day), last Sunday of October 2026 = 25th (25 h day)
    s, e = kyiv.day_bounds("2026-03-29")
    assert (s, e) == (ts("2026-03-28 22:00"), ts("2026-03-29 21:00")) and (e - s) == 23 * 3600
    s, e = kyiv.day_bounds("2026-10-25")
    assert (s, e) == (ts("2026-10-24 21:00"), ts("2026-10-25 22:00")) and (e - s) == 25 * 3600
    assert kyiv.dst_window(2026) == (ts("2026-03-29 01:00"), ts("2026-10-25 01:00"))
    assert kyiv.dst_window(2025) == (ts("2025-03-30 01:00"), ts("2025-10-26 01:00"))
    # the instant of the change itself
    assert kyiv.local_dt(ts("2026-03-29 00:59")).hour == 2 and kyiv.local_dt(ts("2026-03-29 01:00")).hour == 4
    assert kyiv.local_dt(ts("2026-10-25 00:59")).hour == 3 and kyiv.local_dt(ts("2026-10-25 01:00")).hour == 3
    # days tile without gaps across the changes
    for a, b in (("2026-03-28", "2026-03-29"), ("2026-03-29", "2026-03-30"), ("2026-10-24", "2026-10-25"), ("2026-10-25", "2026-10-26")):
        assert kyiv.day_bounds(a)[1] == kyiv.day_bounds(b)[0]
    assert kyiv.yesterday(ts("2026-03-30 06:30")) == "2026-03-29" and kyiv.add_days("2026-03-01", -1) == "2026-02-28"


def mk(conn, chain, tid, **vals):
    ensure_proposal(conn, chain, tid)
    base = dict(destination="x", function="addBlackList", target="TCKiW9WztxAWa6shxdoWn4L4wiKrHeokmd", executed=0, confirmations=1,
                required=2, status="pending")
    base.update(vals)
    update_proposal(conn, chain, tid, **base)


def test_collect_and_format(tmp_path):
    conn = connect(tmp_path / "d.sqlite")
    day = "2026-10-25"           # 25-hour day: 2026-10-24 21:00Z .. 2026-10-25 22:00Z
    mk(conn, "tron", 1, submitted_at=iso(ts("2026-10-24 21:00")), executed=1, status="executed", executed_at=iso(ts("2026-10-24 22:30")), delay_seconds=5400)
    mk(conn, "tron", 2, submitted_at=iso(ts("2026-10-25 21:59")), executed=1, status="executed", executed_at=iso(ts("2026-10-25 21:59")), delay_seconds=60)
    mk(conn, "tron", 3, submitted_at=iso(ts("2026-10-25 22:00")))      # next Kyiv day: out
    mk(conn, "tron", 4, submitted_at=iso(ts("2026-10-24 20:59")))      # previous Kyiv day: out
    mk(conn, "eth", 5, function="issue", amount=1_000_000.0, executed=1, status="executed", executed_at=iso(ts("2026-10-25 10:00")), submitted_at=iso(ts("2026-10-25 09:00")))
    conn.commit()
    d = daily.collect(conn, day)
    assert d["proposals"] == {"tron": 2} and d["executed"] == {"tron": 2} and d["activity"]
    assert d["supply"]["issued"] == 1_000_000.0 and d["supply"]["ni"] == 1
    assert d["delay"]["tron"] == {"median": 2730, "n": 2} and d["fastest"] == (60, "tron") and d["slowest"] == (5400, "tron")
    assert d["pending"]["n"] == 2
    txt = daily.fmt_daily(d)
    assert txt.startswith("📊 <b>DAILY REPORT</b> · 2026-10-25") and txt.endswith("#daily #report")
    assert "New freeze proposals: <b>2</b> (TRON 2)" in txt and "Supply (not freezes): issued $1,000,000" in txt
    assert "#analytics" in txt and "glossary" in txt and "Supply" in txt and "Pending now: <b>2</b>" in txt


def test_zero_activity_day(tmp_path):
    conn = connect(tmp_path / "z.sqlite")
    mk(conn, "tron", 9, submitted_at=iso(ts("2026-01-01 00:00")))
    txt = daily.fmt_daily(daily.collect(conn, "2026-02-02"))
    assert "no freeze activity yesterday" in txt and "Pending now: <b>1</b>" in txt and "Top freezes" not in txt


def test_trigger_dedupe_and_catchup(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    sent = []
    send = lambda t: sent.append(t)   # noqa: E731
    # 08:59 Kyiv (06:59Z in winter): not yet
    assert daily.maybe_send(conn, ts("2026-11-24 06:59"), send, True) is None and not sent
    # 09:00 sharp -> yesterday (23rd)
    assert daily.maybe_send(conn, ts("2026-11-24 07:00"), send, True) == "2026-11-23" and len(sent) == 1
    assert meta_get(conn, "daily_report:2026-11-23") and meta_get(conn, "daily_report_last") == "2026-11-23"
    # never twice
    assert daily.maybe_send(conn, ts("2026-11-24 07:05"), send, True) is None and len(sent) == 1
    assert daily.maybe_send(conn, ts("2026-11-24 21:00"), send, True) is None and len(sent) == 1
    # missed runs: first run after 09:00 is at 09:40 next day -> reports only that day's yesterday, older days are never back-filled
    assert daily.maybe_send(conn, ts("2026-11-25 07:40"), send, True) == "2026-11-24" and len(sent) == 2
    assert daily.due_day(conn, ts("2026-11-27 12:00")) == "2026-11-26" and meta_get(conn, "daily_report:2026-11-25") is None
    # not configured -> nothing sent, nothing marked
    assert daily.maybe_send(conn, ts("2026-11-27 12:00"), send, False) is None and meta_get(conn, "daily_report:2026-11-26") is None


def test_summer_time_trigger_and_failed_send(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    assert daily.due_day(conn, ts("2026-07-10 05:59")) is None          # 08:59 EEST
    assert daily.due_day(conn, ts("2026-07-10 06:00")) == "2026-07-09"  # 09:00 EEST

    def boom(_t):
        raise RuntimeError("telegram down")
    assert daily.maybe_send(conn, ts("2026-07-10 06:00"), boom, True) is None
    assert meta_get(conn, "daily_report:2026-07-09") is None            # retried by the next run
    assert daily.maybe_send(conn, ts("2026-07-10 06:05"), lambda t: None, True) == "2026-07-09"


def test_analytics_daily_hourly_and_windows(tmp_path):
    from freezewatch import analytics
    conn = connect(tmp_path / "w.sqlite")
    now = ts("2026-11-24 10:30")            # 12:30 Kyiv, day 2026-11-24 (00:00 Kyiv = 2026-11-23 22:00Z)
    for i, when in enumerate(("2026-11-23 21:59", "2026-11-23 22:00", "2026-11-24 09:15", "2026-11-10 08:00")):
        mk(conn, "tron", 100 + i, target=f"T{i}" + "a" * 30, submitted_at=iso(ts(when) - 60), executed=1, status="executed",
           executed_at=iso(ts(when)), delay_seconds=60)
    conn.commit()
    an = analytics.compute(conn, now)
    d, h = an["daily"]["tron"], an["hourly"]["tron"]
    assert d["2026-11-23"][0] == 1 and d["2026-11-24"][0] == 2              # 21:59Z is still the 23rd in Kyiv, 22:00Z starts the 24th
    assert "2026-11-10" not in d                                            # older than DAILY_DAYS (14 days incl. today: from 2026-11-11)
    assert h["2026-11-24T11"][0] == 1 and h["2026-11-24T00"][0] == 1 and h["2026-11-23T23"][0] == 1
    assert sum(v[0] for v in an["monthly"]["tron"].values()) == 4 and an["tz"] == "Europe/Kyiv"
    assert analytics.period_cutoff("today", None, now) == ts("2026-11-23 22:00")
    assert analytics.period_cutoff("7d", None, now) == ts("2026-11-17 22:00")
    assert analytics.period_cutoff("30d", 30, now) == now - 30 * 86400
    assert set(an["inflow"]["periods"]) == {"today", "7d", "30d", "90d", "12m", "all"}


def test_period_windows_payload(tmp_path):
    from freezewatch import render
    conn = connect(tmp_path / "pw.sqlite")
    w = render.period_windows(conn, {}, {}, {}, set(), ts("2026-03-30 08:00"))   # day after the spring DST change
    assert w["today"]["from"] == iso(ts("2026-03-29 21:00")) and w["today"]["to"] == iso(ts("2026-03-30 21:00"))
    assert w["7d"]["from"] == iso(kyiv.start_of_day("2026-03-24")) and w["7d"]["reasons"]["freezes"] == 0
