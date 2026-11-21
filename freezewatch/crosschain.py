"""Крос-чейн перегляд: одна й та сама цільова адреса, заморожена/запропонована в різних мережах.
EVM-адреси нормалізуються в lowercase; TRON-адреса (base58) конвертується в той самий 20-байтний hex, тож збігається з EVM."""
import logging
from collections import defaultdict

from . import eth, evmmsw
from .constants import DESTROY_FUNCS, FREEZE_FUNCS, SEL_BALANCE_OF, UNFREEZE_FUNCS, USDT_DECIMALS
from .registry import EVM_CHAINS, MSW_CHAINS, SAFE_CHAINS, chain_meta
from .util import b58check_decode, parse_iso

log = logging.getLogger("freezewatch.crosschain")


def action_class(function):
    if function in FREEZE_FUNCS:
        return "freeze"
    if function in UNFREEZE_FUNCS:
        return "unfreeze"
    if function in DESTROY_FUNCS:
        return "destroy"
    return None


def norm_addr(chain, addr):
    if not addr:
        return None
    if chain == "tron":
        try:
            p = b58check_decode(addr)
            return "0x" + p[1:].hex()
        except Exception:  # noqa: BLE001
            return None
    if chain in EVM_CHAINS or chain in ("usdc_eth", "near"):
        a = addr.lower()
        return a if a.startswith("0x") and len(a) == 42 else None
    return None  # Solana/NEAR/Aptos/TON - окремі простори адрес


def collect(conn):
    """[{address, items:[{chain,class,status,ts,tx_id,function}]}] для цілей, що мають хоч одну дію."""
    by = defaultdict(list)
    for r in conn.execute("SELECT chain,tx_id,function,target,status,submitted_at,executed_at FROM proposals "
                          "WHERE target IS NOT NULL"):
        cls = action_class(r["function"])
        a = norm_addr(r["chain"], r["target"])
        if cls and a:
            by[a].append({"chain": r["chain"], "class": cls, "status": r["status"], "tx_id": r["tx_id"],
                          "function": r["function"], "ts": r["executed_at"] or r["submitted_at"]})
    for r in conn.execute("SELECT source,subject,action,ts FROM freezes WHERE source='usdc_eth'"):
        by[r["subject"].lower()].append({"chain": r["source"], "class": r["action"], "status": "executed",
                                         "tx_id": None, "function": "blacklist", "ts": r["ts"]})
    return by


def pairs(conn):
    """Множина (адреса, мережа) для виявлення нових появ."""
    return {(a, it["chain"]) for a, its in collect(conn).items() for it in its}


def table(conn, limit=200):
    """Крос-чейн таблиця: адреси, присутні у >= 2 мережах (за всю історію), новіші першими."""
    out = []
    for a, its in collect(conn).items():
        chains = {it["chain"] for it in its}
        if len(chains) < 2:
            continue
        its.sort(key=lambda x: x["ts"] or "")
        out.append({"address": a, "n_chains": len(chains), "first": its[0]["ts"], "last": its[-1]["ts"],
                    "items": its})
    out.sort(key=lambda x: x["last"] or "", reverse=True)
    return out[:limit], len(out)


# ---- баланси на інших мережах ----
def rpc_and_token(chain):
    m = chain_meta().get(chain, {})
    if chain == "eth":
        return eth.rpc_url(), m.get("token")
    if chain in MSW_CHAINS:
        return MSW_CHAINS[chain]["rpc"], MSW_CHAINS[chain]["token"]
    if chain in SAFE_CHAINS:
        return SAFE_CHAINS[chain].get("rpc"), SAFE_CHAINS[chain]["token"]
    return None, None


def balances_elsewhere(addr, exclude):
    """Баланси USDT адреси (>0) у всіх EVM-мережах із RPC, окрім уже задіяних. Помилки мереж ігноруються."""
    res = {}
    for ch in ["eth", *MSW_CHAINS, *SAFE_CHAINS]:
        if ch in exclude:
            continue
        url, token = rpc_and_token(ch)
        if not url:
            continue
        try:
            v = eth.rpc_batch([("eth_call", [{"to": token, "data": "0x" + SEL_BALANCE_OF + "0" * 24 + addr[2:]},
                                             "latest"])], url=url)[0]
            bal = int(v, 16) / 10 ** USDT_DECIMALS
            if bal > 0:
                res[ch] = bal
        except Exception as e:  # noqa: BLE001
            log.debug("баланс %s: %s", ch, e)
    return res


def balance_on(chain, addr):
    """Баланс USDT адреси в EVM-мережі з RPC (для відкритих пропозицій Safe-мереж)."""
    url, token = rpc_and_token(chain)
    v = eth.rpc_batch([("eth_call", [{"to": token, "data": "0x" + SEL_BALANCE_OF + "0" * 24 + addr[2:]}, "latest"])],
                      url=url)[0]
    return int(v, 16) / 10 ** USDT_DECIMALS
