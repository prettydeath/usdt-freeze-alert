import threading
import time

import pytest
import requests

from freezewatch import cli, deadline, outflows, util
from freezewatch.db import connect


@pytest.fixture(autouse=True)
def _clean():
    deadline.stop()
    yield
    deadline.stop()


def test_unlimited_outside_a_run():
    assert not deadline.started() and deadline.optional_remaining() == float("inf")
    assert deadline.cap(50) == 50 and not deadline.expired() and not deadline.http_stop()


def test_optional_budget_is_capped_and_keeps_the_render_reserve(monkeypatch):
    deadline.start(total=deadline.RESERVE + 100)
    assert 99 <= deadline.optional_remaining() <= 100       # the reserve for render + publish is never handed out
    assert deadline.cap(500) <= 100 and deadline.cap(20) == 20
    st = deadline.step("balances", 500)
    assert st.budget <= 100 and not st.skipped
    deadline.stop()
    deadline.start(total=deadline.RESERVE - 1)              # reserve alone is larger than the deadline: nothing optional is left
    assert deadline.expired() and deadline.step("x", 30).skipped


def test_http_json_stops_when_the_step_budget_is_over(monkeypatch):
    calls = []
    monkeypatch.setattr(util._session, "request", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(requests.ConnectionError("x")))
    monkeypatch.setattr(deadline, "RESERVE", 0.0)
    deadline.start(total=100)
    with deadline.step("inflows", 0.2):
        time.sleep(0.3)                                     # the step budget is already over
        with pytest.raises(util.HttpError, match="deadline"):
            util.http_json("GET", "https://example.org/x", retries=6)
    assert calls == []                                      # no request was even started
    # core code (no active optional step) is never cut by the deadline
    monkeypatch.setattr(util.time, "sleep", lambda s: None)
    with pytest.raises(util.HttpError, match="retries exhausted"):
        util.http_json("GET", "https://example.org/x", retries=3)
    assert len(calls) == 3


def test_http_json_caps_total_time_and_uses_hard_timeouts(monkeypatch):
    seen = []
    monkeypatch.setattr(util._session, "request", lambda *a, **k: seen.append(k["timeout"]) or (_ for _ in ()).throw(requests.Timeout("t")))
    slept = []
    monkeypatch.setattr(util.time, "sleep", lambda s: slept.append(s))
    with pytest.raises(util.HttpError):
        util.http_json("GET", "https://example.org/x", retries=6, timeout=40, max_total=0.0)
    assert len(seen) == 1 and seen[0] == (5, 30)             # connect 5 s, read capped at 30 s
    assert util._timeout(None) == (5, 20) and util._timeout(10) == (5, 10)


def test_run_guarded_abandons_a_hung_step(monkeypatch):
    monkeypatch.setattr(deadline, "GRACE", 0.2)
    ev = threading.Event()
    t0 = time.time()
    assert deadline.run_guarded(lambda: ev.wait(10), "hang", 0.1) is False
    assert time.time() - t0 < 3                              # did not wait for the hung step
    ev.set()
    assert deadline.run_guarded(lambda: None, "fine", 1) is True
    assert deadline.run_guarded(lambda: 1 / 0, "boom", 1) is True   # an error is logged, not raised


def test_pagination_loop_stops_inside_and_resumes_from_the_cursor():
    transfers = {b: {"topics": [outflows.TRANSFER_TOPIC, "0x" + "0" * 24 + "11" * 20, "0x" + "0" * 24 + "22" * 20], "data": hex(1_000_000),
                     "timeStamp": hex(b), "transactionHash": f"0x{b:x}", "logIndex": "0x0", "blockNumber": hex(b)} for b in range(0, 4000)}
    calls = []

    def page(chain, addr, a, b):
        calls.append((a, b))
        return [transfers[n] for n in range(a, b + 1)][:1000]
    stop_after = {"n": 3}

    def stop():
        return len(calls) >= stop_after["n"]
    rows1, trunc, pend, used = outflows.fetch_evm_out("eth", "0x" + "11" * 20, 0, 3999, page=page, stop=stop)
    assert used == 3 and pend and not trunc                  # stopped INSIDE the bisection after 3 queries, work left
    stop_after["n"] = 10 ** 9
    rows2, trunc, pend2, _ = outflows.fetch_evm_out("eth", "0x" + "11" * 20, 0, 0, page=page, stop=stop, ranges=pend)
    assert pend2 == [] and not trunc
    assert len({r[3] for r in rows1 + rows2}) == 4000        # nothing lost, nothing duplicated across the two runs


