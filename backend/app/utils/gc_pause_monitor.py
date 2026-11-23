"""Evidence and mitigation for CPython full-collection pauses.

On real calls the process freezes for about 200 ms roughly once per 71 s of
connected-call time. The best-supported (NOT yet proven in production) cause is
the generation-2 garbage collection: the collector tracks several hundred
thousand objects and one full pass takes 100-150 ms.

* ``install()`` registers one ``gc.callbacks`` hook that RECORDS every full
  collection and remembers the most recent one, so the audio-gap log line can
  say ``source=gc_pause``. The hook never logs: it runs inside the collector,
  at whatever allocation set the collection off, in whatever thread, possibly
  in the middle of another log call. ``log_pending()`` writes the lines; the
  event loop's 10 ms heartbeat calls it. A full collection of 20 ms or more
  (and a younger one of 50 ms or more) gets its own ``gc_pause`` line within
  about 10 ms (``logged_after_ms`` says exactly how long after). Shorter full
  collections are only counted and reported once a minute in one
  ``gc_pause_summary`` line: with the start-up heap frozen they are frequent
  and short, and one line each would flood the log.
* ``freeze_startup_heap(app)`` first builds the web framework's per-route
  objects, which it otherwise builds on the first request (about 110,000
  objects here), then moves everything alive at the end of start-up into the
  permanent generation (``gc.freeze``) so later full collections do not walk
  it. Switch: ``GC_FREEZE_STARTUP_HEAP`` (default on).

Clock: pause end times use ``time.monotonic()``, the clock behind
``asyncio``'s ``loop.time()`` which the gap detector uses for ``gap_ms``.
Durations use ``time.perf_counter()``.

Nothing here walks the heap or changes collector thresholds.
"""
from __future__ import annotations

import gc
import logging
import os
import time
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

# A full collection gets its own line at or above this; a younger one at or
# above the second value. Shorter full collections are counted for the summary.
_FULL_LOG_THRESHOLD_MS = 20.0
_YOUNG_LOG_THRESHOLD_MS = 50.0
_SUMMARY_EVERY_S = 60.0

# Pauses recorded by the hook and not yet logged: (generation, ms, collected,
# uncollectable, monotonic end). Bounded: when nothing drains it (a process
# without the heartbeat) the oldest entries are kept and newer ones dropped.
_MAX_PENDING = 64

_t0: float = 0.0
_pending: list = []
_last_full: Optional[Tuple[float, float]] = None  # (monotonic end, ms)
# Full collections under the line threshold since the last summary:
# [count, total ms, longest ms].
_quiet: list = [0, 0.0, 0.0]
_summary_at: float = 0.0

_startup_freeze_done: bool = False


def _hook(phase: str, info: dict) -> None:
    """Runs inside every collection: two clock reads on the common path, and
    never a log call (see the module docstring)."""
    global _t0, _last_full
    try:
        if phase == "start":
            _t0 = time.perf_counter()
            return
        started, _t0 = _t0, 0.0
        if started == 0.0:
            return  # a "stop" whose "start" this hook never saw (installed mid-collection)
        ms = (time.perf_counter() - started) * 1000.0
        generation = info.get("generation")
        full = generation == 2
        if full:
            end = time.monotonic()
            _last_full = (end, ms)
            if ms < _FULL_LOG_THRESHOLD_MS:
                _quiet[0] += 1
                _quiet[1] += ms
                if ms > _quiet[2]:
                    _quiet[2] = ms
                return
        elif ms < _YOUNG_LOG_THRESHOLD_MS:
            return
        else:
            end = time.monotonic()
        if len(_pending) < _MAX_PENDING:
            _pending.append((generation, ms, info.get("collected"), info.get("uncollectable"), end))
    except Exception:  # never raise into the collector
        pass


