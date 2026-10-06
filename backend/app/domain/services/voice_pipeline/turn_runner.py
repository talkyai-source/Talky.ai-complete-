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
from dataclasses import replace
from typing import Optional

from fastapi import WebSocket

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.end_session_action import (
    parse_end_session_action,
    should_honor_end_session,
    previous_assistant_turn,
)
from app.services.scripts.call_state_tracker import CallState as CapturedSlotsState
from app.domain.services.voice_pipeline.contact_recording import bind_contact_turn
from app.domain.services.voice_pipeline.identity_disposition import (
    IdentityDisposition,
    contains_dnc,
    contains_explicit_goodbye,
)

logger = logging.getLogger(__name__)

# Last resort when a phantom end-session is suppressed AND the model left the
# turn with nothing to speak: it tried to hang up, the caller never signalled
# they were done, and it emitted only the internal envelope. Keeps the call
# alive instead of leaving dead air or an unwanted goodbye.
#
# A TUPLE, not one string, because the guard can fire repeatedly on one call and
# the same canned sentence twice running is what a caller hears as the agent
# looping. Indexed by how often it has already fired, so it stays deterministic.
_PHANTOM_GOODBYE_RECOVERIES = (
    "Sorry, I'm still here — what else can I help you with?",
    "I'm still on the line. What else can I do for you?",
    "Still with you — was there anything else?",
)
# The canonical first line, kept under its old name for callers and log reading.
_PHANTOM_GOODBYE_RECOVERY = _PHANTOM_GOODBYE_RECOVERIES[0]

# Appended for ONE retry when a suppressed end-session left nothing to say. The
# canned line above answers no question the caller actually asked, so before
# falling back to it we tell the model the call is continuing and let it reply
# properly. Removed again immediately: it is scaffolding for this turn only.
_PHANTOM_RETRY_INSTRUCTION = (
    "SYSTEM: Your previous reply tried to end the call, but the caller has not "
    "finished and the call is continuing. Do not end the session and do not "
    "emit any JSON. Answer the caller's last message now, in one or two short "
    "spoken sentences."
)


def _spoken_remainder(response_text) -> str:
    """The part of a turn the caller actually HEARD.

    The streamer speaks prose up to the internal action envelope and swallows
    everything from that opening brace on (see
    ``turn_streamer._find_action_envelope_start``), so an EMPTY remainder means
    the model emitted only the envelope and the turn was silent. What to do
    about a suppressed hangup depends entirely on which of those two happened.
    """
    from app.domain.services.voice_pipeline.turn_streamer import (
        _find_action_envelope_start,
    )

    text = str(response_text or "")
    idx = _find_action_envelope_start(text)
    return (text if idx < 0 else text[:idx]).strip()


def _drop_last_message(history, content) -> None:
    """Remove the most recent message whose content is exactly ``content``."""
    for i in range(len(history) - 1, -1, -1):
        if getattr(history[i], "content", None) == content:
            del history[i]
            return


def _note_unheard_greeting_bargein(session) -> None:
    """A barge-in cancelled a turn before ANY audio reached the caller (issue #23).

    Keep interruption separate from delivery. The live prompt uses this count
    to follow the caller's latest words without restarting the opening; an
    unheard introduction must never become delivered identity evidence.
    """
    if getattr(session, "_has_introduced", False):
        return
    n = getattr(session, "_greeting_bargein_count", 0) + 1
    try:
        session._greeting_bargein_count = n
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


def _last_agent_turn(history) -> str:
    """The most recent assistant line, interstitials included. Empty if none."""
    for m in reversed(history or []):
        if getattr(m, "role", None) == MessageRole.ASSISTANT:
            return str(getattr(m, "content", "") or "")
    return ""


def _same_utterance(a, b) -> bool:
    """True if two agent lines are the same sentence bar spacing, case and
    trailing punctuation -- i.e. the caller heard the identical thing twice."""
    def norm(t):
        t = re.sub(r"[^a-z0-9 ]+", " ", str(t or "").lower())
        return " ".join(t.split())

    na, nb = norm(a), norm(b)
    return bool(na) and na == nb


