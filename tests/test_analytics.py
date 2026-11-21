from freezewatch import analytics as A
from freezewatch.db import connect, ensure_proposal, update_proposal
from freezewatch.util import iso

DAY = 86400
T0 = 1_760_000_000  # fixed "now"


def test_attribution_burned_still_unfrozen_and_dup():
    ev = [
        (T0, "eth", "0xa", "freeze", None), (T0 + 10, "eth", "0xa", "destroy", 1234.5),      # burned
        (T0, "eth", "0xb", "freeze", None),                                                  # still frozen
        (T0, "eth", "0xc", "freeze", None), (T0 + 10, "eth", "0xc", "unfreeze", None),       # unfrozen
        (T0, "eth", "0xd", "freeze", None), (T0 + 5, "eth", "0xd", "freeze", None),          # duplicate freeze
        (T0, "eth", "0xe", "freeze", None),                                                  # still, balance unknown
        (T0, "sol_usdc", "tokacc", "freeze", 777.0),                                         # executed-only: at-tx amount
    ]
    bal = {("eth", "0xb"): (50.0, 50.0), ("eth", "0xc"): (0.0, 300.0)}
    fr, de, un = A.attribute(ev, bal)
    kinds = {(f[1], f[2], f[3]): f[4] for f in fr}
    assert kinds[("eth", "0xa", "burned")] == 1234.5
    assert kinds[("eth", "0xb", "still")] == 50.0
    assert kinds[("eth", "0xc", "unfrozen")] == 300.0     # balance last observed while frozen
    assert ("eth", "0xd", "dup") in kinds and kinds[("eth", "0xd", "still")] is None
    assert kinds[("eth", "0xe", "still")] is None
    assert kinds[("sol_usdc", "tokacc", "still")] == 777.0
    assert de == [(T0 + 10, "eth", "0xa", 1234.5)] and un == [(T0 + 10, "eth", "0xc", 300.0)]


def test_parse_destroy_logs_legacy_and_new_and_tron():
    amt = 5_000_000 * 10 ** 6
    addr = "2f37df0c24bcafbce1467bcd08459ce5fc01db85"
    legacy = {"topics": ["0x" + A.TOPIC_DESTROYED_LEGACY], "data": "0x" + addr.rjust(64, "0") + f"{amt:064x}"}
    assert A.parse_destroy_logs([legacy], "0x" + addr) == 5_000_000.0
    new = {"topics": ["0x" + A.TOPIC_DESTROYED_NEW, "0x" + addr.rjust(64, "0")], "data": "0x" + f"{12 * 10**6:064x}"}
    other = {"topics": ["0x" + A.TOPIC_DESTROYED_NEW, "0x" + "11".rjust(64, "0")], "data": "0x" + f"{1:064x}"}
    assert A.parse_destroy_logs([other, new], "0x" + addr) == 12.0   # picks the event of the target address
    tron = {"topics": [A.TOPIC_DESTROYED_LEGACY, addr.rjust(64, "0")], "data": f"{amt:064x}"}
    assert A.parse_destroy_logs([tron], addr) == 5_000_000.0
    assert A.parse_destroy_logs([{"topics": ["0xdead"], "data": "0x"}], addr) is None


def test_token_map_and_special_sinks():
    assert A.token_of("arb1") == "USDT0" and A.token_of("usdc_eth") == "USDC" and A.token_of("tron") == "USDT"
    assert A.special_sinks("eth") == {A.EVM_ZERO, "0xdac17f958d2ee523a2206206994597c13d831ec7"}
    assert A.special_sinks("tron") == {"T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"}
    assert "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48" in A.special_sinks("usdc_eth")


def test_compute_and_period_filtering(tmp_path):
    conn = connect(tmp_path / "a.sqlite")

    def prop(chain, tid, fn, target, executed_at, amount=None, status="executed", submitted=None):
        ensure_proposal(conn, chain, tid)
        update_proposal(conn, chain, tid, destination="x", function=fn, target=target, executed=int(status == "executed"),
                        executed_at=iso(executed_at), submitted_at=iso(submitted or executed_at - 600), status=status,
                        amount=amount, delay_seconds=600)
    old, recent = T0 - 400 * DAY, T0 - 10 * DAY
    prop("eth", 1, "addBlackList", "0xa1", old)
    prop("eth", 2, "destroyBlackFunds", "0xa1", old + 100, amount=1000.0)
    prop("eth", 3, "addBlackList", "0xb2", recent)
    prop("eth", 4, "addBlackList", "0xc3", recent, status="stale")
    prop("eth", 6, "addBlackList", A.EVM_ZERO, recent)  # special sink: never counted
    conn.execute("INSERT INTO balances(chain,address,token,balance,checked_at,frozen_balance) VALUES('eth','0xb2','USDT',250.0,'2026-01-01',250.0)")
    conn.commit()
    an = A.compute(conn, now=T0)
    all_ = A.summarize(an)
    assert all_["frozen_n"] == 2 and all_["burned_usd"] == 1000.0 and all_["frozen_usd"] == 1000.0 + 250.0
    assert all_["still_n"] == 1 and all_["still_usd"] == 250.0
    since12 = A.months_since("12m", T0)
    last = A.summarize(an, since=since12)
    assert last["frozen_n"] == 1 and last["burned_usd"] == 0 and last["never_n"] == 1  # old burn excluded
    assert A.summarize(an, token="USDC")["frozen_n"] == 0 and A.summarize(an, chains={"tron"})["frozen_n"] == 0
    assert an["coverage"]["total"] == 1 and an["coverage"]["checked"] == 1
    assert an["top"][0][:3] == ["eth", "0xb2", 250.0]