def log_pending() -> int:
    """Write the lines for what the hook recorded. Returns how many lines.

    Called from the event loop (the 10 ms heartbeat), never from the hook.
    """
    global _summary_at
    written = 0
    while _pending:
        generation, ms, collected, uncollectable, end = _pending.pop(0)
        logger.info(
            "gc_pause generation=%s ms=%.1f collected=%s uncollectable=%s frozen=%s logged_after_ms=%.0f",
            generation, ms, collected, uncollectable, gc.get_freeze_count(),
            max(0.0, (time.monotonic() - end) * 1000.0),
        )
        written += 1
    if _quiet[0]:
        now = time.monotonic()
        if _summary_at == 0.0:
            _summary_at = now
        elif now - _summary_at >= _SUMMARY_EVERY_S:
            count, total, longest = _quiet[0], _quiet[1], _quiet[2]
            _quiet[0], _quiet[1], _quiet[2] = 0, 0.0, 0.0
            window = now - _summary_at
            _summary_at = 0.0
            logger.info(
                "gc_pause_summary full_under_%dms=%d total_ms=%.1f max_ms=%.1f window_s=%.0f frozen=%s",
                int(_FULL_LOG_THRESHOLD_MS), count, total, longest, window, gc.get_freeze_count(),
            )
            written += 1
    return written


def install() -> bool:
    """Register the hook once. Returns True when this call registered it."""
    if _hook in gc.callbacks:
        return False
    gc.callbacks.append(_hook)
    return True


def uninstall() -> None:
    """Remove the hook and forget remembered state (tests)."""
    global _last_full, _t0, _summary_at
    while _hook in gc.callbacks:
        gc.callbacks.remove(_hook)
    _last_full = None
    _t0 = 0.0
    _summary_at = 0.0
    del _pending[:]
    _quiet[0], _quiet[1], _quiet[2] = 0, 0.0, 0.0


def last_full_pause() -> Optional[Tuple[float, float]]:
    """(monotonic end time, ms) of the most recent full collection, or None."""
    return _last_full


def full_pause_between(t0: float, t1: float) -> Optional[float]:
    """ms of a full collection that ENDED within [t0, t1], else None."""
    last = _last_full
    if last is None:
        return None
    end, ms = last
    return ms if t0 <= end <= t1 else None


def _switch_enabled() -> bool:
    raw = os.environ.get("GC_FREEZE_STARTUP_HEAP", "1")
    return raw.strip().lower() not in ("0", "false", "no", "off")


def prebuild_route_objects(app: Any) -> int:
    """Make the web framework build its per-route objects now.

    FastAPI builds each route's dependency and validation objects the first time
    the route is matched against a request (about 110,000 objects for this
    application, all at the first request after a restart). Asking every route
    whether it matches a path that no route has builds them here, before the
    start-up heap is frozen; no handler runs. Fail-soft per route. Returns the
    number of routes asked.
    """
    asked = 0
    try:
        routes = list(app.router.routes)
    except Exception:
        return 0
    scope = {
        "type": "http", "method": "GET", "path": "/__route_warmup__", "root_path": "",
        "headers": [], "query_string": b"", "app": app,
    }
    for route in routes:
        try:
            route.matches(dict(scope))
            asked += 1
        except Exception:  # one odd route must not stop start-up or the others
            continue
    return asked


def freeze_startup_heap(app: Any = None) -> bool:
    """Once per process: build the route objects, collect, then freeze the
    start-up heap.

    Returns True when this call froze the heap. Logs one wiring line
    (``gc_startup_heap_freeze enabled=...``) the first time it is called.
    """
    global _startup_freeze_done
    if _startup_freeze_done:
        return False
    _startup_freeze_done = True
    enabled = _switch_enabled()
    logger.info("gc_startup_heap_freeze enabled=%s", "true" if enabled else "false")
    if not enabled:
        return False
    started = time.perf_counter()
    routes = prebuild_route_objects(app) if app is not None else 0
    prebuilt = time.perf_counter()
    gc.collect()
    gc.freeze()
    logger.info(
        "gc_startup_heap_frozen objects=%s routes_prebuilt=%s prebuild_ms=%.1f ms=%.1f",
        gc.get_freeze_count(), routes, (prebuilt - started) * 1000.0,
        (time.perf_counter() - prebuilt) * 1000.0,
    )
    return True


def reset_startup_freeze_flag() -> None:
    """Test hook."""
    global _startup_freeze_done
    _startup_freeze_done = False
