"""Exposure of a freeze target at proposal time: who can still move its USDT?

- ERC-20 / TRC-20 allowances the target granted (Approval logs with owner = target, then the current allowance(owner, spender),
  Permit2 included): a spender with an allowance can pull the funds after the freeze proposal is public.
- EIP-7702: an Ethereum account whose code starts with 0xef0100 is delegated to a contract (the delegate can act as the account).
Stored in `exposure`, shown on the address page and in the FREEZE INCOMING alert. Network access is injectable for the tests."""
import logging
import time

from . import deadline, eth, trongrid
from .constants import ETH_USDT, TRON_USDT, USDT_DECIMALS
from .util import iso, now_ts, parse_iso, tron_from_hex20, tron_to_hex20

log = logging.getLogger("freezewatch.exposure")
TOPIC_APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
SEL_ALLOWANCE = "dd62ed3e"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
UNLIMITED = 2 ** 200
MAX_SPENDERS = 12
RECHECK_SEC = 1800
FREEZE_FNS = ("addBlackList", "addToBlockedList", "freeze_accounts", "freeze_wallet")


def pad(a):
    return a.lower().removeprefix("0x").rjust(64, "0")


def delegate_of(code):
    """EIP-7702 delegation designator 0xef0100 + 20 bytes -> delegate address, else None."""
    c = (code or "").lower()
    return "0x" + c[8:48] if c.startswith("0xef0100") and len(c) >= 48 else None


def eth_exposure(addr, get_code=None, spenders=None, allowance=None):
    """-> [(kind, spender, amount, note)] for an Ethereum address. The three accessors are injectable for tests."""
    get_code = get_code or (lambda a: eth.rpc_batch([("eth_getCode", [a, "latest"])], retries=2)[0])
    spenders = spenders or _approval_spenders
    allowance = allowance or _eth_allowance
    out = []
    d = delegate_of(get_code(addr))
    if d:
        out.append(("eip7702", d, None, "account code delegates to this contract (EIP-7702)"))
    seen = {PERMIT2}
    for sp in [PERMIT2] + [s for s in spenders(addr) if s not in seen][:MAX_SPENDERS]:
        seen.add(sp)
        try:
            amt = allowance(addr, sp)
        except Exception as e:  # noqa: BLE001
            log.debug("allowance %s: %s", sp[:10], str(e)[:50])
            continue
        if amt and amt > 0:
            out.append(("allowance", sp, amt, "unlimited" if amt >= UNLIMITED else None))
    return out


def _approval_spenders(owner):
    o = eth._es({"module": "logs", "action": "getLogs", "address": ETH_USDT, "topic0": TOPIC_APPROVAL, "topic1": "0x" + pad(owner),
                 "topic0_1_opr": "and", "fromBlock": 0, "toBlock": "latest", "page": 1, "offset": 1000})
    res = o["result"] if o.get("status") == "1" else []
    last = {}
    for lg in res:                       # the newest approval per spender decides who to check
        last["0x" + lg["topics"][2][-40:]] = eth._h(lg["blockNumber"])
    return [s for s, _b in sorted(last.items(), key=lambda kv: -kv[1])]


def _eth_allowance(owner, spender):
    r = eth.rpc_batch([("eth_call", [{"to": ETH_USDT, "data": "0x" + SEL_ALLOWANCE + pad(owner) + pad(spender)}, "latest"])], retries=2)[0]
    v = int(r, 16) if r not in (None, "0x") else 0
    return UNLIMITED if v >= UNLIMITED else v / 10 ** USDT_DECIMALS


def tron_exposure(addr, approvals=None, allowance=None):
    """-> [(kind, spender, amount, note)] from the target's recent TRC-20 approvals."""
    approvals = approvals or _tron_approvals
    allowance = allowance or _tron_allowance
    out = []
    for sp in approvals(addr)[:MAX_SPENDERS]:
        try:
            amt = allowance(addr, sp)
        except Exception as e:  # noqa: BLE001
            log.debug("tron allowance %s: %s", sp[:10], str(e)[:50])
            continue
        if amt and amt > 0:
            out.append(("allowance", sp, amt, "unlimited" if amt >= UNLIMITED else None))
    return out


