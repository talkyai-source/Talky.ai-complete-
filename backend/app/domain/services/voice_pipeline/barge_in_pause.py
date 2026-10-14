"""Pause-then-decide barge-in.

The agent stops speaking the moment the caller starts talking, then decides:
real words interrupt for good (the existing ``handle_barge_in``), while a turn
that ends as only a backchannel ("yeah", "mhm") resumes from where it held.

Holding is done on audio the agent has not sent yet, so a resume continues
with the next unsent word and nothing is lost or repeated. On the phone the
gateway's small queue (~300 ms) drains during the hold; the browser test page
suspends its own player on ``tts_pause`` and continues on ``tts_resume``.

Production agents work this way (LiveKit pauses after speech starts and
resumes on a false interruption). Before this, the agent waited for the
caller's first non-filler word before stopping and talked over callers for
1.2-3.1 s on half the interruptions in test call 850b885e (2026-10-13).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from app.domain.services.voice_pipeline.playback_gate import _caller_quiet_for_s

logger = logging.getLogger(__name__)

# Never hold the agent longer than this without a decision. AssemblyAI always
# ends a turn it started, so a decision normally arrives well before this.
PAUSE_MAX_S = 2.0
# Carry on once the caller has gone quiet, instead of waiting for the provider
# to close the turn: the hold only starts on a filler or backchannel ("uh",
# "yeah"; real words interrupt at once), and in a live test (2026-10-14) the
# provider closed a lone "Yeah." 1.1 s after the caller stopped, so the agent
# sat silent for 1.5 s on every backchannel. 0.6 s is above an inter-word gap
# and a short breath; words that follow still interrupt for good. Uses the same
# voiced-frame clock as the pre-playback gate; no measurement means no opinion
# (wait for the decision as before). 0 disables.
PAUSE_QUIET_RESUME_S = float(os.getenv("VOICE_BARGE_IN_QUIET_RESUME_S", "0.6"))
_POLL_S = 0.02


def is_paused(session: Any) -> bool:
    return getattr(session, "_tts_paused_at", None) is not None


async def _notify(websocket: Any, message_type: str, call_id: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": message_type})
    except Exception as exc:  # noqa: BLE001 - a lost notice must not stop the call
        logger.debug("%s_notify_failed call=%s err=%s", message_type, call_id, type(exc).__name__)


async def pause_for_caller(session: Any, websocket: Any = None, *, text: str = "") -> bool:
    """Hold the agent's audio while the caller's first words are still unclear.

    Only when the agent is audibly speaking: a caller taking their normal turn
    has nothing to pause.
    """
    if is_paused(session) or not getattr(session, "tts_active", False):
        return False
    call_id = str(getattr(session, "call_id", "?"))[:12]
    session._tts_paused_at = time.monotonic()
    logger.info("barge_in_paused call=%s words=%d", call_id, len((text or "").split()))
    await _notify(websocket, "tts_pause", call_id)
    # The playback loop only checks between chunks; once a reply is with the
    # player there is no next chunk, so release an undecided hold from here.
    watcher = asyncio.create_task(_release_when_quiet_or_late(session, websocket))
    _watchers.add(watcher)
    watcher.add_done_callback(_watchers.discard)
    return True


_watchers: set = set()


async def _release_when_quiet_or_late(session: Any, websocket: Any) -> None:
    try:
        await wait_while_paused(session, websocket)
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not end the call
        logger.debug("barge_in_pause_watch_failed call=%s err=%s",
                     str(getattr(session, "call_id", "?"))[:12], type(exc).__name__)


async def resume_after_caller(session: Any, websocket: Any = None, *, reason: str) -> bool:
    """Carry on speaking from where the hold started."""
    paused_at = getattr(session, "_tts_paused_at", None)
    if paused_at is None:
        return False
    session._tts_paused_at = None
    call_id = str(getattr(session, "call_id", "?"))[:12]
    logger.info(
        "barge_in_resumed call=%s reason=%s held_ms=%.0f",
        call_id, reason, (time.monotonic() - paused_at) * 1000.0,
    )
    await _notify(websocket, "tts_resume", call_id)
    return True


def end_pause_for_interruption(session: Any) -> None:
    """A confirmed interruption replaces the hold; the stop clears the player."""
    if getattr(session, "_tts_paused_at", None) is not None:
        session._tts_paused_at = None


async def wait_while_paused(
    session: Any,
    websocket: Any = None,
    *,
    barge_in_event: Optional[asyncio.Event] = None,
) -> None:
    """Called by the playback loop before sending each chunk."""
    while is_paused(session):
        if barge_in_event is not None and barge_in_event.is_set():
            return
        paused_at = getattr(session, "_tts_paused_at", None)
        if paused_at is not None and time.monotonic() - paused_at >= PAUSE_MAX_S:
            await resume_after_caller(session, websocket, reason="no_decision")
            return
        quiet = _caller_quiet_for_s(session)
        if PAUSE_QUIET_RESUME_S > 0 and quiet is not None and quiet >= PAUSE_QUIET_RESUME_S:
            await resume_after_caller(session, websocket, reason="caller_quiet")
            return
        await asyncio.sleep(_POLL_S)
