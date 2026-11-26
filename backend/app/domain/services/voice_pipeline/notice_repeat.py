"""Say a recording notice that was cut off once more, as the first audio of the next reply.

On an outgoing call the notice is part of the agent's first words. When the person
speaks over it, the first saying is never restarted (a restart from the top reads as
a broken robot, see ``modes/agent_first.py``). This module is the one allowed second
chance: the notice SENTENCE alone (never the opener), once, before the model's next
reply. It is proven by the same receipt rule as the first saying; if it is cut again
the recording stays marked "notice not confirmed" and nothing is tried a third time.
One exception: a try that provably put NO audio on the line (cancelled or failed before
even its first send was started) does not count. It is tried again at the next reply,
at most ``_MAX_TRIES`` tries per call; a sentence that may have been partly heard is
never started again.

Skipped when the person opted out, a transfer is under way, the call is ending, or
the notice is already proven. Inbound calls never reach it (the owed sentence is only
stored for outgoing calls). It never touches the recording gate and never raises into
the turn; only cancellation propagates.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


_MAX_TRIES = 3


def _audio_marks(session) -> tuple:
    """What ``tts_playback`` has counted so far: sends started (counted BEFORE each send)
    and the stamp it sets right after each chunk is handed to the media gateway."""
    return (getattr(session, "_tts_send_attempts", None), getattr(session, "_last_tts_chunk_at", None))


def _defer_if_no_audio(voice, session, call_id: str, marks_before) -> bool:
    """Synchronous. True when it is PROVEN that no audio of this sentence reached the line.

    Then the attempt is not spent (up to ``_MAX_TRIES`` unproven tries per call).
    Proof = ``tts_playback`` still says "pending" (never "submitted"/"partial") or "failed"
    (it fails before the first chunk) AND neither of its two marks moved since this try
    began: no send was even started (a cancel INSIDE the first send counts as started, so
    a sentence that may have been partly heard is never restarted), and no chunk was
    handed over.
    Anything else (missing, unknown, audio handed over) means spent. Never raises.
    """
    try:
        status = getattr(session, "_tts_delivery_status", None)
        if status not in ("pending", "failed"):
            return False
        if _audio_marks(session) != marks_before:
            return False
        tries = int(getattr(voice, "_notice_repeat_tries", 0) or 0) + 1
        voice._notice_repeat_tries = tries
        if tries >= _MAX_TRIES:
            return False
        voice._notice_repeat_done = False
        logger.info(
            "recording_notice_repeat_deferred call_id=%s reason=no_audio_sent tries=%d",
            call_id[:12], tries,
        )
        return True
    except Exception:  # noqa: BLE001 - bookkeeping only; default is spent
        return False


async def repeat_cut_notice_once(pipeline, session, websocket, transcript: str = "") -> str | None:
    """Returns the sentence if it was proven delivered (the caller puts it in the history), else None."""
    voice = getattr(session, "_voice_session_ref", None)
    if voice is None:
        return None
    sentence = getattr(voice, "_notice_repeat_text", None)
    if not sentence or getattr(voice, "_notice_repeat_done", False) is True:
        return None
    voice._notice_repeat_done = True  # tried: set before any await
    call_id = str(getattr(session, "call_id", "") or "")
    try:
        from app.domain.services.end_session_action import caller_signaled_end, previous_assistant_turn
        from app.domain.services.voice_pipeline.identity_disposition import contains_dnc
        from app.domain.services.recording_policy_service import (
            DISCLOSURE_FAILED,
            DISCLOSURE_SPOKEN,
            DISCLOSURE_TRANSMITTED,
            disclosure_delivered,
            record_disclosure_state,
        )
        from app.domain.services.telephony.modes.agent_first import (
            _disclosure_call_ids,
            _notice_skip_reason,
            notice_receipt_proves_delivery,
        )
        from app.domain.services.voice_pipeline.action_execution import transfer_in_progress

        call_ids = _disclosure_call_ids(voice)
        reason = None
        if getattr(session, "_caller_opted_out", False) is True or getattr(
            voice, "_caller_opted_out", False
        ) is True:
            reason = "opted_out"
        elif transfer_in_progress(session, voice):
            reason = "transfer_in_progress"
        elif contains_dnc(transcript):
            reason = "opt_out_phrase"
        elif caller_signaled_end(
            transcript,
            previous_assistant_text=previous_assistant_turn(session.conversation_history),
        ):
            reason = "caller_ending"
        else:
            reason = await _notice_skip_reason(voice, voice.call_id)
        if reason is None and disclosure_delivered(*call_ids):
            reason = "already_delivered"
        if reason is not None:
            logger.info(
                "recording_notice_repeat_skipped call_id=%s reason=%s", call_id[:12], reason
            )
            return None

        session._tts_playback_receipt = None
        session._tts_playback_utterance_id = None
        session._tts_delivery_status = "pending"
        marks_before = _audio_marks(session)
        was_in_flight = getattr(session, "_disclosure_in_flight", False)
        session._disclosure_in_flight = True  # a bare "Hello?" must not cut it
        try:
            interrupted = await pipeline.synthesize_and_send_audio(
                session, sentence, websocket, track_latency=False,
            )
        except asyncio.CancelledError:
            # Its task was cancelled mid-sentence (barge-in or hang-up): never silent.
            if not _defer_if_no_audio(voice, session, call_id, marks_before):
                logger.warning(
                    "recording_disclosure_unconfirmed call_id=%s attempt=2 cancelled=True tries=%d — "
                    "repeated notice cut off; not tried again",
                    call_id[:12], int(getattr(voice, "_notice_repeat_tries", 0) or 0),
                )
            raise
        finally:
            session._disclosure_in_flight = was_in_flight
            tracker = getattr(pipeline, "latency_tracker", None)
            if tracker is not None:
                try:
                    tracker.mark_llm_start(session.call_id)
                except Exception:  # noqa: BLE001 - bookkeeping only
                    pass

        receipt = getattr(session, "_tts_playback_receipt", None)
        if not notice_receipt_proves_delivery(session, interrupted):
            if not _defer_if_no_audio(voice, session, call_id, marks_before):
                logger.warning(
                    "recording_disclosure_unconfirmed call_id=%s attempt=2 interrupted=%s "
                    "delivery=%s tries=%d — repeated notice not proven; not tried again",
                    call_id[:12], interrupted, getattr(session, "_tts_delivery_status", None),
                    int(getattr(voice, "_notice_repeat_tries", 0) or 0),
                )
            return None
        record_disclosure_state(
            DISCLOSURE_SPOKEN if receipt["evidence"] == "transport_played"
            else DISCLOSURE_TRANSMITTED,
            *call_ids,
        )
        logger.info(
            "recording_disclosure_delivered call_id=%s attempt=2 late=True evidence=%s chars=%d",
            call_id[:12], receipt["evidence"], len(sentence),
        )
        ts = getattr(pipeline, "transcript_service", None)
        if ts is not None:
            ts.accumulate_turn(
                call_id=call_id,
                role="assistant",
                content=sentence,
                talklee_call_id=getattr(session, "talklee_call_id", None),
                turn_index=getattr(session, "turn_id", 0),
                event_type="assistant_response",
                is_final=True,
                include_in_plaintext=True,
                metadata={"recording_notice": "repeated_after_cut"},
            )
        return sentence
    except Exception as exc:  # noqa: BLE001 - never break the turn
        logger.warning(
            "recording_notice_repeat_error call_id=%s error_type=%s",
            call_id[:12], type(exc).__name__,
        )
    return None