def _tron_approvals(owner):
    o = trongrid.request("GET", f"/v1/accounts/{owner}/transactions/trc20",
                         params={"only_from": "true", "contract_address": TRON_USDT, "limit": 200, "order_by": "block_timestamp,desc"},
                         max_sec=25)
    seen = []
    for r in o.get("data") or []:
        if r.get("type") == "Approval" and r.get("to") not in seen:
            seen.append(r["to"])
    return seen


def _tron_allowance(owner, spender):
    from . import tron
    param = pad(tron_to_hex20(owner)) + pad(tron_to_hex20(spender))
    r = tron.constant_call("allowance(address,address)", param, contract=TRON_USDT)
    v = int(r, 16)
    return UNLIMITED if v >= UNLIMITED else v / 10 ** USDT_DECIMALS


def targets(conn, now=None):
    """[(chain, address)] pending freeze proposals (eth / tron) not checked in the last RECHECK_SEC."""
    now = now or now_ts()
    from . import analytics
    out = []
    for r in conn.execute("SELECT chain, target FROM proposals WHERE status='pending' AND function IN (?,?,?,?) AND target IS NOT NULL "
                          "AND chain IN ('eth','tron')", FREEZE_FNS):
        a = analytics.norm(r["chain"], r["target"])
        mark = conn.execute("SELECT checked_at FROM exposure WHERE chain=? AND address=? AND kind='checked'", (r["chain"], a)).fetchone()
        if mark and now - parse_iso(mark[0]) < RECHECK_SEC:
            continue
        out.append((r["chain"], a))
    return sorted(set(out))


def refresh(conn, budget_sec=25, now=None, eth_fn=eth_exposure, tron_fn=tron_exposure):
    """Check the exposure of pending targets. -> number of targets checked."""
    t0 = time.time()
    n = 0
    for chain, addr in targets(conn, now):
        if time.time() - t0 > budget_sec or deadline.http_stop():
            break
        try:
            found = tron_fn(addr) if chain == "tron" else eth_fn(addr)
        except Exception as e:  # noqa: BLE001
            log.debug("exposure %s %s: %s", chain, addr[:10], str(e)[:60])
            continue
        conn.execute("DELETE FROM exposure WHERE chain=? AND address=?", (chain, addr))
        conn.execute("INSERT INTO exposure(chain,address,kind,spender,amount,note,checked_at) VALUES(?,?,?,?,?,?,?)",
                     (chain, addr, "checked", "", None, None, iso(now_ts())))
        for kind, sp, amt, note in found:
            conn.execute("INSERT OR REPLACE INTO exposure(chain,address,kind,spender,amount,note,checked_at) VALUES(?,?,?,?,?,?,?)",
                         (chain, addr, kind, sp, float(amt) if amt is not None else None, note, iso(now_ts())))   # 2**200 does not fit an SQLite integer
        n += 1
    conn.commit()
    return n


def lines(conn, chain, addr):
    """Plain-text warning lines for the alert (empty when nothing was found or the target was not checked)."""
    from . import analytics
    out = []
    for r in conn.execute("SELECT kind, spender, amount, note FROM exposure WHERE chain=? AND address=? AND kind<>'checked' "
                          "ORDER BY kind, amount DESC", (chain, analytics.norm(chain, addr))):
        if r["kind"] == "eip7702":
            out.append(f"⚠️ EIP-7702 delegated to {r['spender']}")
        else:
            amt = "unlimited" if (r["amount"] or 0) >= UNLIMITED else f"${r['amount']:,.0f}"
            sp = "Permit2" if r["spender"].lower() == PERMIT2 else r["spender"]
            out.append(f"⚠️ has allowance ({amt}) to {sp}")
    return out
