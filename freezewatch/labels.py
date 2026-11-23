"""Address labels from the public dataset github.com/prettydeath/wallet-attribution (MIT; it aggregates DefiLlama CEX lists,
GraphSense TagPacks, Forta, etherscan-labels, OFAC SDN and more).

Only the networks we need are downloaded, at most once a day (If-None-Match / ETag), into a cache directory; the labels of
the addresses that appear in OUR data (frozen addresses, outflow recipients, inflow senders) are copied into the
`address_labels` table. The dashboard receives only those few labels, never the dataset. Labels are always shown as
"label from wallet-attribution: <label> (<source>)" with the upstream source_url - never as our own claim."""
import csv
import gzip
import io
import logging
from pathlib import Path

import requests

from .constants import DATA_DIR
from .util import b58check_decode, iso, now_ts

log = logging.getLogger("freezewatch.labels")
RAW = "https://raw.githubusercontent.com/prettydeath/wallet-attribution/main/data/"
CACHE = DATA_DIR / "cache" / "labels"
REFETCH_SEC = 24 * 3600
RETRY_SEC = 6 * 3600  # cache missing (e.g. fresh CI checkout) but unlabeled addresses are waiting
# our chain key -> wallet-attribution network file
NETWORK_OF = {"eth": "ethereum", "usdc_eth": "ethereum", "tron": "tron", "arb1": "arbitrum", "arb_usdt": "arbitrum",
              "avax": "avalanche", "pol": "polygon", "oeth": "optimism", "celo": "celo", "base": "base", "bsc": "bsc",
              "kaia": "kaia", "aptos": "aptos", "ton": "ton", "near": "near", "mantle": "mantle", "unichain": "unichain",
              "ink": "ink", "berachain": "berachain", "hyper": "hyperevm", "plasma": "plasma", "sei": "sei"}
# categories that connect unrelated people (exchanges, bridges, protocols): never used as cluster connectors
CONNECTOR_CATEGORIES = {"exchange", "bridge", "dex", "defi", "protocol", "payments", "payment", "custody", "market-maker",
                        "mixer", "service", "stablecoin", "token", "contract", "cex", "wallet"}
SEVERE = {"sanctioned", "hack", "exploit", "ransomware", "scam", "phishing", "terrorism", "darknet", "theft", "fraud", "mixer"}


def key_of(network, addr):
    """Normalised lookup key: lowercase for EVM-style networks, 20-byte hex for TRON base58, as-is otherwise."""
    a = (addr or "").strip()
    if not a:
        return None
    if network == "tron" and a.startswith("T") and len(a) == 34:
        try:
            return "0x" + b58check_decode(a)[1:].hex()
        except Exception:  # noqa: BLE001
            return None
    if a.lower().startswith("0x"):
        return a.lower()
    if network in ("aptos",):
        return a.lower()
    return a if network not in ("near",) else a.lower()


def parse_rows(text, network):
    """CSV text -> {key: [row dicts]} (only the columns we use)."""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        k = key_of(network, r.get("address"))
        if not k:
            continue
        out.setdefault(k, []).append({"entity": r.get("entity") or "", "label": r.get("label") or "",
                                      "category": (r.get("category") or "").lower(), "categories": r.get("categories") or "",
                                      "source": r.get("source") or "", "source_url": r.get("source_url") or "",
                                      "confidence": r.get("confidence") or ""})
    return out


def best(rows):
    """One label per address: prefer severe categories, then higher confidence."""
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(rows, key=lambda r: (0 if r["category"] in SEVERE else 1, rank.get(r["confidence"], 3)))[0]


def fetch(network, cache=CACHE, session=None):
    """Download data/<network>.csv (or .csv.gz) with ETag caching. -> path of the cached csv text file or None."""
    s = session or requests
    cache.mkdir(parents=True, exist_ok=True)
    for ext in (".csv", ".csv.gz"):
        f = cache / f"{network}{ext}"
        et = cache / f"{network}{ext}.etag"
        hdr = {"If-None-Match": et.read_text().strip()} if f.exists() and et.exists() else {}
        try:
            r = s.get(RAW + network + ext, headers=hdr, timeout=120)
        except Exception as e:  # noqa: BLE001
            log.warning("labels %s: %s", network, str(e)[:80])
            return f if f.exists() else None
        if r.status_code == 304:
            return f
        if r.status_code == 200:
            f.write_bytes(r.content)
            if r.headers.get("ETag"):
                et.write_text(r.headers["ETag"])
            return f
    return None


