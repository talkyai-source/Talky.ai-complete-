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


_DEFAULT_INBOUND_WAIT_S = 2.0
_INBOUND_READY_POLL_S = 0.25
_INBOUND_READY_MAX_S = 15.0
_INBOUND_SETTLE_S = 0.3


def inbound_silent_pickup_wait_s() -> float:
    """Wait for a TRUE incoming call (INBOUND_SILENT_PICKUP_S, default 2.0, 0 disables)."""
    raw = os.getenv("INBOUND_SILENT_PICKUP_S")
    if raw is None or not raw.strip():
        return _DEFAULT_INBOUND_WAIT_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_INBOUND_WAIT_S


def _is_inbound(voice_session) -> bool:
    from app.domain.services.telephony.recording import _is_true_inbound_session

    return _is_true_inbound_session(voice_session)


def _inbound_skip_reason(voice_session, session) -> str:
    """Why an incoming call must not be greeted now, or ''."""
    if getattr(voice_session, "pipeline", None) is None or getattr(
        voice_session, "realtime_bridge", None
    ) is not None:
        return "realtime_session"
    if str(getattr(voice_session, "_inbound_selected_action", "") or "").strip().lower() == "voicemail":
        return "voicemail_action"
    if getattr(session, "llm_active", False):
        return "llm_active"
    if getattr(session, "_instant_opener_in_flight", False):
        return "greeting_playing"
    return _caller_has_spoken(session)


async def _run_inbound(voice_session, session, call: str, started: float, wait: float) -> bool:
    """Greet a silent TRUE incoming call (caller-first) once.

    Scheduled before ``prepare_inbound_recording`` runs and that function
    decides, and if needed speaks the recording notice, before the pipeline
    task exists. So wait for ``pipeline_task``: the greeting then always
    follows the recording decision, and this code never opens the gate.
    """
    ready_deadline = time.monotonic() + _INBOUND_READY_MAX_S
    while getattr(voice_session, "pipeline_task", None) is None:
        if time.monotonic() >= ready_deadline:
            logger.info("inbound_silent_pickup_skipped call=%s reason=setup_not_finished", call)
            return False
        await asyncio.sleep(_INBOUND_READY_POLL_S)
        if _call_ended(session):
            return False
    # The pipeline (and so the voice-onset signal) only runs from here. What the
    # caller said during a recording notice is buffered and is processed now.
    # A notice was spoken when ``_tts_delivery_status`` is set: only
    # _speak_recording_disclosure sets it, and it is the only TTS that can run
    # before the pipeline task exists. After a notice the caller gets the full
    # wait of silence counted from now; with no notice the wait from answer has
    # already passed, so only a short settle lets the buffered audio drain.
    notice_spoken = getattr(session, "_tts_delivery_status", None) is not None
    await asyncio.sleep(wait if notice_spoken else _INBOUND_SETTLE_S)
    if _call_ended(session):
        return False
    reason = _inbound_skip_reason(voice_session, session)
    if reason:
        logger.info("inbound_silent_pickup_skipped call=%s reason=%s", call, reason)
        return False

    # What an agent-first incoming call says: the campaign's saved greeting.
    # With none saved, the answering line that also says it is an AI assistant.
    if not str(getattr(voice_session, "_pinned_inbound_greeting", "") or "").strip():
        from app.domain.services.voice_pipeline.inbound_answer_greeting import (
            build_inbound_answer_greeting,
        )

        voice_session._pinned_inbound_greeting = build_inbound_answer_greeting(session)
        voice_session._presynth_greeting_audio = None
        voice_session._presynth_greeting_text = None

    logger.info(
        "inbound_silent_pickup call=%s waited_s=%.1f — the caller said nothing, agent greets first",
        call, time.monotonic() - started,
    )
    from app.domain.services.telephony.modes import agent_first

    await agent_first._send_outbound_greeting(voice_session)
    return True


async def run_silent_pickup(voice_session, wait_s: float | None = None) -> bool:
    """Wait, then greet if still silent. Returns True when the greeting ran."""
    from app.domain.services.voice_pipeline.turn_helpers import _first_speaker_label

    inbound = _is_inbound(voice_session)
    if wait_s is None:
        wait = inbound_silent_pickup_wait_s() if inbound else silent_pickup_wait_s()
    else:
        wait = wait_s
    if wait <= 0:
        return False
    started = time.monotonic()
    await asyncio.sleep(wait)

    session = getattr(voice_session, "call_session", None)
    call = str(getattr(voice_session, "call_id", "") or "")[:12]
    if session is None or _call_ended(session):
        return False
    if _first_speaker_label(session) != "user":
        return False
    if inbound:
        return await _run_inbound(voice_session, session, call, started, wait)
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
    if wait_s is None:
        wait = inbound_silent_pickup_wait_s() if _is_inbound(voice_session) else silent_pickup_wait_s()
    else:
        wait = wait_s
    if wait <= 0:
        return None
    task = asyncio.create_task(run_silent_pickup(voice_session, wait))

    def _done(t: "asyncio.Task") -> None:
        if not t.cancelled() and t.exception():
            logger.error("caller_first_silent_pickup failed: %s", t.exception())

    task.add_done_callback(_done)
    voice_session._greeting_task = task
    return task
