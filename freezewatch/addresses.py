"""Public address index for the dashboard's watchlist / search (site/addresses.json).

The index is keyed by a normalised address; EVM addresses are lowercase and TRON addresses are converted to their
20-byte hex (so the same key matches both). Lookups happen entirely in the browser, which loads this file lazily - the
addresses a visitor checks never leave the browser. Compact short keys keep the file small; if it grows beyond
SHARD_LIMIT it is split into site/addr/<first hex char>.json shards and addresses.json becomes a manifest."""
import json
import re

from . import analytics
from .constants import DESTROY_FUNCS, FREEZE_FUNCS, UNFREEZE_FUNCS
from .util import b58check_decode, parse_iso

SHARD_LIMIT = 5_000_000
B58 = r"[1-9A-HJ-NP-Za-km-z]"


def canon(addr):
    """Normalise any supported address form (EVM, TRON, Aptos, TON raw, NEAR, Solana) -> key or None."""
    a = (addr or "").strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", a) or re.fullmatch(r"0x[0-9a-fA-F]{64}", a):
        return a.lower()
    if re.fullmatch(r"0:[0-9a-fA-F]{64}", a):
        return a.lower()
    if re.fullmatch(r"T" + B58 + r"{33}", a):
        try:
            p = b58check_decode(a)
            if len(p) == 21 and p[0] == 0x41:
                return "0x" + p[1:].hex()
        except Exception:  # noqa: BLE001
            return None
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", a):
        return a.lower()  # NEAR implicit account
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", a) and ("." in a or a.endswith("near")):
        return a
    if re.fullmatch(B58 + r"{32,44}", a):
        return a  # Solana (case-sensitive)
    return None


def canon_for(chain, addr):
    """Key of an address as stored for `chain` (chain-aware: stored non-EVM names are lowercased/kept)."""
    k = canon(addr)
    if k:
        return k
    return addr.lower() if addr and chain in ("near", "aptos", "ton") else addr


def _kind(fn):
    if fn in FREEZE_FUNCS:
        return "f"
    if fn in UNFREEZE_FUNCS:
        return "u"
    if fn in DESTROY_FUNCS:
        return "b"
    return None


def build(conn):
    """{key: {p, e, s, b, i}} - proposals (live/stale), executed events, current state, balances, inflows."""
    idx = {}

    def rec(key):
        return idx.setdefault(key, {})

    sinks = {}

    def skip(chain, addr):
        return addr in sinks.setdefault(chain, analytics.special_sinks(chain))

    for r in conn.execute("SELECT chain, tx_id, function, target, confirmations, required, status, submitted_at, executed, "
                          "executed_at, exec_tx, amount, delay_seconds FROM proposals WHERE target IS NOT NULL"):
        k = _kind(r["function"])
        if not k or skip(r["chain"], analytics.norm(r["chain"], r["target"])):
            continue
        key = canon_for(r["chain"], r["target"])
        if not key:
            continue
        if r["executed"] and r["executed_at"]:
            rec(key).setdefault("e", []).append([r["chain"], k, parse_iso(r["executed_at"]), r["exec_tx"],
                                                 r["amount"], r["delay_seconds"]])
        elif r["status"] in ("pending", "stale"):
            rec(key).setdefault("p", []).append([r["chain"], r["tx_id"], r["function"], r["confirmations"], r["required"],
                                                 parse_iso(r["submitted_at"]), r["status"]])
    for f in conn.execute("SELECT source, subject, owner, action, ts, tx_hash, amount FROM freezes"):
        k = "f" if f["action"] == "freeze" else "u"
        ts = parse_iso(f["ts"])
        rec(canon_for(f["source"], f["subject"]))["e"] = rec(canon_for(f["source"], f["subject"])).get("e", []) + [
            [f["source"], k, ts, f["tx_hash"], f["amount"], None]]
        if f["owner"] and f["owner"] != f["subject"] and canon_for(f["source"], f["owner"]):
            o = rec(canon_for(f["source"], f["owner"]))
            o["e"] = o.get("e", []) + [[f["source"], k, ts, f["tx_hash"], f["amount"], None, f["subject"]]]
    for r in conn.execute("SELECT chain, address, balance FROM balances WHERE balance IS NOT NULL"):
        if skip(r["chain"], r["address"]):
            continue
        key = canon_for(r["chain"], r["address"])
        rec(key).setdefault("b", {})[r["chain"]] = round(r["balance"], 2)
    senders = {}
    for r in conn.execute("SELECT chain, frozen_address, sender, amount FROM inflows_after_freeze"):
        if skip(r["chain"], r["frozen_address"]):
            continue
        d = senders.setdefault((r["chain"], r["frozen_address"]), {})
        e = d.setdefault(r["sender"], [0, 0.0])
        e[0] += 1
        e[1] += r["amount"]
    for (chain, addr), d in senders.items():
        top = sorted(d.items(), key=lambda kv: -kv[1][1])[:3]
        key = canon_for(chain, addr)
        rec(key).setdefault("i", {})[chain] = [sum(v[0] for v in d.values()), round(sum(v[1] for v in d.values()), 2),
                                               [[s, v[0], round(v[1], 2)] for s, v in top]]
    # current state per chain: last freeze-type event decides
    for key, r in idx.items():
        st = {}
        for e in sorted(r.get("e", []), key=lambda x: x[2] or 0):
            st[e[0]] = {"f": "frozen", "u": "unfrozen", "b": "burned"}[e[1]]
        if st:
            r["s"] = st
        if "e" in r:
            r["e"].sort(key=lambda x: x[2] or 0)
    return idx


def write(site_dir, idx):
    """site/addresses.json (or shards + manifest if too large). Returns {file: bytes}."""
    site_dir.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"v": 1, "a": idx}, separators=(",", ":"), ensure_ascii=False)
    sizes = {}
    shard_dir = site_dir / "addr"
    old = site_dir / "addresses.json"
    if len(blob.encode()) <= SHARD_LIMIT:
        _write_if_changed(old, blob)
        sizes["addresses.json"] = len(blob.encode())
        if shard_dir.exists():
            for f in shard_dir.glob("*.json"):
                f.unlink()
        return sizes
    shards = {}
    for k, v in idx.items():
        s = k[2] if k.startswith("0x") else "_"
        shards.setdefault(s, {})[k] = v
    shard_dir.mkdir(exist_ok=True)
    for s, part in shards.items():
        text = json.dumps({"v": 1, "a": part}, separators=(",", ":"), ensure_ascii=False)
        _write_if_changed(shard_dir / f"{s}.json", text)
        sizes[f"addr/{s}.json"] = len(text.encode())
    manifest = json.dumps({"v": 1, "sharded": True, "shards": sorted(shards)}, separators=(",", ":"))
    _write_if_changed(old, manifest)
    sizes["addresses.json"] = len(manifest.encode())
    return sizes


def _write_if_changed(path, text):
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")
