"""Say a recording notice that was cut off once more, as the agent's next reply (realtime path).

The realtime twin of ``domain/services/voice_pipeline/notice_repeat.py``. On an outgoing
recorded call the opening carries the notice. When the person speaks while the opening
audio is still being sent, ``opening_notice`` settles the notice "unconfirmed" and it is
never restarted. This module is the one allowed second chance: the notice SENTENCE alone
(never the opener), once, as the agent's next reply.

How, with the model speaking: the next reply is withheld BEFORE any byte of it is sent,
and the model is asked (``repair_unspoken_response``) to say exactly the sentence. That
reply is played only if its transcript matches the sentence word for word, and it counts
as delivered only on the same receipt rule as the opening. Then the conversation is
continued once, if the person has not spoken since.

State only moves forward: at most one exact request, one plain repair, one continuation
and one tool-call deferral per call. Cut again or not proven: logged
``recording_disclosure_unconfirmed ... attempt=2`` and never tried a third time.
Skipped (the reply plays untouched) for opt-out, transfer, goodbye, a call that is
ending, a notice already proven, or caller words that are still unknown.

It never touches the recording gate. Nothing here raises into the bridge except
cancellation. Log lines carry no phone number and none of the caller's words.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# How long the repeat waits for the caller's final words before it gives up and skips.
_CALLER_WORDS_WAIT_S = 1.0
_REPAIR_REASON = "The recording notice was cut off and must be said once."


def _cid(gate) -> str:
    return str(gate.call_id)[:12]


def _epoch(bridge):
    return getattr(getattr(bridge, "_rt", None), "_response_epoch", None)


def owe(gate, utterance) -> None:
    """Called when the opening was cut after some audio was sent: the sentence is owed once.

    Not owed for incoming calls or calls with no notice (no sentence is stored for them),
    when the account's policy refused recording, or when the whole audio was already
    handed to the phone system (only its receipt was awaited): the person heard it.
    """
    try:
        if (gate.repeat_state == "none" and gate.outbound and gate.notice_owed
                and gate.notice_sentence
                and not getattr(gate.voice_session, "_recording_refused_by_policy", False)
                and utterance.get("submission_started") and not utterance.get("submission_complete")):
            gate.repeat_state = "owed"
            logger.info("recording_notice_repeat_owed call_id=%s path=realtime", _cid(gate))
    except Exception:  # noqa: BLE001 - bookkeeping only
        pass


def _has_tool_call(event) -> bool:
    output = ((event.raw or {}).get("response") or {}).get("output") or []
    return any(isinstance(item, dict) and item.get("type") == "function_call" for item in output)


async def _await_caller_words(bridge) -> bool:
    """True when the caller's final words are known (bounded wait)."""
    deadline = time.monotonic() + _CALLER_WORDS_WAIT_S
    while getattr(bridge, "_caller_transcript_pending", False):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.02)
    return True


async def _skip_reason(bridge, gate):
    from app.domain.services.end_session_action import caller_signaled_end
    from app.domain.services.recording_policy_service import disclosure_delivered
    from app.domain.services.telephony.modes.agent_first import _notice_skip_reason
    from app.domain.services.voice_pipeline.action_execution import transfer_in_progress
    from app.domain.services.voice_pipeline.identity_disposition import contains_dnc

    action = bridge._action_session
    voice = gate.voice_session
    latest = getattr(bridge, "_latest_caller_text", "") or ""
    if getattr(action, "_caller_opted_out", False) is True:
        return "opted_out"
    if transfer_in_progress(action, voice):
        return "transfer_in_progress"
    if contains_dnc(latest):
        return "opt_out_phrase"
    if caller_signaled_end(latest, previous_assistant_text=getattr(bridge, "_previous_assistant_text", "")):
        return "caller_ending"
    if (getattr(bridge, "_closing_after_generation", None) is not None
            or getattr(action, "_end_call_requested", False) is True
            or getattr(bridge, "_hangup_started", False) is True):
        return "end_call_armed"
    reason = await _notice_skip_reason(voice, gate.call_id)
    if reason:
        return reason
    if disclosure_delivered(*gate.call_ids):
        return "already_delivered"
    return None


def _skip(gate, reason: str) -> bool:
    gate.repeat_state = "skipped"
    logger.info("recording_notice_repeat_skipped call_id=%s reason=%s path=realtime", _cid(gate), reason)
    return True


