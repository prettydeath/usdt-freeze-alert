"""Data-health check: compare the on-chain transactionCount with the highest id in the DB for the multisig chains.
A gap means ingestion silently stopped (the dashboard footer then says "data stale" instead of showing old data)."""
import json
import logging

from . import eth, tron
from .db import meta_get, meta_set

log = logging.getLogger("freezewatch.health")
MAX_GAP = 3


def check(conn, readers=None):
    """-> {chain: {onchain_count, max_id, gap, stale} | {error}}; stored in meta['health'] only when it changes."""
    readers = readers or {"eth": eth.read_count, "tron": tron.read_count}
    out = {}
    for chain, read in readers.items():
        try:
            count = int(read())
        except Exception as e:  # noqa: BLE001
            out[chain] = {"error": "count unreadable"}
            log.warning("health %s: on-chain count unreadable (%s)", chain, str(e)[:60])
            continue
        mx = conn.execute("SELECT MAX(tx_id) FROM proposals WHERE chain=? AND destination IS NOT NULL", (chain,)).fetchone()[0]
        gap = (count - 1) - (mx if mx is not None else -1)
        out[chain] = {"onchain_count": count, "max_id": mx, "gap": gap, "stale": gap > MAX_GAP}
        if gap > MAX_GAP:
            log.error("HEALTH %s data is stale: on-chain count %s but the highest id in the DB is %s (gap %s)", chain.upper(),
                      count, mx, gap)
    new = json.dumps(out, sort_keys=True)
    if meta_get(conn, "health") != new:
        meta_set(conn, "health", new)
        conn.commit()
    return out


def load(conn):
    try:
        return json.loads(meta_get(conn, "health") or "null")
    except ValueError:
        return None
