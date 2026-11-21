"""HTTP з ретраями, base58check, час."""
import hashlib
import logging
import os
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
    """Потокобезпечний ліміт частоти: кожен виклик резервує свій слот."""
    if interval <= 0:
        return
    with _lock:
        now = time.monotonic()
        slot = max(now, _last_call.get(key, 0) + interval)
        _last_call[key] = slot
    if slot > now:
        time.sleep(slot - now)


def http_json(method, url, *, interval=0.0, retries=6, timeout=40, validate=None, **kw):
    """JSON-запит з експоненційним backoff. validate(obj)->bool; False => ретрай."""
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
                last = f"невалідна відповідь: {str(obj)[:150]}"
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        delay = min(2 ** attempt, 30)
        log.debug("ретрай %s (%s) через %ss", host, last, delay)
        time.sleep(delay)
    raise HttpError(f"{url}: вичерпано ретраї ({last})")


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
        raise ValueError("невірна контрольна сума base58check")
    return payload


def tron_from_hex20(h: str) -> str:
    """20-байтний hex -> T-адреса (префікс 0x41)."""
    h = h.lower().removeprefix("0x")
    if len(h) == 42 and h.startswith("41"):
        h = h[2:]
    return b58check_encode(b"\x41" + bytes.fromhex(h))


def tron_to_hex20(addr: str) -> str:
    p = b58check_decode(addr)
    if len(p) != 21 or p[0] != 0x41:
        raise ValueError("не TRON-адреса")
    return p[1:].hex()


# ---------- час ----------
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
