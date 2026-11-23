"""Background retries for a call summary the model could not produce.

A failed summary is never saved (store.py keeps that rule). This module only
asks again later: after ~60 s and, if that fails too, after ~300 s (plus jitter),
at most 2 retries per call and at most ``MAX_PENDING`` waiting or running in the
process.

The caller does everything it always did right after the first attempt (the CRM
record is written at once, without a summary). ``on_stored`` runs only when a
retry really stored a summary, so the CRM record can be completed. Nothing is
therefore lost when a retry never happens: retries live in memory, a process
restart drops the ones still waiting, and the call page's own on-demand summary
still covers those.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable

from app.domain.services.call_summary.summarizer import SUMMARY_UNAVAILABLE_HEADLINE

logger = logging.getLogger(__name__)

RETRY_DELAYS_SECONDS = (60.0, 300.0)
MAX_JITTER_SECONDS = 10.0
MAX_PENDING = 4

_pending = 0
_tasks: set = set()


def summary_unavailable(summary) -> bool:
    return isinstance(summary, dict) and summary.get("headline") == SUMMARY_UNAVAILABLE_HEADLINE


def _log(call_id: str, attempt: int, outcome: str) -> None:
    logger.info("call_summary_retry call_id=%s attempt=%d outcome=%s", str(call_id)[:12], attempt, outcome)


def start_retries(
    call_id: str,
    generate: Callable[[], Awaitable],
    on_stored: Callable[[], Awaitable],
) -> bool:
    """Take a slot and run the retries in the background; False when dropped.

    ``on_stored`` runs once, and only after a retry stored a summary.
    """
    global _pending
    if _pending >= MAX_PENDING:
        _log(call_id, 1, "dropped")
        return False
    try:
        task = asyncio.get_running_loop().create_task(_run(call_id, generate, on_stored))
    except RuntimeError:
        _log(call_id, 1, "dropped")
        return False
    _pending += 1
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def _run(call_id, generate, on_stored) -> None:
    global _pending
    stored = False
    try:
        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            await asyncio.sleep(delay + random.uniform(0, MAX_JITTER_SECONDS))
            outcome = "unavailable"
            try:
                summary = await generate()
                if summary is None:
                    # Nothing was stored and nothing will be (for example the
                    # transcript changed): asking again cannot help.
                    outcome = "nothing_stored"
                elif not summary_unavailable(summary):
                    outcome = "ok"
            except Exception as exc:  # noqa: BLE001 - a retry must never escape
                logger.warning("call_summary_retry call_id=%s attempt=%d error=%s",
                               str(call_id)[:12], attempt, type(exc).__name__)
            _log(call_id, attempt, outcome)
            if outcome != "unavailable":
                stored = outcome == "ok"
                break
    finally:
        # Also on cancellation (a shutdown): the slot is always given back.
        _pending -= 1
    if not stored:
        return
    try:
        await on_stored()
    except Exception as exc:  # noqa: BLE001
        logger.warning("call_summary_retry call_id=%s after-store step failed error=%s",
                       str(call_id)[:12], type(exc).__name__)
