"""Make a database statement that names no account LOUD in the log.

The log line ``rls_context_missing source=... target=... where=... suppressed=N``
means: a database statement ran with no account (tenant) named and no bypass.
Under row security such a statement sees nothing: a SELECT returns no rows and
an UPDATE changes none, and until now nothing said so.

What to do about it: name the account at the call site, with
``acquire_with_tenant(pool, tenant)`` for ``get_db`` or ``tenant_scope(tenant)``
for the old client (``app.core.postgres_adapter``). ``where`` is the first
``app/`` caller found (outside ``app/core/``); ``-`` is normal for the old
client, which runs statements in a worker thread, so ``target`` (the table or
function name) is the locator there.

At most one line in five minutes per source and target; ``suppressed`` counts
the occurrences in between. ``get_db`` has no target, so there the limit is
per caller (per ``where``).

This only logs. It never changes a statement or raises, and it must not import
``app.core.db`` or ``app.core.postgres_adapter`` (they import it).
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 300.0
_MAX_PAIRS = 256
_MAX_FRAMES = 15
_UNSAFE = re.compile(r"[^A-Za-z0-9_.]")

_lock = threading.Lock()
# (source, target) -> [last_logged_monotonic, suppressed_count]
_state: dict[tuple[str, str], list] = {}


def _reset_for_tests() -> None:
    with _lock:
        _state.clear()


def _clean_target(target: object) -> str:
    cleaned = _UNSAFE.sub("", str(target))[:64]
    return cleaned or "-"


def _find_where(depth: int) -> str:
    try:
        frame = sys._getframe(depth)
    except ValueError:
        return "-"
    for _ in range(_MAX_FRAMES):
        if frame is None:
            break
        path = frame.f_code.co_filename.replace("\\", "/")
        if "/app/" in path and "/app/core/" not in path:
            return f"{path.rsplit('/app/', 1)[1]}:{frame.f_lineno}"
        frame = frame.f_back
    return "-"


def note_missing_context(source: str, target: str = "-") -> None:
    """Log (rate limited) that a statement ran with no account and no bypass."""
    try:
        shown = _clean_target(target)
        # With no table to tell callers apart (get_db), the caller's place is
        # the locator: each caller then has its own window, so a second caller
        # in the same five minutes is named too and not only counted.
        where = _find_where(2) if shown == "-" else None
        key = (source, shown if where is None else where)
        now = time.monotonic()
        with _lock:
            if key not in _state and len(_state) >= _MAX_PAIRS:
                key = (source, "other")
                shown = "other"
            entry = _state.get(key)
            if entry is None:
                _state[key] = [now, 0]
                suppressed = 0
            elif now - entry[0] < _WINDOW_SECONDS:
                entry[1] += 1
                return
            else:
                suppressed = entry[1]
                entry[0] = now
                entry[1] = 0
        if where is None:
            where = _find_where(2)
        logger.warning(
            "rls_context_missing source=%s target=%s where=%s suppressed=%d",
            source, shown, where, suppressed,
        )
    except Exception:
        return
