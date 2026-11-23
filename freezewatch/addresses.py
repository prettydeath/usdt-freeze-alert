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


def build(conn, extras=None):
    """{key: {p, e, s, b, i}} - proposals (live/stale), executed events, current state, balances, inflows."""
    idx = {}

    def rec(key):
        return idx.setdefault(key, {})

    sinks = {}

    def skip(chain, addr):
        return addr in sinks.setdefault(chain, analytics.special_sinks(chain))

    for r in conn.execute("SELECT chain, tx_id, function, target, confirmations, required, status, submitted_at, executed, "
                          "executed_at, exec_tx, amount, delay_seconds, public_proposal, balance_now FROM proposals WHERE target IS NOT NULL"):
        k = _kind(r["function"])
        if not k or skip(r["chain"], analytics.norm(r["chain"], r["target"])):
            continue
        key = canon_for(r["chain"], r["target"])
        if not key:
            continue
        if r["executed"] and r["executed_at"]:
            # chain, kind, executed ts, tx, amount, delay, token account, proposal id, signers, required, submitted ts, public
            rec(key).setdefault("e", []).append([r["chain"], k, parse_iso(r["executed_at"]), r["exec_tx"], r["amount"],
                                                 r["delay_seconds"], None, r["tx_id"], r["confirmations"], r["required"],
                                                 parse_iso(r["submitted_at"]), r["public_proposal"]])
        elif r["status"] in ("pending", "stale"):
            rec(key).setdefault("p", []).append([r["chain"], r["tx_id"], r["function"], r["confirmations"], r["required"],
                                                 parse_iso(r["submitted_at"]), r["status"], r["balance_now"]])
    for f in conn.execute("SELECT source, subject, owner, action, ts, tx_hash, amount FROM freezes"):
        k = "f" if f["action"] == "freeze" else "u"
        ts = parse_iso(f["ts"])
        rec(canon_for(f["source"], f["subject"]))["e"] = rec(canon_for(f["source"], f["subject"])).get("e", []) + [
            [f["source"], k, ts, f["tx_hash"], f["amount"], None, None, None, None, None, None, 0]]
        if f["owner"] and f["owner"] != f["subject"] and canon_for(f["source"], f["owner"]):
            o = rec(canon_for(f["source"], f["owner"]))
            o["e"] = o.get("e", []) + [[f["source"], k, ts, f["tx_hash"], f["amount"], None, f["subject"], None, None, None, None, 0]]
    for r in conn.execute("SELECT chain, address, balance, checked_at FROM balances WHERE balance IS NOT NULL"):
        if skip(r["chain"], r["address"]):
            continue
        key = canon_for(r["chain"], r["address"])
        rec(key).setdefault("b", {})[r["chain"]] = round(r["balance"], 2)
        if r["checked_at"]:
            rec(key).setdefault("bc", {})[r["chain"]] = r["checked_at"]   # date the balance was read (stale for unfrozen addresses)
    senders = {}
    spans = {}
    for r in conn.execute("SELECT chain, frozen_address, sender, amount, ts FROM inflows_after_freeze"):
        if skip(r["chain"], r["frozen_address"]):
            continue
        d = senders.setdefault((r["chain"], r["frozen_address"]), {})
        e = d.setdefault(r["sender"], [0, 0.0])
        e[0] += 1
        e[1] += r["amount"]
        ts = parse_iso(r["ts"])
        sp = spans.setdefault((r["chain"], r["frozen_address"]), [ts, ts])
        sp[0], sp[1] = min(sp[0], ts), max(sp[1], ts)
    for (chain, addr), d in senders.items():
        top = sorted(d.items(), key=lambda kv: -kv[1][1])[:3]
        key = canon_for(chain, addr)
        first, last = spans[(chain, addr)]
        rec(key).setdefault("i", {})[chain] = [sum(v[0] for v in d.values()), round(sum(v[1] for v in d.values()), 2),
                                               [[s, v[0], round(v[1], 2)] for s, v in top], first, last]
    if extras:
        _add_insights(idx, rec, extras)
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


def _label_arr(x):
    """label row -> compact [entity, label, category, source, source_url, confidence] (wallet-attribution, never our claim)."""
    return [x["entity"], x["label"], x["category"], x["source"], x["source_url"], x["confidence"]] if x else None


def _add_insights(idx, rec, extras):
    """Own label (o: reason hints / sanctions), outflows before freezes (o), clusters (c), cross-chain signals (x)."""
    from .labels import NETWORK_OF, key_of
    lab = extras["labels"]
    for key, r in list(idx.items()):
        chains = {e[0] for e in r.get("e", [])} | {p[0] for p in r.get("p", [])}
        for ch in chains:
            net = NETWORK_OF.get(ch)
            x = lab.get((net, key_of(net, key))) if net else None
            if x:
                r.setdefault("l", {})[ch] = _label_arr(x)
    for (chain, addr), freezes in extras.get("outflow_addr", {}).items():
        key = canon_for(chain, addr)
        if not key:
            continue
        lst = []
        for f in freezes[-3:]:
            # freeze time, escaped $, escaped in-window $, recipients [to, $, in-window $, label, internal], internal $, truncated
            lst.append([parse_iso(f["freeze_ts"]), round(f["escaped"], 2), round(f["window"], 2),
                        [[x["to"], round(x["usd"], 2), round(x["w"], 2), _label_arr(x["label"]), int(x["internal"])]
                         for x in f["recipients"][:8]], round(f["internal"], 2), int(f["truncated"]), int(f["service"])])
        rec(key).setdefault("o", {})[chain] = lst
    from . import reasons as _r
    cur = extras.get("curated") or {}
    for key, r in list(idx.items()):
        chains = {e[0] for e in r.get("e", [])} | {p[0] for p in r.get("p", [])}
        for ch in chains:
            net = NETWORK_OF.get(ch)
            x = _r.reason_of(lab, cur, ch, key, (net, key_of(net, key)) if net else (None, key))
            if x:  # reason: category, title, source url, origin (curated | wallet-attribution), OFAC SDN flag
                r.setdefault("r", {})[ch] = [x["category"], x["title"], x["url"], x["origin"], int(bool(x.get("ofac")))]
    big = {c["id"] for c in extras.get("cluster_list", []) if c["size"] >= 3 or c["kind"] != "batch"}
    for (chain, addr), ids in extras.get("cluster_index", {}).items():
        ids = [i for i in ids if i in big][:4]
        if ids:
            key = canon_for(chain, addr)
            if key:
                rec(key).setdefault("c", [])
                rec(key)["c"] += [i for i in ids if i not in rec(key)["c"]]
    for s in (extras.get("signals") or {}).get("open", []):
        rec(s["address"]).setdefault("x", []).append([s["frozen_on"], s["stage"], parse_iso(s["frozen_at"]), s["at_risk"]])


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
