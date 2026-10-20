"""One short line when a tool step leaves the caller in silence (2026-10-20).

A knowledge lookup is instant (it reads the call's snapshot), but the second
model round after it is not, and voice actions can call outside services.
Vapi speaks its "request-response-delayed" message only when a tool is slow;
LiveKit speaks a filler only when the model's reply is a bare tool call. The
turn streamer arms this when a tool starts before the caller heard anything;
the line plays once the silence reaches TOOL_FILLER_AFTER_S, and is cancelled
if the real answer is ready first. It is not part of the reply the model sees
or of the measured reply time.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Silence since the turn began after which the caller hears a filler.
TOOL_FILLER_AFTER_S = 1.2

# Rotated by turn so the same words don't come back on every lookup.
FILLER_PHRASES = (
    "One moment.",
    "Let me check that.",
    "Just a second.",
    "Let me look that up.",
)


class ToolFiller:
    def __init__(
        self,
        speak: Optional[Callable[[str], Awaitable[bool]]],
        *,
        turn_start: float,
        turn_index: int,
        after_s: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._speak = speak
        self._turn_start = turn_start
        self._after_s = after_s
        self._clock = clock
        self.phrase = FILLER_PHRASES[turn_index % len(FILLER_PHRASES)]
        self._task: Optional[asyncio.Task] = None
        self._speaking = False
        # True when the caller talked over the filler (synthesize_and_send
        # reports it and clears the shared barge-in event, so the streamer
        # could not otherwise see it).
        self.interrupted = False

    def arm(self) -> None:
        """A tool started and the caller has heard nothing yet. At most once
        per turn."""
        if self._task is not None or self._speak is None:
            return
        after = TOOL_FILLER_AFTER_S if self._after_s is None else self._after_s
        delay = max(0.0, self._turn_start + after - self._clock())
        self._task = asyncio.create_task(self._run(delay))

    async def _run(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._speaking = True
        logger.info("voice_tool_filler phrase=%r after_ms=%d", self.phrase,
                    round((self._clock() - self._turn_start) * 1000))
        self.interrupted = bool(await self._speak(self.phrase))

    def cancel(self) -> None:
        """The turn itself was cancelled (barge-in, hangup): no filler may
        outlive it, speaking or not."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    async def settle(self) -> None:
        """Before real speech, and at the end of the turn: drop a filler that
        hasn't started, let one that is speaking finish. Never overlap."""
        task = self._task
        if task is None or task.done():
            return
        if self._speaking:
            with suppress(Exception):
                await task
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
