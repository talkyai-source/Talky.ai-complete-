"""Background retries for a call summary the model could not produce.

A failed summary is never saved (store.py keeps that rule). This module only
asks again later: after ~60 s and, if that fails too, after ~300 s (plus jitter),
at most 2 retries per call and at most ``MAX_PENDING`` waiting or running in the
process. Retries live in memory: a process restart loses the ones still waiting
(the call page's lazy backfill still covers those).
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
    finish: Callable[[], Awaitable],
) -> bool:
    """Take a slot and run the retries in the background; False when dropped.

    ``finish`` runs exactly once after the last attempt (the work that follows
    the summary). When False is returned nothing was scheduled and the caller
    must run ``finish`` itself.
    """
    global _pending
    if _pending >= MAX_PENDING:
        _log(call_id, 1, "dropped")
        return False
    try:
        task = asyncio.get_running_loop().create_task(_run(call_id, generate, finish))
    except RuntimeError:
        _log(call_id, 1, "dropped")
        return False
    _pending += 1
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def _run(call_id, generate, finish) -> None:
    global _pending
    try:
        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            await asyncio.sleep(delay + random.uniform(0, MAX_JITTER_SECONDS))
            outcome = "unavailable"
            try:
                if not summary_unavailable(await generate()):
                    outcome = "ok"
            except Exception as exc:  # noqa: BLE001 - a retry must never escape
                logger.warning("call_summary_retry call_id=%s attempt=%d error=%s",
                               str(call_id)[:12], attempt, type(exc).__name__)
            _log(call_id, attempt, outcome)
            if outcome == "ok":
                break
    except (Exception, asyncio.CancelledError) as exc:
        # A shutdown cancels the wait. The slot is still released and what follows
        # the summary still runs once, so a restart does not lose the CRM record.
        logger.warning("call_summary_retry call_id=%s aborted error=%s", str(call_id)[:12], type(exc).__name__)
    finally:
        _pending -= 1
    try:
        await finish()
    except Exception as exc:  # noqa: BLE001
        logger.warning("call_summary_retry call_id=%s finish failed error=%s",
                       str(call_id)[:12], type(exc).__name__)
