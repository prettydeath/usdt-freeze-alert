"""Clusters of frozen addresses.

batch        freezes executed on the same chain within BATCH_GAP_SEC of each other (the same multisig signing a list in one
             sitting, e.g. TRON ids 11968-11991) or in the same transaction
crosschain   the same address frozen on two or more chains
counterparty frozen addresses that share senders (inflows) or recipients (outflows); union-find over shared counterparties.
             Labeled exchanges / bridges / protocols, sinks and hubs linking more than MAX_HUB addresses are NOT connectors:
             they would merge unrelated people into giant false clusters."""
import hashlib
import json
import logging

from . import analytics, crosschain
from .constants import FREEZE_FUNCS
from .labels import NETWORK_OF, is_connector, key_of
from .util import iso, parse_iso

log = logging.getLogger("freezewatch.clusters")
BATCH_GAP_SEC = 600
MAX_HUB = 6
MEMBER_CAP = 400


def cid(prefix, *parts):
    return prefix + "-" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8]


def batch_clusters(rows):
    """rows: [(chain, address, ts, tx)] executed freezes -> clusters (>= 2 distinct addresses)."""
    out = []
    by = {}
    for chain, addr, ts, tx in rows:
        by.setdefault(chain, []).append((ts, addr, tx))
    for chain, items in by.items():
        items.sort()
        cur = []
        for it in items + [None]:
            if it is not None and (not cur or it[0] - cur[-1][0] <= BATCH_GAP_SEC):
                cur.append(it)
                continue
            addrs = list(dict.fromkeys(a for _t, a, _x in cur))
            if len(addrs) >= 2:
                out.append({"kind": "batch", "chains": [chain], "members": [(chain, a) for a in addrs],
                            "first": cur[0][0], "last": cur[-1][0], "id": cid("b", chain, cur[0][0])})
            cur = [it] if it is not None else []
    return out


def crosschain_clusters(rows):
    """rows: [(chain, address_key, ts)] -> one cluster per address frozen on >= 2 chains."""
    by = {}
    for chain, a, ts in rows:
        by.setdefault(a, []).append((ts, chain))
    out = []
    for a, items in by.items():
        chains = sorted({c for _t, c in items})
        if len(chains) >= 2:
            out.append({"kind": "crosschain", "chains": chains, "members": [(c, a) for c in chains],
                        "first": min(t for t, _c in items), "last": max(t for t, _c in items), "id": cid("x", a)})
    return out


def counterparty_clusters(edges, connector=lambda chain, cp: False, max_hub=MAX_HUB):
    """edges: [(chain, frozen_address, counterparty)] -> union-find clusters (>= 2 frozen addresses)."""
    links = {}
    for chain, fa, cp in edges:
        if fa == cp or connector(chain, cp):
            continue
        links.setdefault((chain, cp), set()).add((chain, fa))
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for members in links.values():
        if len(members) < 2 or len(members) > max_hub:
            continue
        ms = sorted(members)
        for m in ms[1:]:
            parent[find(m)] = find(ms[0])
    groups = {}
    for m in list(parent):
        groups.setdefault(find(m), []).append(m)
    out = []
    for g in groups.values():
        if len(g) >= 2:
            g = sorted(g)
            out.append({"kind": "counterparty", "chains": sorted({c for c, _a in g}), "members": g, "first": None, "last": None,
                        "id": cid("c", g[0][0], g[0][1])})
    return out


def _first_ts(conn, member):
    chain, addr = member
    r = conn.execute("SELECT MIN(executed_at) FROM proposals WHERE chain=? AND executed=1 AND target=?", (chain, addr)).fetchone()
    return parse_iso(r[0]) if r and r[0] else None


