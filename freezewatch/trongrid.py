"""TronGrid access in one place: key rotation, per-key rate limiting and 429 cooldown, bounded attempts, usage counters, and a
Tronscan fallback for TRC20 transfer lists when TronGrid is saturated.

- Keys (TRONGRID_API_KEY, comma separated) are used round-robin; each key has its own throttle (<= ~14 req/s, the free key limit
  is 15) and a short cooldown after HTTP 429 / a rate-limit reply, so load spreads over all keys. When keys exist a request is
  never sent keyless.
- One call is bounded by `max_sec` wall time and stops once the run deadline / optional step budget is over.
- Per-key counters (requests, 429s) are logged at the end of a run.
- Transfers: `transfers()` asks TronGrid first and falls back to the Tronscan token-transfer API (keyless) when TronGrid fails;
  rows are normalised to the TronGrid shape, Transfer events only, deduplicated by (tx, from, to, value)."""
import logging
import threading
import time

from . import deadline
from .constants import TRON_USDT
from .util import HttpError, _session, key_pool, throttle

log = logging.getLogger("freezewatch.trongrid")
BASE = "https://api.trongrid.io"
TRONSCAN_TRANSFERS = "https://apilist.tronscanapi.com/api/token_trc20/transfers"
PER_KEY_INTERVAL = 0.07      # seconds between requests of ONE key (~14 req/s)
KEYLESS_INTERVAL = 0.5
COOLDOWN_429 = 6.0
_usage = {}
_lock = threading.Lock()


class Stopped(HttpError):
    """The run deadline / step budget ended the call: never answered by the fallback source."""


def _count(label, bad=False):
    with _lock:
        u = _usage.setdefault(label, [0, 0])
        u[1 if bad else 0] += 1


def usage_report():
    """'TronGrid keys: key#1 812 req (3 x 429), key#2 ...' (empty string when nothing was called)."""
    with _lock:
        if not _usage:
            return ""
        return "TronGrid usage: " + ", ".join(f"{k} {v[0]} req" + (f" ({v[1]} x 429)" if v[1] else "") for k, v in sorted(_usage.items()))


def reset_usage():
    with _lock:
        _usage.clear()


def request(method, path, *, params=None, json=None, max_sec=45, session=None, sleep=time.sleep):
    """One TronGrid call with rotation, cooldown and a wall-time bound. -> decoded JSON, raises HttpError."""
    session = session or _session
    pool = key_pool("TRONGRID_API_KEY")
    t0 = time.monotonic()
    last = None
    attempts = 6 + 3 * max(1, len(pool))
    for attempt in range(attempts):
        if time.monotonic() - t0 > max_sec or deadline.http_stop():
            break
        key = pool.next()
        label = pool.label(key) if key else "no-key"
        throttle("trongrid:" + label, PER_KEY_INTERVAL if key else KEYLESS_INTERVAL)
        _count(label)
        try:
            r = session.request(method, BASE + path, params=params, json=json, headers={"TRON-PRO-API-KEY": key} if key else {},
                                timeout=(5, 20))
            if r.status_code == 429 or r.status_code == 403:
                _count(label, bad=True)
                if key:
                    pool.mark_bad(key, COOLDOWN_429)
                last = f"HTTP {r.status_code}"
            elif r.status_code >= 500:
                last = f"HTTP {r.status_code}"
            elif r.status_code >= 400:
                raise HttpError(f"HTTP {r.status_code} {path}: {r.text[:120]}")
            else:
                o = r.json()
                if o.get("success") is False and "rate" in str(o).lower():
                    _count(label, bad=True)
                    if key:
                        pool.mark_bad(key, COOLDOWN_429)
                    last = str(o)[:80]
                else:
                    return o
        except HttpError:
            raise
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:80]
        # all keys cooling: wait for the first one to recover instead of hammering
        if key and pool.cooling() >= len(pool):
            sleep(min(COOLDOWN_429, max(0.2, 1 + attempt * 0.5)))
        elif not key:
            sleep(min(1 + attempt, 6))
    raise HttpError(f"TronGrid {path.split('?')[0]}: retries exhausted ({last})")


# ---------------- TRC20 transfers: TronGrid first, Tronscan fallback ----------------
def normalise_tronscan(row):
    """Tronscan token_transfers row -> TronGrid trc20 row shape (Transfer only)."""
    if (row.get("event_type") or "Transfer") != "Transfer":
        return None
    try:
        return {"type": "Transfer", "value": str(int(row["quant"])), "from": row["from_address"], "to": row["to_address"],
                "block_timestamp": int(row["block_ts"]), "transaction_id": row["transaction_id"]}
    except (KeyError, ValueError, TypeError):
        return None


def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        k = (r.get("transaction_id"), r.get("from"), r.get("to"), r.get("value"))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def tronscan_transfers(addr, direction, min_ms, max_ms=None, *, get=None, max_pages=20, stop=None, limit=50):
    """TRC20 USDT transfers to / from `addr` (direction 'in' | 'out') from the Tronscan API, newest first, in TronGrid shape."""
    get = get or _tronscan_get
    out, start = [], 0
    for _ in range(max_pages):
        if stop and stop():
            break
        params = {"limit": limit, "contract_address": TRON_USDT, "relatedAddress": addr, "start_timestamp": int(min_ms)}
        if max_ms:
            params["end_timestamp"] = int(max_ms)
        if start:
            params["start"] = start
        o = get(params)
        rows = o.get("token_transfers") or []
        for x in rows:
            n = normalise_tronscan(x)
            if n and ((direction == "in" and n["to"] == addr) or (direction == "out" and n["from"] == addr)):
                out.append(n)
        if len(rows) < limit:
            break
        start += limit
    return dedupe(out)


def _tronscan_get(params):
    from .util import http_json
    return http_json("GET", TRONSCAN_TRANSFERS, params=params, interval=0.4, retries=3, max_total=30)


def transfers(addr, direction, min_ms, max_ms=None, *, grid_get=None, scan_get=None, stop=None):
    """-> (rows, source). TronGrid's trc20 list first (`only_to` / `only_from`, fingerprint paging); on any failure the Tronscan
    list. A source that returns rows only partially is not mixed with the other one."""
    grid_get = grid_get or (lambda params: request("GET", f"/v1/accounts/{addr}/transactions/trc20", params=params))
    rows, fp = [], None
    try:
        for _ in range(25):
            if stop and stop():
                raise Stopped("stopped")
            params = {"only_to" if direction == "in" else "only_from": "true", "contract_address": TRON_USDT,
                      "min_timestamp": int(min_ms), "limit": 200, "order_by": "block_timestamp,asc"}
            if max_ms:
                params["max_timestamp"] = int(max_ms)
            if fp:
                params["fingerprint"] = fp
            o = grid_get(params)
            rows += o.get("data") or []
            fp = (o.get("meta") or {}).get("fingerprint")
            if not fp:
                break
        return dedupe([r for r in rows if r.get("type", "Transfer") == "Transfer"]), "trongrid"
    except Stopped:
        raise
    except HttpError as e:
        log.info("TronGrid transfers %s unavailable (%s): Tronscan fallback", addr[:10], str(e)[:50])
    return tronscan_transfers(addr, direction, min_ms, max_ms, get=scan_get, stop=stop), "tronscan"
