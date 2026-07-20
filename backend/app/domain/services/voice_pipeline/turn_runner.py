"""Per-turn LLM+TTS execution with atomic conversation-history management.

Extracted from VoicePipelineService._run_turn (item 2, slice 4). Holds a
reference to the pipeline and reads its collaborators (_stream_llm_and_tts,
_supports_llm_end_session_action, _shutdown_session_for_end_action,
transcript_service) at CALL time — same pattern as TtsPlayback, so
attribute patching/mocking keeps working and the runtime path is
identical. The service keeps _run_turn() as a thin delegator (tests call
it directly).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import is_dataclass, replace
from typing import Optional

from fastapi import WebSocket

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.end_session_action import (
    parse_end_session_action,
    should_honor_end_session,
)
from app.domain.services.voice_pipeline import capture_mode
from app.services.scripts import (
    CallState as CapturedSlotsState,
    update_state_from_user_turn,
)
from app.services.scripts.call_state_tracker import _classify_core_confirmation
from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_agent_readback,
    extract_email_from_speech,
    extract_phone_from_speech,
    natural_email_readback,
    natural_phone_readback,
)
from app.domain.services.voice_pipeline.confirm_llm import llm_confirmation_verdict
from app.domain.services.voice_pipeline.identity_disposition import (
    IdentityDisposition,
    contains_explicit_goodbye,
)

logger = logging.getLogger(__name__)

# Spoken when a phantom end-session is suppressed: the model tried to hang up
# but the caller never signalled they were done. Keeps the call alive with a
# short, neutral re-engagement instead of dead air or an unwanted goodbye.
_PHANTOM_GOODBYE_RECOVERY = "Sorry, I'm still here — what else can I help you with?"


def _note_unheard_greeting_bargein(session) -> None:
    """A barge-in cancelled a turn before ANY audio reached the caller (issue #23).

    On the opening turn that leaves ``_has_introduced`` False, so the next turn
    re-greets from the top — and a caller who keeps talking over the very start
    makes it loop the intro. Allow one clean re-attempt, then bound it: after a
    second unheard opening barge-in, mark the agent introduced so it picks up the
    conversation instead of restarting its greeting forever. No-op once introduced.
    """
    if getattr(session, "_has_introduced", False):
        return
    n = getattr(session, "_greeting_bargein_count", 0) + 1
    try:
        session._greeting_bargein_count = n
        if n >= 2:
            session._has_introduced = True
    except Exception:  # pragma: no cover - defensive
        pass


# The silence monitor speaks these; they are NOT read-backs and must be skipped
# when looking for the agent's real prior turn (else they mask the read-back).
_SILENCE_CHECK_RE = re.compile(
    r"\b(still\s+(there|with\s+me|on\s+the\s+line)|are\s+you\s+(still\s+)?there|"
    r"you\s+(still\s+)?there|can\s+you\s+hear\s+me|did\s+i\s+lose\s+you|lost\s+you|"
    r"you\s+on\s+the\s+line)\b",
    re.IGNORECASE,
)


def _is_email_correction(utterance, current_email) -> bool:
    """True if the caller restated a DIFFERENT email — a correction, which the
    capture path (not the confirmation path) handles. Used to skip the LLM
    confirmation call when the turn is actually a re-capture."""
    if not current_email:
        return False
    parsed = extract_email_from_speech(utterance)
    return bool(parsed and parsed != current_email)


# A turn that explicitly asks the caller to confirm ("did I get that right?").
# Required to promote a DOMAIN-ONLY match to a read-back (issue #4): naming the
# domain ("reach you at your gmail dot com address?") is NOT a read-back unless
# the turn also names the local part or asks for confirmation.
_CONFIRM_QUESTION_RE = re.compile(
    r"\b(did\s+i\s+(get|say|hear)\s+(that|it|this)|is\s+(that|this|it)\s+(right|correct)|"
    r"got\s+(that|it)\s+right|that\s+right\?|is\s+that\s+ok(ay)?\?|"
    r"sounds?\s+right|correct\?|right\?)",
    re.IGNORECASE,
)


def _email_local_signal(email: str) -> str:
    """The first alphabetic run (>=2 chars) of the local part — a distinctive
    signal that the agent actually SPOKE the local part, not just the domain."""
    local = email.split("@", 1)[0].lower()
    for run in re.findall(r"[a-z]+", local):
        if len(run) >= 2:
            return run
    return ""


def _is_phone_correction(utterance, current_phone) -> bool:
    """True if the caller restated a DIFFERENT phone number — a correction handled
    by the capture path, so we skip the confirmation classification for it."""
    if not current_phone:
        return False
    parsed = extract_phone_from_speech(utterance)
    return bool(parsed and parsed != current_phone)


def _email_from_recent_agent_readback(history):
    """Parse an ASSEMBLED email out of the agent's most recent REAL turn (gap #2).

    Only inspects the latest non-silence-check assistant turn — the read-back the
    caller is replying to right now — and returns the assembled address only when
    that turn is unmistakably a read-back-for-confirmation (see
    ``extract_email_from_agent_readback``). None otherwise.
    """
    for m in reversed(history or []):
        if getattr(m, "role", None) != MessageRole.ASSISTANT:
            continue
        c = m.content or ""
        if _SILENCE_CHECK_RE.search(c.lower()):
            continue
        return extract_email_from_agent_readback(c)
    return None


def _agent_read_back_email(history, email) -> bool:
    """True if the agent's most recent REAL turn read the pending email back — so
    the caller's current turn can safely be interpreted as a confirmation reply.

    Robust to how the address is actually spoken: the agent voices separators and
    digits as words ("j dot smith", "seven eight"), so a literal match of the
    glyph-laden read-back string fails for dotted/underscored/digit local parts.
    We therefore also match the domain-as-words (always spoken the same way,
    e.g. "gmail dot com") — but ONLY when the local part is ALSO signalled, or the
    turn asks for confirmation. A bare domain mention ("reach you at your gmail
    dot com address?") does NOT verify the LOCAL part, so it must not let a "yeah"
    confirm an unheard local (issue #4). Silence-check turns are skipped so an
    interposed "are you still there?" can't mask the read-back (re-audit flow #1).
    """
    if not email or "@" not in email:
        return False
    spoken = natural_email_readback(email).lower()
    domain_spoken = email.rsplit("@", 1)[-1].lower().replace(".", " dot ")  # "gmail dot com"
    local_sig = _email_local_signal(email)
    for m in reversed(history or []):
        if getattr(m, "role", None) != MessageRole.ASSISTANT:
            continue
        c = (m.content or "").lower()
        if _SILENCE_CHECK_RE.search(c):
            continue  # a silence-check is not a read-back — keep scanning back
        if (bool(spoken) and spoken in c) or (email.lower() in c):
            return True  # full spoken read-back or the literal address
        # Domain-only: require the local part be signalled too, OR a confirm question.
        if bool(domain_spoken) and domain_spoken in c:
            return (bool(local_sig) and local_sig in c) or bool(_CONFIRM_QUESTION_RE.search(c))
        return False
    return False


def _agent_read_back_phone(history, phone) -> bool:
    """True if the agent's most recent REAL turn read the pending phone number
    back. Matches on the digit string regardless of formatting (the agent may
    speak "555-123-4567", "5 5 5 …", or grouped), so a caller "yes" only counts
    once the digits were actually voiced. Silence checks are skipped."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return False
    spoken = natural_phone_readback(phone).lower()
    for m in reversed(history or []):
        if getattr(m, "role", None) != MessageRole.ASSISTANT:
            continue
        c = (m.content or "").lower()
        if _SILENCE_CHECK_RE.search(c):
            continue
        c_digits = re.sub(r"\D", "", c)
        return (digits in c_digits) or (bool(spoken) and spoken in c)
    return False


class TurnRunner:
    """Runs one user turn: append history → stream LLM+TTS → commit/rollback."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def run(
        self,
        session: CallSession,
        full_transcript: str,
        websocket: Optional[WebSocket] = None,
        turn_id: int = 0,
    ) -> tuple[str, float, float]:
        """
        Execute the LLM+TTS cycle for one user turn.

        Manages conversation history atomically:
        - User message is appended before LLM starts.
        - Rolled back on empty response, LLM error, or asyncio.CancelledError.
        - Assistant message is appended only when a non-empty response is produced.

        Returns (response_text, llm_latency_ms, tts_latency_ms).
        """
        call_id = session.call_id
        history_snapshot = len(session.conversation_history)
        session.conversation_history.append(
            Message(role=MessageRole.USER, content=full_transcript)
        )

        # This user turn is the one we may have relaxed STT for (e.g. they just
        # spelled an email). It has arrived, so revert to normal turn-detection.
        capture_mode.maybe_exit(getattr(self._p, "stt_provider", None), call_id)

        captured_slots = getattr(session, "captured_slots", None)
        if captured_slots is None or not is_dataclass(captured_slots):
            session.captured_slots = CapturedSlotsState()
        # Confirmation of a pending email only counts when the agent's last turn
        # actually read it back (see _agent_read_back_email). HYBRID classifier:
        # the fast deterministic regex resolves the clear cases with zero added
        # latency; only the ambiguous tail asks a small, tightly-bounded LLM —
        # fail-closed, so an unresolved verdict leaves the value pending.
        _pending = session.captured_slots
        _pending_email = getattr(_pending, "email", None)
        # Gap #2: a multi-word / carrier-prefixed spoken email never enters
        # CallState via the deterministic user-turn extractor (it refuses to guess
        # a word boundary), so the HARDEST emails bypassed the gate. When nothing
        # is pinned yet and this turn isn't itself a fresh email, seed the address
        # the AGENT assembled and read back in its prior turn as UNCONFIRMED — so
        # the SAME read-back → verdict → commit loop runs over it.
        if not _pending_email and extract_email_from_speech(full_transcript) is None:
            _seeded = _email_from_recent_agent_readback(session.conversation_history)
            if _seeded:
                _pending = replace(
                    _pending, email=_seeded, email_confirmed=False,
                    email_readback_attempts=0,
                )
                session.captured_slots = _pending
                _pending_email = _seeded

        _readback_issued = _agent_read_back_email(session.conversation_history, _pending_email)
        _confirm_verdict = None
        if (
            _pending_email
            and not getattr(_pending, "email_confirmed", False)
            and _readback_issued
            and not _is_email_correction(full_transcript, _pending_email)
        ):
            _confirm_verdict = _classify_core_confirmation(full_transcript)
            _via_llm = False
            if _confirm_verdict == "unclear":
                _via_llm = True
                _confirm_verdict = await llm_confirmation_verdict(
                    self._p.llm_provider, full_transcript, _pending_email
                )
            logger.info(
                "email_confirm call=%s via_llm=%s verdict=%s",
                call_id[:8], _via_llm, _confirm_verdict,
            )

        # Phone / callback number — SAME gate as email, resolved independently.
        _pending_phone = getattr(_pending, "phone", None)
        _phone_readback_issued = _agent_read_back_phone(
            session.conversation_history, _pending_phone
        )
        _phone_verdict = None
        if (
            _pending_phone
            and not getattr(_pending, "phone_confirmed", False)
            and _phone_readback_issued
            and not _is_phone_correction(full_transcript, _pending_phone)
        ):
            _phone_verdict = _classify_core_confirmation(full_transcript)
            if _phone_verdict == "unclear":
                _phone_verdict = await llm_confirmation_verdict(
                    self._p.llm_provider, full_transcript, _pending_phone,
                    subject="phone number",
                )
            logger.info("phone_confirm call=%s verdict=%s", call_id[:8], _phone_verdict)

        session.captured_slots = update_state_from_user_turn(
            _pending,
            full_transcript,
            readback_issued=_readback_issued,
            confirmation_verdict=_confirm_verdict,
            phone_readback_issued=_phone_readback_issued,
            phone_confirmation_verdict=_phone_verdict,
        )

        response_text = ""
        llm_latency_ms = 0.0
        tts_latency_ms = 0.0

        try:
            response_text, llm_latency_ms, tts_latency_ms = await self._p._stream_llm_and_tts(
                session, websocket
            )

            ask_ai_end_action = (
                parse_end_session_action(response_text)
                if self._p._supports_llm_end_session_action(session)
                else None
            )

            # Phantom-goodbye guard: the model emitted an end-session action but
            # the caller never actually signalled they were done. Suppress the
            # hangup and keep the call going with a short re-engagement line.
            if ask_ai_end_action:
                user_turns = sum(
                    1 for m in session.conversation_history if m.role == MessageRole.USER
                )
                # Two declines = the persona legitimately closes (issue #16), so
                # honor end-session rather than re-opening with the recovery line.
                _declined = getattr(getattr(session, "captured_slots", None), "declined_count", 0)
                # F-15 fix (2026-07-20): this JSON end-session path is the OTHER
                # hangup gate, and it never consulted the deterministic
                # disposition — so a model that chose the JSON format instead of
                # the [[END_CALL]] sentinel bypassed turn_ender's wrong-person
                # reverse gate entirely and could hang up on a valid prospect.
                # Mirror that gate here: on a WRONG_PERSON turn (right business,
                # wrong person → pivot) suppress the hangup unless the caller
                # explicitly said goodbye. do_not_call is EXEMPT — a genuine
                # opt-out always ends (and a DNC utterance classifies as DNC,
                # not WRONG_PERSON, so this can never swallow an opt-out).
                _wrong_person_block = (
                    not ask_ai_end_action.get("do_not_call")
                    and getattr(session, "_turn_disposition", IdentityDisposition.NONE)
                    == IdentityDisposition.WRONG_PERSON
                    and not contains_explicit_goodbye(full_transcript)
                )
                if _wrong_person_block or not should_honor_end_session(
                    ask_ai_end_action, full_transcript, user_turns, declined_count=_declined,
                ):
                    logger.info(
                        "phantom_goodbye_suppressed call_id=%s reason=%s user_turns=%d "
                        "wrong_person_block=%s transcript=%r — keeping call alive",
                        call_id, ask_ai_end_action.get("reason"), user_turns,
                        _wrong_person_block, (full_transcript or "")[:60],
                    )
                    session.tts_active = True
                    await self._p.synthesize_and_send_audio(
                        session, _PHANTOM_GOODBYE_RECOVERY, websocket, track_latency=False,
                    )
                    session.conversation_history.append(
                        Message(role=MessageRole.ASSISTANT, content=_PHANTOM_GOODBYE_RECOVERY)
                    )
                    # Persist the spoken recovery line too, so the transcript
                    # matches what the caller heard (re-audit flow #3).
                    try:
                        self._p.transcript_service.accumulate_turn(
                            call_id=call_id, role="assistant",
                            content=_PHANTOM_GOODBYE_RECOVERY,
                            talklee_call_id=session.talklee_call_id,
                            turn_index=session.turn_id,
                            event_type="assistant_response", is_final=True,
                            include_in_plaintext=True,
                        )
                    except Exception:  # pragma: no cover - defensive
                        pass
                    return _PHANTOM_GOODBYE_RECOVERY, llm_latency_ms, tts_latency_ms

            if ask_ai_end_action:
                # Compliance: caller asked never to be contacted again. Flag
                # the session so the call-end teardown runs the opt-out purge
                # (DNC + cancel scheduled jobs + mark lead DNC). We only set
                # the flag here; the side effects run once, at hangup.
                if ask_ai_end_action.get("do_not_call"):
                    try:
                        session._caller_opted_out = True
                    except Exception:
                        pass
                    logger.info(
                        "caller_opt_out_detected call_id=%s — will purge at hangup",
                        getattr(session, "call_id", "?"),
                    )
                await self._p._shutdown_session_for_end_action(
                    session,
                    websocket,
                    ask_ai_end_action["reason"],
                    ask_ai_end_action["farewell"],
                )
                return "", llm_latency_ms, tts_latency_ms

            if response_text and response_text.strip():
                session.conversation_history.append(
                    Message(role=MessageRole.ASSISTANT, content=response_text)
                )
                # The agent has now delivered a real reply (its opening on turn 1).
                # Flip the LIVE STATE flag so later turns are told NOT to
                # re-introduce (see prompts/live_state.py).
                session._has_introduced = True
                self._p.transcript_service.accumulate_turn(
                    call_id=call_id,
                    role="assistant",
                    content=response_text,
                    talklee_call_id=session.talklee_call_id,
                    turn_index=session.turn_id,
                    event_type="assistant_response",
                    is_final=True,
                    include_in_plaintext=True,
                )
                # If the agent just asked for an email / to spell something,
                # relax STT for the caller's upcoming spell-out turn.
                capture_mode.maybe_enter(
                    getattr(self._p, "stt_provider", None), call_id, response_text
                )
            else:
                logger.warning(
                    f"Empty LLM response for call {call_id} — rolling back user message"
                )
                session.conversation_history = session.conversation_history[:history_snapshot]

        except asyncio.CancelledError:
            # P3: a barge-in cancels the turn mid-reply. If the agent actually
            # spoke some sentences before the interrupt, KEEP the user turn and
            # commit ONLY what the caller heard (+ marker) so the model has
            # correct context. Discarding it (or committing the full unheard
            # reply) is what produced "absurd" replies after a few interrupts.
            spoken = " ".join(getattr(session, "_spoken_sentences", []) or []).strip()
            if spoken:
                # Keep the user message (at history_snapshot), drop anything the
                # cancelled task appended after it, then add the spoken partial.
                session.conversation_history = session.conversation_history[:history_snapshot + 1]
                session.conversation_history.append(
                    Message(role=MessageRole.ASSISTANT, content=spoken + " [interrupted by caller]")
                )
                # The agent spoke a partial reply (possibly its opening), so it
                # counts as introduced — don't make it re-introduce next turn.
                session._has_introduced = True
                # Tell handle_barge_in we already committed the correct partial,
                # so it does NOT roll it back or double-annotate.
                session._speculative_history_len = None
            else:
                session.conversation_history = session.conversation_history[:history_snapshot]
                # Nothing was heard. If this keeps happening on the opening, stop
                # looping the intro (issue #23).
                _note_unheard_greeting_bargein(session)
            raise
        except Exception as e:
            logger.error(f"Turn error for call {call_id}: {e}", exc_info=True)
            session.conversation_history = session.conversation_history[:history_snapshot]

        return response_text, llm_latency_ms, tts_latency_ms
