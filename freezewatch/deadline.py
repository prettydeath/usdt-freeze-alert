"""One monotonic deadline for a whole `run`, so a slow or hanging source can never hold the CI queue.

Order of a run: core freeze / proposal sync of every chain (must always finish; bounded by per-call HTTP timeouts), then the
optional steps (history back-fills, balances, inflows, outflows, signals, labels). Every optional step gets
min(its own budget, time left - RESERVE) and is stopped *inside* its loops: while a step is active, `http_json` (and the other
HTTP helpers) refuse to start another request or retry once the step budget or the global deadline is over, so pagination /
bisection / retry loops end at the next call. RESERVE seconds are kept for rendering the site and publishing.

Outside `run` (backfill, tests) nothing is started and everything is unlimited."""
import logging
import os
import sys
import threading
import time
import traceback

log = logging.getLogger("freezewatch.deadline")
DEFAULT_TOTAL = 420.0
RESERVE = 60.0          # kept for render + publish
GRACE = 25.0            # an optional step still running this long after its budget is abandoned (hung socket)
_S = {"start": None, "total": DEFAULT_TOTAL, "step_end": None, "optional": False, "timings": []}
_lock = threading.Lock()


def start(total=None):
    """Begin a run. FREEZEWATCH_RUN_DEADLINE_SEC (default 420) is the wall time for the update part of the run."""
    with _lock:
        _S.update(start=time.monotonic(), total=float(total if total is not None else os.environ.get("FREEZEWATCH_RUN_DEADLINE_SEC", DEFAULT_TOTAL)),
                  step_end=None, optional=False, timings=[])


def stop():
    with _lock:
        _S.update(start=None, step_end=None, optional=False)


def started():
    return _S["start"] is not None


def elapsed():
    return time.monotonic() - _S["start"] if started() else 0.0


def optional_remaining():
    """Seconds optional work may still use (global deadline minus the render / publish reserve)."""
    if not started():
        return float("inf")
    return max(0.0, _S["total"] - RESERVE - elapsed())


def expired():
    return started() and optional_remaining() <= 0


def cap(own):
    """min(own budget, what is left for optional work)."""
    return min(own, optional_remaining()) if started() else own


def http_stop():
    """True when an HTTP call should not start (optional step over its budget, or global deadline reached)."""
    if not started() or not _S["optional"]:
        return False
    if optional_remaining() <= 0:
        return True
    end = _S["step_end"]
    return end is not None and time.monotonic() > end


def http_time_left(default):
    """Seconds an HTTP retry sleep may use right now."""
    if not started() or not _S["optional"]:
        return default
    end = _S["step_end"]
    left = optional_remaining() if end is None else min(optional_remaining(), end - time.monotonic())
    return max(0.0, min(default, left))


class Step:
    """Context manager / helper for one timed step. `budget` is the capped time (None for core steps)."""

    def __init__(self, name, own=None, optional=True):
        self.name, self.optional = name, optional
        if optional and started():
            self.budget = cap(own) if own is not None else optional_remaining()
        else:
            self.budget = own
        self.skipped = bool(optional and started() and self.budget is not None and self.budget <= 0)
        self.hit = False
        self.result = None      # what the step reported (e.g. addresses processed): explains a 0 s step ("nothing due")

    def __enter__(self):
        self.t0 = time.monotonic()
        with _lock:
            _S["optional"] = self.optional
            _S["step_end"] = (self.t0 + self.budget) if (self.optional and self.budget is not None and started()) else None
        return self

    def __exit__(self, *exc):
        dt = time.monotonic() - self.t0
        self.hit = bool(self.optional and self.budget is not None and dt >= self.budget - 0.5)
        with _lock:
            _S["optional"] = False
            _S["step_end"] = None
            row = [self.name, round(dt, 1), "skipped" if self.skipped else ("deadline" if self.hit else "")]
            _S["timings"].append(row + ([self.result] if self.result is not None else []))
        return False


def step(name, own=None, optional=True):
    return Step(name, own, optional)


def run_guarded(fn, name, budget):
    """Run an optional step in a daemon thread: if it is still running `budget + GRACE` seconds in (e.g. a hung socket) it is
    abandoned and the run goes on. -> True if it finished (an exception is logged, not raised)."""
    box = {}

    def target():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            box["err"] = e
    th = threading.Thread(target=target, name=f"step-{name}", daemon=True)
    th.start()
    th.join(max(1.0, (budget or 0) + GRACE))
    if th.is_alive():
        frame = sys._current_frames().get(th.ident)
        where = "".join(traceback.format_stack(frame)[-4:]) if frame else ""
        log.error("step %s hung (> %.0fs): abandoned. Stack tail:\n%s", name, (budget or 0) + GRACE, where)
        return False
    if "err" in box:
        log.warning("step %s failed: %s", name, str(box["err"])[:120])
    return True


def note(name, secs, flag=""):
    with _lock:
        _S["timings"].append([name, round(secs, 1), flag])


def timings():
    return [list(t) for t in _S["timings"]]


def summary():
    """'timings: tron 41s, eth 12s, outflows 60s (deadline), ...' for the log."""
    parts = [f"{t[0]} {t[1]:.0f}s" + (f" ({t[2]})" if t[2] else "") + (f" [{t[3]}]" if len(t) > 3 else "") for t in _S["timings"]]
    return f"timings (run {elapsed():.0f}s of {_S['total']:.0f}s): " + ", ".join(parts)