def read_text(path):
    b = Path(path).read_bytes()
    return (gzip.decompress(b) if str(path).endswith(".gz") else b).decode("utf-8", "replace")


def counterparties(conn):
    """{network: {key}} of every address we may want a label for."""
    out = {}

    def add(chain, addr):
        net = NETWORK_OF.get(chain)
        k = key_of(net, addr) if net else None
        if k:
            out.setdefault(net, set()).add(k)
    for r in conn.execute("SELECT DISTINCT chain, recipient FROM outflows"):
        add(r[0], r[1])
    for r in conn.execute("SELECT DISTINCT chain, sender FROM inflows_after_freeze"):
        add(r[0], r[1])
    for r in conn.execute("SELECT DISTINCT chain, target FROM proposals WHERE target IS NOT NULL"):
        add(r[0], r[1])
    for r in conn.execute("SELECT DISTINCT chain, frozen_address FROM inflows_after_freeze"):
        add(r[0], r[1])
    for r in conn.execute("SELECT DISTINCT source, subject FROM freezes"):
        add(r[0], r[1])
    return out


def match(conn, network, keys, rows_by_key):
    """Store labels of `keys` found in rows_by_key; mark every key as checked. -> number of labels stored."""
    n = 0
    for k in keys:
        # "frozen" tags just restate the Tether/Circle blacklist we track ourselves: not stored
        rows = [r for r in rows_by_key.get(k, []) if not (r["category"] == "frozen" and r["categories"] in ("frozen", ""))]
        if rows:
            b = best(rows)
            conn.execute("INSERT OR REPLACE INTO address_labels(network,key,entity,label,category,categories,source,source_url,"
                         "confidence) VALUES(?,?,?,?,?,?,?,?,?)",
                         (network, k, b["entity"], b["label"], b["category"], b["categories"], b["source"], b["source_url"],
                          b["confidence"]))
            n += 1
        conn.execute("INSERT OR REPLACE INTO label_checked(network,key,checked_at) VALUES(?,?,?)", (network, k, iso(now_ts())))
    return n


def refresh(conn, budget_sec=240, cache=CACHE):
    """Daily: fetch the needed networks and label the addresses that were not checked yet (or all, once a day)."""
    import time
    from .db import meta_get, meta_set
    t0 = time.time()
    cp = counterparties(conn)
    last = float(meta_get(conn, "labels_fetched_at", 0) or 0)
    due = now_ts() - last > REFETCH_SEC
    total = 0
    for net, keys in sorted(cp.items()):
        checked = {r[0] for r in conn.execute("SELECT key FROM label_checked WHERE network=?", (net,))}
        todo = keys if due else keys - checked
        if not todo:
            continue
        if not due and now_ts() - float(meta_get(conn, f"labels_try_{net}", 0) or 0) < RETRY_SEC and not (cache / f"{net}.csv").exists():
            continue
        if time.time() - t0 > budget_sec:
            break
        meta_set(conn, f"labels_try_{net}", now_ts())
        f = fetch(net, cache)
        if not f:
            log.info("labels: no data file for %s", net)
            continue
        total += match(conn, net, todo, parse_rows(read_text(f), net))
    if due:
        meta_set(conn, "labels_fetched_at", now_ts())
    conn.commit()
    if total:
        log.info("labels: %s labelled addresses stored", total)
    return total


def lookup(conn, chain, addr):
    """Label row (dict) for an address on one of our chains, or None."""
    net = NETWORK_OF.get(chain)
    k = key_of(net, addr) if net else None
    if not k:
        return None
    r = conn.execute("SELECT * FROM address_labels WHERE network=? AND key=?", (net, k)).fetchone()
    return dict(r) if r else None


def load_map(conn):
    """{(network, key): row} of every stored label (small: only addresses in our data)."""
    return {(r["network"], r["key"]): dict(r) for r in conn.execute("SELECT * FROM address_labels")}


def is_connector(row):
    return bool(row) and any(c in CONNECTOR_CATEGORIES for c in {row.get("category")} | set((row.get("categories") or "").split(";")))
