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
from dataclasses import is_dataclass
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

logger = logging.getLogger(__name__)

# Spoken when a phantom end-session is suppressed: the model tried to hang up
# but the caller never signalled they were done. Keeps the call alive with a
# short, neutral re-engagement instead of dead air or an unwanted goodbye.
_PHANTOM_GOODBYE_RECOVERY = "Sorry, I'm still here — what else can I help you with?"


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
        session.captured_slots = update_state_from_user_turn(
            session.captured_slots, full_transcript
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
                if not should_honor_end_session(ask_ai_end_action, full_transcript, user_turns):
                    logger.info(
                        "phantom_goodbye_suppressed call_id=%s reason=%s user_turns=%d "
                        "transcript=%r — keeping call alive",
                        call_id, ask_ai_end_action.get("reason"), user_turns,
                        (full_transcript or "")[:60],
                    )
                    session.tts_active = True
                    await self._p.synthesize_and_send_audio(
                        session, _PHANTOM_GOODBYE_RECOVERY, websocket, track_latency=False,
                    )
                    session.conversation_history.append(
                        Message(role=MessageRole.ASSISTANT, content=_PHANTOM_GOODBYE_RECOVERY)
                    )
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
            session.conversation_history = session.conversation_history[:history_snapshot]
            raise
        except Exception as e:
            logger.error(f"Turn error for call {call_id}: {e}", exc_info=True)
            session.conversation_history = session.conversation_history[:history_snapshot]

        return response_text, llm_latency_ms, tts_latency_ms