async def known_line_number(session) -> Optional[str]:
    """The E.164 number this call is on, or None. One small read per call.

    Outbound: the number that was dialled. Inbound: caller ID. Extensions,
    withheld numbers and anything that is not a real E.164 number give None,
    so the agent simply asks for a number as before.
    """
    try:
        from app.core.container import get_container
        from app.domain.services.phone_number_normalizer import normalize_phone_for_capture
        from app.domain.services.voice_pipeline.lead_slot_capture import resolve_call_binding

        binding = resolve_call_binding(session)
        call_id, tenant_id = binding.get("call_id"), binding.get("tenant_id")
        container = get_container()
        if not (call_id and tenant_id and getattr(container, "is_initialized", False)):
            return None
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(container.db_pool, str(tenant_id), timeout=0.5) as conn:
            row = await asyncio.wait_for(
                conn.fetchrow(
                    "SELECT direction, phone_number, caller_ani FROM calls "
                    "WHERE id = $1::uuid AND tenant_id = $2::uuid",
                    str(call_id), str(tenant_id),
                ),
                timeout=0.5,
            )
        if not row:
            return None
        raw = row["caller_ani"] if row["direction"] == "inbound" else row["phone_number"]
        raw = str(raw or "").strip()
        if not raw.startswith("+"):
            return None
        return normalize_phone_for_capture(raw, region=None)
    except Exception as exc:  # noqa: BLE001 - never block a turn on this
        logger.debug("known_line_number unavailable: %s", exc)
        return None


