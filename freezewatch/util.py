"""HTTP with retries, base58check, time."""
import hashlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("freezewatch")

_last_call: dict[str, float] = {}
_session = requests.Session()
_session.headers["User-Agent"] = "usdt-freeze-watch/0.1"


class HttpError(RuntimeError):
    pass


_lock = threading.Lock()


def throttle(key: str, interval: float) -> None:
    """Thread-safe rate limit: each call reserves its own slot."""
    if interval <= 0:
        return
    with _lock:
        now = time.monotonic()
        slot = max(now, _last_call.get(key, 0) + interval)
        _last_call[key] = slot
    if slot > now:
        time.sleep(slot - now)


def http_json(method, url, *, interval=0.0, retries=6, timeout=40, validate=None, **kw):
    """JSON request with exponential backoff. validate(obj)->bool; False => retry."""
    host = url.split("/")[2]
    last = None
    for attempt in range(retries):
        throttle(host, interval)
        try:
            r = _session.request(method, url, timeout=timeout, **kw)
            if r.status_code in (429, 500, 502, 503, 504, 522, 524):
                last = f"HTTP {r.status_code}"
            elif r.status_code >= 400:
                raise HttpError(f"HTTP {r.status_code} {url}: {r.text[:200]}")
            else:
                obj = r.json()
                if validate is None or validate(obj):
                    return obj
                last = f"invalid response: {str(obj)[:150]}"
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        delay = min(2 ** attempt, 30)
        log.debug("retry %s (%s) in %ss", host, last, delay)
        time.sleep(delay)
    raise HttpError(f"{url}: retries exhausted ({last})")


# ---------- base58check ----------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(b) - len(b.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + body


def b58check_encode(payload: bytes) -> str:
    chk = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return b58encode(payload + chk)


def b58check_decode(s: str) -> bytes:
    raw = b58decode(s)
    payload, chk = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != chk:
        raise ValueError("invalid base58check checksum")
    return payload


def tron_from_hex20(h: str) -> str:
    """20-byte hex -> T-address (0x41 prefix)."""
    h = h.lower().removeprefix("0x")
    if len(h) == 42 and h.startswith("41"):
        h = h[2:]
    return b58check_encode(b"\x41" + bytes.fromhex(h))


def tron_to_hex20(addr: str) -> str:
    p = b58check_decode(addr)
    if len(p) != 21 or p[0] != 0x41:
        raise ValueError("not a TRON address")
    return p[1:].hex()


# ---------- time ----------
def iso(ts) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> int | None:
    if not s:
        return None
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def now_ts() -> int:
    return int(time.time())


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v else default


# ---------- API key rotation ----------
class KeyPool:
    """Round-robin over comma-separated API keys with per-key cooldown. Key values are never logged: use label()."""

    def __init__(self, raw, cooldown=60.0, clock=time.monotonic):
        self.keys = [k.strip() for k in re.split(r"[,;\r\n]+", raw or "") if k.strip()]
        self.cooldown = cooldown
        self.clock = clock
        self._i = 0
        self._until = {}
        self._lock = threading.Lock()

    def __len__(self):
        return len(self.keys)

    def next(self):
        """Next usable key (round-robin, skipping cooling ones). If all are cooling, the one that recovers first.
        Returns None when no keys are configured."""
        if not self.keys:
            return None
        with self._lock:
            now = self.clock()
            n = len(self.keys)
            for step in range(n):
                idx = (self._i + step) % n
                if self._until.get(idx, 0) <= now:
                    self._i = (idx + 1) % n
                    return self.keys[idx]
            idx = min(range(n), key=lambda j: self._until.get(j, 0))
            self._i = (idx + 1) % n
            return self.keys[idx]

    def mark_bad(self, key, seconds=None):
        """Put a key on cooldown (rate limit / invalid key)."""
        if key in self.keys:
            with self._lock:
                self._until[self.keys.index(key)] = self.clock() + (seconds or self.cooldown)

    def label(self, key):
        return f"key#{self.keys.index(key) + 1}" if key in self.keys else "no-key"

    def cooling(self):
        now = self.clock()
        return sum(1 for i in range(len(self.keys)) if self._until.get(i, 0) > now)


_pools = {}


def key_pool(name):
    """Pool for env var `name` (comma-separated list allowed); rebuilt if the variable changes."""
    raw = os.environ.get(name, "")
    p = _pools.get(name)
    if p is None or p[0] != raw:
        p = (raw, KeyPool(raw))
        _pools[name] = p
    return p[1]


RATE_LIMIT_MARKERS = ("rate limit", "max calls", "invalid api key", "api key", "too many", "429", "suspended")


def looks_rate_limited(obj) -> bool:
    s = str(obj).lower()
    return any(m in s for m in RATE_LIMIT_MARKERS)


# expected key shapes: (provider label, env var, length, pattern)
KEY_SHAPES = [("etherscan", "ETHERSCAN_API_KEY", 34, r"[A-Za-z0-9]+"), ("trongrid", "TRONGRID_API_KEY", 36, r"[0-9a-fA-F-]+")]


def key_report():
    """Startup summary: counts per provider and warnings about unusual key shapes (index and length only, never values)."""
    parts, warns = [], []
    for name, var, ln, pat in KEY_SHAPES + [("tonapi", "TONAPI_KEY", 0, ""), ("safe", "SAFE_API_KEY", 0, "")]:
        pool = key_pool(var)
        if not len(pool):
            continue
        parts.append(f"{name}: {len(pool)} key{'s' if len(pool) != 1 else ''}")
        if ln:
            for i, k in enumerate(pool.keys, 1):
                if len(k) != ln or not re.fullmatch(pat, k):
                    warns.append(f"{name} key#{i} has unusual shape (length {len(k)}, expected {ln})")
    return ", ".join(parts), warns