async def admit(bridge, event, utterance) -> bool:
    """True when the reply may play; False when it was withheld (the exact sentence was requested)."""
    gate = bridge._opening_gate
    if gate.repeat_state not in ("owed", "requested"):
        return True
    try:
        from app.realtime.opening_notice import normalize_words, words_match

        if gate.repeat_state == "owed":
            # Only the provider whose "delete the held reply, then say this text" repair is
            # known to work. Anything else (xAI, a fake with no name) is left untouched.
            if getattr(bridge._rt, "observation_provider", "") != "openai":
                return _skip(gate, "provider_unverified")
            if _has_tool_call(event):
                if gate.repeat_deferrals == 0:
                    gate.repeat_deferrals = 1
                    logger.info("recording_notice_repeat_deferred call_id=%s reason=tool_call path=realtime",
                                _cid(gate))
                    return True
                return _skip(gate, "tool_calls")
            if not await _await_caller_words(bridge):
                return _skip(gate, "caller_words_unknown")
            reason = await _skip_reason(bridge, gate)
            if reason:
                return _skip(gate, reason)
            gate.repeat_state = "requested"
            gate.repeat_epoch = _epoch(bridge)
            logger.info("recording_notice_repeat_requested call_id=%s words=%d path=realtime",
                        _cid(gate), len(normalize_words(gate.notice_sentence)))
            await bridge._rt.repair_unspoken_response(
                (event.raw or {}).get("response") or {},
                required_text=gate.notice_sentence, required_reason=_REPAIR_REASON)
            return False

        # requested: this is the reply that answers the request (or a later one).
        response = (event.raw or {}).get("response") or {}
        if getattr(bridge._action_session, "_caller_opted_out", False) is True:
            return _skip(gate, "opted_out")
        if _epoch(bridge) != gate.repeat_epoch:
            # The person spoke again: this reply answers their newer words. It plays as an
            # ordinary reply (never flagged, never counted as the notice), so the call is
            # never left waiting with nothing requested.
            gate.repeat_state = "done"
            logger.warning("recording_disclosure_unconfirmed call_id=%s attempt=2 "
                           "reason=caller_spoke_again path=realtime", _cid(gate))
            return True
        if words_match(event.text, gate.notice_sentence) and not _has_tool_call(event):
            gate.repeat_state = "playing"
            utterance["opening_gate"] = True
            utterance["notice_repeat"] = True
            return True
        gate.repeat_state = "done"
        logger.warning("recording_disclosure_unconfirmed call_id=%s attempt=2 reason=text_mismatch "
                       "path=realtime", _cid(gate))
        await bridge._rt.repair_unspoken_response(response)
        return False
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - never break the call
        gate.repeat_state = "done"
        logger.warning("recording_notice_repeat_error call_id=%s error_type=%s path=realtime",
                       _cid(gate), type(exc).__name__)
        return True


async def _continue(bridge, gate, utterance) -> None:
    """Ask the model to carry on, once, if the person has not spoken since the request.

    Independent of the delivery proof: the person's question was withheld, so the
    conversation must go on even when the sentence is not proven delivered.
    """
    try:
        if (utterance.get("status") != "interrupted"
                and _epoch(bridge) == gate.repeat_epoch
                and not getattr(bridge, "_caller_transcript_pending", False)):
            await bridge._rt.request_response()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - never break the call
        logger.warning("recording_notice_repeat_error call_id=%s error_type=%s path=realtime",
                       _cid(gate), type(exc).__name__)


async def settle(bridge, utterance, text) -> None:
    """After the sentence played: delivered only on the opening's receipt rule; then continue."""
    gate = bridge._opening_gate
    if gate.repeat_state != "playing":
        return
    gate.repeat_state = "done"
    try:
        from app.domain.services.recording_policy_service import (
            DISCLOSURE_SPOKEN,
            DISCLOSURE_TRANSMITTED,
            record_disclosure_state,
        )
        from app.realtime.opening_notice import receipt_evidence

        receipt = utterance.get("receipt")
        evidence = receipt_evidence(receipt, utterance.get("id"))
        if (evidence is None or utterance.get("status") == "interrupted"
                or not utterance.get("submission_started")):
            logger.warning(
                "recording_disclosure_unconfirmed call_id=%s attempt=2 interrupted=%s delivery=%s "
                "path=realtime - repeated notice not proven; not tried again", _cid(gate),
                utterance.get("status") == "interrupted",
                (receipt or {}).get("status") if isinstance(receipt, dict) else None)
        else:
            record_disclosure_state(
                DISCLOSURE_SPOKEN if evidence == "transport_played" else DISCLOSURE_TRANSMITTED,
                *gate.call_ids)
            logger.info("recording_disclosure_delivered call_id=%s attempt=2 late=True evidence=%s "
                        "chars=%d path=realtime", _cid(gate), evidence, len(gate.notice_sentence))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - never mark delivered on an error
        logger.warning("recording_notice_repeat_error call_id=%s error_type=%s path=realtime",
                       _cid(gate), type(exc).__name__)
    await _continue(bridge, gate, utterance)


def settle_interrupted(bridge, utterance) -> None:
    """From the playback ``finally``: the sentence was cut again. Unconfirmed, never retried."""
    gate = bridge._opening_gate
    if gate.repeat_state != "playing":
        return
    gate.repeat_state = "done"
    logger.warning(
        "recording_disclosure_unconfirmed call_id=%s attempt=2 interrupted=True delivery=%s "
        "path=realtime - repeated notice not proven; not tried again", _cid(gate),
        "submitted" if utterance.get("submission_started") else "no_audio")
