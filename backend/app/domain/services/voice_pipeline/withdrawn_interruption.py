"""Answer the caller again when an interruption turns out to carry no words.

AssemblyAI raises a barge-in on the first partial transcript, which cancels the
agent's reply. If it then closes that turn with NO words
(``metadata["empty_turn"] is True``) the transcript handler drops the turn on
purpose, and nothing made the agent speak again: the existing recovery
(``VoicePipelineService._resume_after_false_barge_in``) returns early whenever
caller text was seen after the barge-in, and on AssemblyAI the barge-in's own
partial always counts as such text. The caller sat in silence.

``handle_barge_in`` remembers the reply it cancelled (``remember_cut_reply``);
the ``empty_turn`` branch of the transcript handler calls
``reissue_after_withdrawn_turn`` which, under the guards below, runs that reply
again. It shares the per-caller-line cap with the old recovery
(``session._false_barge_resumes``), so the two cannot loop between them.
The withdrawn partial is never sent to the model, and the caller's text is
never logged here.

The re-issue also applies on the first turn: the coded opener is not restarted;
the notice is said once by the existing repeat and the model then answers the
caller's first words, exactly as already happens when the caller's cutting
words are real.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time

from app.domain.models.conversation import MessageRole

logger = logging.getLogger(__name__)

_STORE_ATTR = "_withdrawn_cut_reply"
_OFF_VALUES = {"0", "false", "no", "off"}
_ENDED_STATES = {"ending", "ended", "error"}
_DEFAULT_MAX_AGE_S = 8.0
# The tail of the cut reply may still be sent a moment after the cut; only audio
# later than this after the cut means the agent really spoke again.
_SPOKE_SINCE_TOLERANCE_S = 0.5


def _tuple_of_orders(value) -> tuple:
    try:
        return tuple(value or ())
    except Exception:  # noqa: BLE001 - a malformed value becomes empty
        return ()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def remember_cut_reply(session, cancelled: dict, barge_at: float) -> None:
    """Keep what a barge-in cancelled, if it was a final reply with caller text."""
    try:
        text = cancelled.get("text")
        if not text or cancelled.get("type") != "final":
            return
        setattr(session, _STORE_ATTR, {
            "text": text,
            "caller_turn_order": cancelled.get("caller_turn_order"),
            "prior_caller_turn_orders": _tuple_of_orders(
                cancelled.get("prior_caller_turn_orders")
            ),
            "barge_at": barge_at,
        })
    except Exception:  # noqa: BLE001 - bookkeeping must never break a barge-in
        return


def _max_age_s() -> float:
    try:
        value = float(os.getenv("VOICE_WITHDRAWN_REISSUE_MAX_AGE_S", str(_DEFAULT_MAX_AGE_S)))
    except ValueError:
        return _DEFAULT_MAX_AGE_S
    if not math.isfinite(value) or value <= 0:
        return _DEFAULT_MAX_AGE_S
    return value


def _probe(gateway, name, call_id):
    """Synchronous gateway probe: the value, or None when unusable."""
    fn = getattr(gateway, name, None)
    if not callable(fn):
        return None
    try:
        value = fn(call_id)
    except Exception:  # noqa: BLE001 - a broken probe must not drop a live turn
        return None
    if asyncio.iscoroutine(value):  # async stand-in: not a usable answer
        value.close()
        return None
    return value


def _call_over(pipeline, session) -> bool:
    state = getattr(session, "state", None)
    if str(getattr(state, "value", state) or "").lower() in _ENDED_STATES:
        return True
    if (
        getattr(session, "_end_session_action_handled", False) is True
        or getattr(session, "_end_call_requested", False) is True
    ):
        return True
    voice = getattr(session, "_voice_session_ref", None)
    if getattr(voice, "_call_ending", False) is True:
        return True
    # Same probe as turn_ender._call_is_over: only an explicit False is "over".
    if _probe(getattr(pipeline, "media_gateway", None), "is_session_active", session.call_id) is False:
        return True
    # Same gateway and call id as agent_first._notice_skip_reason.
    gateway = getattr(voice, "media_gateway", None) if voice is not None else None
    hangup_id = getattr(voice, "call_id", None) if gateway is not None else None
    if gateway is None or hangup_id is None:
        gateway, hangup_id = getattr(pipeline, "media_gateway", None), session.call_id
    for name in ("is_hangup_requested", "is_hangup_pending"):
        if _probe(gateway, name, hangup_id) is True:
            return True
    return False


def _skip_reason(pipeline, session, stored: dict, now: float) -> str:
    """Name of the first guard that forbids a re-issue, or '' when none does."""
    if os.getenv("VOICE_WITHDRAWN_REISSUE_ENABLED", "").strip().lower() in _OFF_VALUES:
        return "switched_off"
    from app.domain.services.telephony.config import _call_direction

    if _call_direction(session) == "inbound":
        return "inbound"
    if now - stored["barge_at"] > _max_age_s():
        return "too_old"
    call_id = session.call_id
    if call_id not in pipeline._barge_in_events:
        return "session_gone"
    if _call_over(pipeline, session):
        return "call_ending"
    if getattr(session, "_caller_opted_out", False) is True:
        return "opted_out"
    if (
        getattr(session, "_amd_voicemail", False) is True
        or getattr(session, "_machine_screening", False) is True
    ):
        return "voicemail"
    from app.domain.services.voice_pipeline.action_execution import transfer_in_progress

    voice = getattr(session, "_voice_session_ref", None)
    if transfer_in_progress(session, voice) or getattr(voice, "_transfer_connected", False) is True:
        return "transfer"
    order = stored.get("caller_turn_order")
    if order is not None and getattr(session, "_accepted_caller_turn_order", None) != order:
        return "newer_turn"
    last = getattr(session, "_last_tts_chunk_at", None)
    if _is_number(last) and last > stored["barge_at"] + _SPOKE_SINCE_TOLERANCE_S:
        return "agent_spoke_since"
    pending = pipeline._pending_llm_tasks.get(call_id)
    if (
        (pending is not None and not pending.done())
        or getattr(session, "tts_active", False)
        or getattr(session, "llm_active", False)
    ):
        return "reply_running"
    return ""


async def _run_reissued_turn(pipeline, session, websocket, text):
    # The kept USER copy is dropped HERE, not before the task is created: the re-run
    # appends it again, and a task cancelled before its first step must not lose it.
    history = session.conversation_history
    if (
        history
        and history[-1].role == MessageRole.USER
        and history[-1].content == text
    ):
        session.conversation_history = history[:-1]
    return await pipeline.handle_turn_end(session, websocket, source="final", user_text=text)


async def reissue_after_withdrawn_turn(pipeline, session, websocket) -> bool:
    """Re-issue the reply a barge-in cut, now that its turn was withdrawn.

    Always consumes the stored reply. Returns True only when a turn was started.
    """
    try:
        stored = getattr(session, _STORE_ATTR, None)
        setattr(session, _STORE_ATTR, None)
        if not isinstance(stored, dict) or not stored.get("text"):
            return False
        call_id = session.call_id
        short = call_id[:12]
        now = time.monotonic()

        if not _is_number(stored.get("barge_at")):
            logger.info("withdrawn_interruption_not_reissued call=%s reason=malformed", short)
            return False
        reason = _skip_reason(pipeline, session, stored, now)
        text = stored["text"]
        if not reason:
            # Same bookkeeping and cap as _resume_after_false_barge_in.
            try:
                cap = int(os.getenv("VOICE_FALSE_BARGE_IN_MAX_RESUMES", "2"))
            except ValueError:
                cap = 2
            state = getattr(session, "_false_barge_resumes", None)
            count = state[1] if isinstance(state, tuple) and state[0] == text else 0
            if count >= cap:
                reason = "capped"
        if reason:
            logger.info("withdrawn_interruption_not_reissued call=%s reason=%s", short, reason)
            return False

        session._false_barge_resumes = (text, count + 1)
        session._withdrawn_reissued_for = stored["barge_at"]
        logger.info(
            "withdrawn_interruption_reissued call=%s age_ms=%d",
            short, int((now - stored["barge_at"]) * 1000),
        )
        task = asyncio.create_task(_run_reissued_turn(pipeline, session, websocket, text))
        task._turn_type = "final"
        task._utterance_seq = pipeline._utterance_seq.get(call_id, 0)
        task._caller_turn_order = stored.get("caller_turn_order")
        task._prior_caller_turn_orders = _tuple_of_orders(stored.get("prior_caller_turn_orders"))
        task._withdrawn_reissue = True
        task._source_text = text
        pipeline._pending_llm_tasks[call_id] = task
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - recovery must never break the STT loop
        logger.warning("withdrawn_interruption_reissue_failed error=%s", type(exc).__name__)
        return False
