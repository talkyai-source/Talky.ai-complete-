"""Caller-first outbound pickup with nobody speaking: the agent speaks first.

Live 2026-10-20: a callee picked up and said nothing for 57.6 s (caller RMS
6-7, no transcript). Caller-first waits for the caller, and the silence monitor
only hangs up at VOICE_SILENCE_HANGUP_S=60. Owner decision: if nobody has
spoken CALLER_FIRST_SILENT_PICKUP_S (default 4.0, 0 disables) after pickup, the
agent runs the normal agent-first greeting path (recording notice, then opener).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_WAIT_S = 4.0
_ENDED_STATES = {"ending", "ended", "error"}


def silent_pickup_wait_s() -> float:
    raw = os.getenv("CALLER_FIRST_SILENT_PICKUP_S")
    if raw is None or not raw.strip():
        return _DEFAULT_WAIT_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_WAIT_S


def _caller_has_spoken(session) -> str:
    """Return the name of the signal showing someone spoke, or ''."""
    from app.domain.models.conversation import MessageRole

    if any(
        getattr(m, "role", None) == MessageRole.USER
        for m in (getattr(session, "conversation_history", None) or [])
    ):
        return "user_message"
    if getattr(session, "_caller_turn_open_since", None) is not None:
        return "turn_open"
    if (getattr(session, "current_user_input", None) or "").strip():
        return "partial_transcript"
    if getattr(session, "_caller_voice_onset_at", None) is not None:
        return "voice_onset"
    if getattr(session, "tts_active", False):
        return "agent_speaking"
    if getattr(session, "_amd_voicemail", False) or getattr(session, "_machine_screening", False):
        return "machine_detected"  # never greet a voicemail or call screener
    return ""


def _call_ended(session) -> bool:
    state = getattr(session, "state", None)
    return str(getattr(state, "value", state) or "").lower() in _ENDED_STATES


async def run_silent_pickup(voice_session, wait_s: float | None = None) -> bool:
    """Wait, then greet if still silent. Returns True when the greeting ran."""
    from app.domain.services.telephony.recording import _is_true_inbound_session
    from app.domain.services.voice_pipeline.turn_helpers import _first_speaker_label

    wait = silent_pickup_wait_s() if wait_s is None else wait_s
    if wait <= 0:
        return False
    started = time.monotonic()
    await asyncio.sleep(wait)

    session = getattr(voice_session, "call_session", None)
    call = str(getattr(voice_session, "call_id", "") or "")[:12]
    if session is None or _call_ended(session):
        return False
    if _is_true_inbound_session(voice_session) or _first_speaker_label(session) != "user":
        return False
    signal = _caller_has_spoken(session)
    if signal:
        logger.info("caller_first_silent_pickup_skipped call=%s reason=%s", call, signal)
        return False

    logger.info(
        "caller_first_silent_pickup call=%s waited_s=%.1f — nobody spoke, agent speaks first",
        call, time.monotonic() - started,
    )
    from app.domain.services.telephony.modes import agent_first

    await agent_first._send_outbound_greeting(voice_session)
    return True


def schedule_silent_pickup(voice_session, wait_s: float | None = None):
    """Start the watcher task; stored on ``_greeting_task`` so call teardown
    cancels it. Returns None when disabled."""
    wait = silent_pickup_wait_s() if wait_s is None else wait_s
    if wait <= 0:
        return None
    task = asyncio.create_task(run_silent_pickup(voice_session, wait))

    def _done(t: "asyncio.Task") -> None:
        if not t.cancelled() and t.exception():
            logger.error("caller_first_silent_pickup failed: %s", t.exception())

    task.add_done_callback(_done)
    voice_session._greeting_task = task
    return task
