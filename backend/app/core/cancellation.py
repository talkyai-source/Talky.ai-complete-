"""Cancellation-safe ownership handoffs for critical cleanup work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar


T = TypeVar("T")

# A strong reference is load-bearing: the event loop only keeps weak
# references to tasks.  A cleanup/re-defer handoff must not disappear merely
# because its cancelled request task stopped being its sole owner.
_CRITICAL_CLEANUP_TASKS: set[asyncio.Task[object]] = set()


async def finish_critical_handoff(awaitable: Awaitable[T]) -> T:
    """Wait for a durable handoff even if the parent is cancelled again.

    Callers invoke this *inside* their first ``CancelledError`` handler and
    re-raise that original cancellation after this function returns. Further
    ``Task.cancel()`` calls interrupt ``shield`` but not the child. We consume
    those interruptions and keep waiting until the child has either completed
    its durability contract or raised a real error.
    """

    task = asyncio.ensure_future(awaitable)
    _CRITICAL_CLEANUP_TASKS.add(task)
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # The parent was cancelled again. The original handler will
                # propagate cancellation after the durable child completes.
                continue
        return task.result()
    finally:
        _CRITICAL_CLEANUP_TASKS.discard(task)