def build(conn, labels=None):
    """All clusters with totals; also returns {(chain, address_key): [cluster ids]} for the address index."""
    labels = labels if labels is not None else {}
    usd = analytics.frozen_usd_map(conn)
    usd_by = {}
    for (c, a, _f), v in usd.items():
        usd_by[(c, a)] = max(usd_by.get((c, a), 0) or 0, v or 0)
    rows, xrows = [], []
    qm = ",".join("?" * len(FREEZE_FUNCS))
    for r in conn.execute(f"SELECT chain, target, executed_at, exec_tx FROM proposals WHERE executed=1 AND executed_at IS NOT NULL "
                          f"AND target IS NOT NULL AND function IN ({qm})", FREEZE_FUNCS):
        a = analytics.norm(r["chain"], r["target"])
        if a in analytics.special_sinks(r["chain"]):
            continue
        ts = parse_iso(r["executed_at"])
        rows.append((r["chain"], a, ts, r["exec_tx"]))
        k = crosschain.norm_addr(r["chain"], r["target"])
        if k:
            xrows.append(("arb1" if r["chain"] == "arb_usdt" else r["chain"], k, ts))

    for r in conn.execute("SELECT source, subject, ts, tx_hash FROM freezes WHERE action='freeze'"):
        a = analytics.norm(r["source"], r["subject"])
        if a and a not in analytics.special_sinks(r["source"]):
            ts = parse_iso(r["ts"])
            rows.append((r["source"], a, ts, r["tx_hash"]))
            k = crosschain.norm_addr(r["source"], r["subject"])
            if k:
                xrows.append((r["source"], k, ts))

    def connector(chain, cp):
        net = NETWORK_OF.get(chain)
        if not net:
            return False
        if cp in analytics.special_sinks(chain):
            return True
        return is_connector(labels.get((net, key_of(net, cp))))
    edges = []
    for r in conn.execute("SELECT chain, frozen_address, sender FROM inflows_after_freeze"):
        edges.append((r["chain"], r["frozen_address"], r["sender"]))
    for r in conn.execute("SELECT DISTINCT chain, frozen_address, recipient FROM outflows"):
        edges.append((r["chain"], r["frozen_address"], r["recipient"]))
    cl = batch_clusters(rows) + crosschain_clusters(xrows) + counterparty_clusters(edges, connector)
    index = {}
    for c in cl:
        mem = c["members"]
        c["size"] = len(mem)
        c["total_usd"] = round(sum(usd_by.get(m, 0) or 0 for m in mem), 2)
        if c["first"] is None:
            ts = [t for t in (_first_ts(conn, m) for m in mem) if t]
            c["first"], c["last"] = (min(ts), max(ts)) if ts else (None, None)
        for m in mem:
            index.setdefault(m, []).append(c["id"])
    cl.sort(key=lambda c: (-c["total_usd"], -c["size"]))
    return cl, index


def save(conn, clusters):
    """Persist (rewrite only when something changed, keeps the DB file stable)."""
    rows = [(c["id"], c["kind"], c["size"], json.dumps(c["chains"]), c["total_usd"], iso(c["first"]) if c["first"] else None,
             iso(c["last"]) if c["last"] else None, json.dumps([list(m) for m in c["members"][:MEMBER_CAP]])) for c in clusters]
    old = {r[0]: tuple(r) for r in conn.execute("SELECT * FROM clusters")}
    if {r[0]: r for r in rows} == old:
        return False
    conn.execute("DELETE FROM clusters")
    conn.executemany("INSERT INTO clusters VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return True


def _member(m, labels):
    """[chain, address, label?] - a labelled member (e.g. an exchange hot wallet that was itself frozen) keeps its label."""
    net = NETWORK_OF.get(m[0])
    x = labels.get((net, key_of(net, m[1]))) if net and labels else None
    return [m[0], m[1]] + ([[x["entity"], x["label"], x["category"], x["source"], x["source_url"], x["confidence"]]] if x else [])


def payload(clusters, limit=40, member_sample=25, labels=None):
    """Dashboard JSON: biggest clusters first (batches of 2 are noise on a top list). Labelled members are listed first, so a
    labelled frozen wallet is visible as a member even though labelled addresses never act as connectors."""
    keep = [c for c in clusters if c["size"] >= 3 or c["kind"] != "batch"]
    def has_label(c):
        return bool(labels) and any(labels.get((NETWORK_OF.get(m[0]), key_of(NETWORK_OF.get(m[0]), m[1]))) for m in c["members"])
    # the top list, plus clusters that contain a labelled (e.g. exchange) wallet even if they rank lower
    extra = [c for c in keep[limit:] if has_label(c)][:80]
    out = []
    for c in keep[:limit] + extra:
        mem = sorted(c["members"], key=lambda m: 0 if (labels and labels.get((NETWORK_OF.get(m[0]), key_of(NETWORK_OF.get(m[0]), m[1])))) else 1)
        out.append({"id": c["id"], "kind": c["kind"], "size": c["size"], "chains": c["chains"], "usd": c["total_usd"],
                    "first": c["first"], "last": c["last"], "members": [_member(m, labels) for m in mem[:member_sample]]})
    kinds = {}
    for c in clusters:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    return {"top": out, "counts": kinds}