def test_tron_paging_stops_and_resumes_with_the_fingerprint():
    state = {"calls": 0}

    def get(url, params):
        state["calls"] += 1
        fp = params.get("fingerprint")
        n = int(fp) if fp else 0
        return {"data": [{"type": "Transfer", "value": "1000000", "to": f"T{n}", "block_timestamp": 1000 + n, "transaction_id": f"t{n}"}],
                "meta": {"fingerprint": str(n + 1)} if n < 4 else {}}
    rows, trunc, fp, pages = outflows.fetch_tron_out("Tx", 0, 10 ** 6, get=get, stop=lambda: state["calls"] >= 2)
    assert fp == "2" and pages == 2 and len(rows) == 2 and not trunc
    rows2, trunc2, fp2, pages2 = outflows.fetch_tron_out("Tx", 0, 10 ** 6, get=get, fp=fp, pages_done=pages)
    assert fp2 is None and len(rows2) == 3 and not trunc2 and pages2 == 5


def test_core_always_runs_and_optional_steps_are_skipped_when_the_deadline_is_gone(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEZEWATCH_RUN_DEADLINE_SEC", "0")  # the deadline is over before the run starts
    conn = connect(tmp_path / "d.sqlite")
    log = []
    monkeypatch.setattr(cli, "chains_of", lambda arg: ["eth", "tron"])
    monkeypatch.setattr(cli, "do_sync", lambda c, chain, since, budget, phase="all": log.append(("sync", chain, phase)) or True)
    monkeypatch.setattr(cli, "refresh_balances", lambda c: log.append(("core", "balances-open")))
    monkeypatch.setattr(cli.health, "check", lambda c: log.append(("core", "health")))
    monkeypatch.setattr(cli.alerts, "run_alerts", lambda *a, **k: log.append(("core", "alerts")) or 0)
    for mod, name in ((cli.signals, "detect"), (cli.signals, "resolve"), (cli.analytics, "enrich_destroy_amounts"),
                      (cli.analytics, "refresh_balances"), (cli.inflows, "refresh"), (cli.outflows, "refresh"), (cli.labels, "refresh")):
        monkeypatch.setattr(mod, name, lambda *a, _n=name, **k: log.append(("optional", _n)))

    class A:
        budget = 300
        chain = "all"
    assert cli.cmd_update(A(), conn) == 0
    assert [x for x in log if x[0] == "sync"] == [("sync", "tron", "core"), ("sync", "eth", "core")]   # core sync always runs, TRON first (time-critical pending proposals)
    assert [x for x in log if x == ("core", "alerts")] == [("core", "alerts")] * 2 and ("core", "health") in log   # alerts: right after core, again at the end                                    # so do the cheap core steps
    assert not [x for x in log if x[0] == "optional"]                                                 # every optional step skipped
    flags = {n: f for n, s, f in deadline.timings()}
    assert flags["signals"] == "skipped" and flags["outflows"] == "skipped" and flags["labels"] == "skipped"
    assert "timings" in deadline.summary() and "outflows 0s (skipped)" in deadline.summary()
    assert conn.execute("SELECT value FROM meta WHERE key='run_timings'").fetchone()                  # saved for the Status tab


def test_optional_steps_run_with_a_capped_budget_when_time_is_left(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEZEWATCH_RUN_DEADLINE_SEC", "1000")
    conn = connect(tmp_path / "d.sqlite")
    budgets = {}
    monkeypatch.setattr(cli, "chains_of", lambda arg: ["eth"])
    monkeypatch.setattr(cli, "do_sync", lambda *a, **k: True)
    monkeypatch.setattr(cli, "refresh_balances", lambda c: None)
    monkeypatch.setattr(cli.health, "check", lambda c: None)
    monkeypatch.setattr(cli.alerts, "run_alerts", lambda *a, **k: 0)
    monkeypatch.setattr(cli.signals, "detect", lambda *a, **k: None)
    monkeypatch.setattr(cli.signals, "resolve", lambda *a, **k: None)
    for mod, name in ((cli.analytics, "enrich_destroy_amounts"), (cli.analytics, "refresh_balances"), (cli.inflows, "refresh"),
                      (cli.labels, "refresh")):
        monkeypatch.setattr(mod, name, lambda c, b, _n=name: budgets.setdefault(_n, b))
    monkeypatch.setattr(cli.outflows, "refresh", lambda c, b: budgets.setdefault("outflows", b))
    monkeypatch.setenv("ANALYTICS_BUDGET_SEC", "50")

    class A:
        budget = 300
        chain = "all"
    cli.cmd_update(A(), conn)
    assert budgets["outflows"] == pytest.approx(50, abs=1) and budgets["refresh"] == pytest.approx(50, abs=1)   # inflows / labels