class TurnRunner:
    """Runs one user turn: append history → stream LLM+TTS → commit/rollback."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def _recover_suppressed_turn(
        self,
        session: CallSession,
        websocket: Optional[WebSocket],
        response_text: str,
    ) -> str:
        """Return what the caller should end up having heard on a turn whose
        end-session action was suppressed.

        Three cases, in order of preference:

        1. The model wrote prose AND the envelope. The prose was already
           streamed to the caller, so the turn is complete — only the hangup
           needed suppressing. Speaking a canned line on top would talk over a
           finished answer with a non-sequitur.
        2. The model emitted ONLY the envelope, so the caller heard nothing at
           all. Ask once more, telling it the call is continuing. This is the
           case that cost a live caller their answer on 2026-09-21: the guard
           replaced the whole turn with a fixed sentence, so the question went
           unanswered and the correction it contained was lost.
        3. The retry also produced nothing. Fall back to the canned line, which
           at least keeps the call alive — varied per firing so a repeat guard
           does not read the identical sentence twice.
        """
        call_id = session.call_id

        spoken = _spoken_remainder(response_text)
        if spoken:
            logger.info(
                "phantom_goodbye_kept_prose call_id=%s chars=%d — answer already spoken",
                call_id, len(spoken),
            )
            return spoken

        retry_text = ""
        try:
            session.conversation_history.append(
                Message(role=MessageRole.SYSTEM, content=_PHANTOM_RETRY_INSTRUCTION)
            )
            try:
                retry_text, _, _ = await self._p._stream_llm_and_tts(session, websocket)
            finally:
                # Drop the nudge whether or not it worked — it is scaffolding
                # for this turn, not conversation the model should keep seeing.
                _drop_last_message(
                    session.conversation_history, _PHANTOM_RETRY_INSTRUCTION
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "phantom_goodbye_retry_failed call_id=%s", call_id, exc_info=True
            )

        retry_spoken = _spoken_remainder(retry_text)
        if retry_spoken:
            logger.info(
                "phantom_goodbye_retry_spoke call_id=%s chars=%d", call_id, len(retry_spoken)
            )
            return retry_spoken

        fired = int(getattr(session, "_phantom_recovery_count", 0) or 0)
        line = _PHANTOM_GOODBYE_RECOVERIES[
            min(fired, len(_PHANTOM_GOODBYE_RECOVERIES) - 1)
        ]
        try:
            session._phantom_recovery_count = fired + 1
        except Exception:  # pragma: no cover - defensive
            pass
        logger.info(
            "phantom_goodbye_recovery_line call_id=%s fired=%d — retry produced nothing",
            call_id, fired + 1,
        )
        session.tts_active = True
        await self._p.synthesize_and_send_audio(
            session, line, websocket, track_latency=False,
        )
        return line

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
        # The finisher also handles plain goodbye replies. Record when this
        # runner owns the close so the same turn cannot shut down twice.
        session._end_session_action_handled = False
        history_snapshot = len(session.conversation_history)
        session.conversation_history.append(
            Message(role=MessageRole.USER, content=full_transcript)
        )

        # The model interprets contact language through record_contact. Bind the
        # actual caller source before generation; a turn counter alone is no proof.
        if not isinstance(getattr(session, "captured_slots", None), CapturedSlotsState):
            session.captured_slots = CapturedSlotsState()
        from app.domain.services.voice_pipeline.contact_capture import ContactSource
        order = getattr(asyncio.current_task(), "_caller_turn_order", None)
        source, source_text = None, ""
        if isinstance(order, int) and not isinstance(order, bool):
            resolver = getattr(self._p.transcript_service, "caller_evidence", None)
            evidence = resolver(call_id, order) if callable(resolver) else None
            if isinstance(evidence, dict):
                try:
                    source = ContactSource(**evidence["source"])
                    source_text = evidence["text"]
                except (KeyError, TypeError, ValueError):
                    pass
        # Echo cleanup changes the model's message, not the saved caller row.
        # Bind the canonical bundle so its source hash still verifies exactly.
        bind_contact_turn(session, source_text, source)
        if not session.captured_slots.line_phone and not getattr(session, "_line_phone_checked", False):
            session._line_phone_checked = True
            line = await known_line_number(session)
            if line:
                session.captured_slots = replace(session.captured_slots, line_phone=line)

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
            if ask_ai_end_action and ask_ai_end_action.get("do_not_call") and not contains_dnc(full_transcript):
                ask_ai_end_action = {**ask_ai_end_action, "do_not_call": False}

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
                    previous_assistant_text=previous_assistant_turn(session.conversation_history),
                ):
                    logger.info(
                        "phantom_goodbye_suppressed call_id=%s reason=%s user_turns=%d "
                        "wrong_person_block=%s transcript_chars=%d — keeping call alive",
                        call_id, ask_ai_end_action.get("reason"), user_turns,
                        _wrong_person_block, len(full_transcript or ""),
                    )
                    # From here this is an ORDINARY turn. Dropping the action
                    # skips the shutdown path below and lets the shared
                    # post-turn block run, which is the point: the old early
                    # return hand-rolled its own history append and transcript
                    # write and therefore skipped update_state_from_agent_turn,
                    # _has_introduced. A suppressed
                    # turn that asked for an email never relaxed endpointing, so
                    # the caller's spell-out was cut off mid-address.
                    ask_ai_end_action = None
                    response_text = await self._recover_suppressed_turn(
                        session, websocket, response_text
                    )

            if ask_ai_end_action:
                # Compliance: caller asked never to be contacted again. Flag
                # the session so the call-end teardown runs the opt-out purge
                # (DNC + cancel scheduled jobs + mark lead DNC). We only set
                # the flag here; the side effects run once, at hangup.
                if ask_ai_end_action.get("do_not_call") and contains_dnc(full_transcript):
                    try:
                        session._caller_opted_out = True
                    except Exception:
                        pass
                    logger.info(
                        "caller_opt_out_detected call_id=%s — will purge at hangup",
                        getattr(session, "call_id", "?"),
                    )
                session._end_session_action_handled = True
                await self._p._shutdown_session_for_end_action(
                    session,
                    websocket,
                    ask_ai_end_action["reason"],
                    ask_ai_end_action["farewell"],
                )
                return "", llm_latency_ms, tts_latency_ms

            if response_text and response_text.strip():
                # The caller hearing the identical sentence twice running is the
                # single most obvious way the agent sounds broken. It cannot be
                # unspoken here -- the streamer already sent it -- so record it
                # where it can be counted per call rather than only heard.
                if _same_utterance(
                    _last_agent_turn(session.conversation_history), response_text
                ):
                    logger.warning(
                        "agent_repeated_turn call_id=%s turn=%s chars=%d — "
                        "identical to the previous agent line",
                        call_id, getattr(session, "turn_id", "?"), len(response_text),
                    )
                session.conversation_history.append(
                    Message(role=MessageRole.ASSISTANT, content=response_text)
                )
                # The agent has now delivered a real reply — since 2026-08-11
                # that is the turn AFTER the bare pickup greeting, not turn 1
                # (turn 1 is TTS-only and runs no LLM call at all). Flip the
                # LIVE STATE flag so later turns are told NOT to re-introduce
                # (see prompts/live_state.py). Idempotent: harmless to re-set.
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
                # KEEP the user message (at history_snapshot) — dropping it
                # discarded the caller's own words permanently. Each Flux
                # EndOfTurn dispatches only its own segment (turn_ender.py
                # builds full_transcript fresh per turn from just that
                # segment), so nothing downstream ever re-queues or merges a
                # rolled-back fragment into a later turn: once dropped here it
                # was gone for the rest of the call. On a caller who speaks in
                # fragments this produced half-heard TTS AND an agent that
                # answers as though the caller never spoke (51450718: "I want
                # full body checkup" never engaged with, "which day" asked 3x;
                # b97ce4c5: 8 replies cancelled, agent later said "I'm sorry I
                # missed that" to a caller who explained their problem twice).
                # Only discard what THIS cancelled task appended after the
                # user's own message.
                session.conversation_history = session.conversation_history[:history_snapshot + 1]
                # Nothing was heard. If this keeps happening on the opening, stop
                # looping the intro (issue #23).
                _note_unheard_greeting_bargein(session)
            raise
        except Exception as e:
            logger.error(f"Turn error for call {call_id}: {e}", exc_info=True)
            session.conversation_history = session.conversation_history[:history_snapshot]

        return response_text, llm_latency_ms, tts_latency_ms
