"""Transcript dispatch: classify each STT transcript and route it
(barge-in, TurnResumed cancel, eager/speculative LLM start, final turn-end,
or accumulate) — the entry point the STT loop calls per transcript.

Extracted from VoicePipelineService.handle_transcript (item 2, slice 7).
Same collaborator pattern: holds the pipeline, reads its deps
(handle_barge_in / handle_turn_end / stt_provider / transcript_service /
latency_tracker / _pending_llm_tasks / _await_task_after_cancel) at call
time. The service keeps handle_transcript() as a thin delegator (audio_ingest
calls it).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import WebSocket

from app.domain.models.conversation import BargeInSignal
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.backchannel import is_backchannel

logger = logging.getLogger(__name__)


class TranscriptHandler:
    """Routes each STT transcript to the right pipeline action."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def handle(
        self,
        session: CallSession,
        transcript,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id

        if isinstance(transcript, BargeInSignal):
            await self._p.handle_barge_in(session, websocket)
            return

        if transcript.metadata and transcript.metadata.get("resumed"):
            # TurnResumed cancels SPECULATIVE work only. If a confirmed final
            # answer is already in flight (a speculative task promoted on
            # EndOfTurn), it must NOT be killed — doing so leaves the caller in
            # silence. (Per Deepgram: TurnResumed targets the in-progress
            # speculative response, not a committed turn.)
            existing = self._p._pending_llm_tasks.get(call_id)
            if existing is not None and getattr(existing, "_turn_type", "speculative") == "final":
                logger.info(
                    "TurnResumed ignored — final answer in flight for %s", call_id[:12]
                )
                return
            logger.info(f"TurnResumed for call {call_id} — cancelling speculative LLM")
            session.llm_active = False
            if call_id in self._p._pending_llm_tasks:
                task = self._p._pending_llm_tasks.pop(call_id)
                if not task.done():
                    task.cancel()
                await self._p._await_task_after_cancel(task, call_id, "speculative_llm")
            # Roll back any messages the speculative handle_turn_end appended
            # before being cancelled.  Without this, orphaned user/assistant
            # messages corrupt the conversation context for subsequent turns.
            restore_len = getattr(session, "_speculative_history_len", None)
            if restore_len is not None and len(session.conversation_history) > restore_len:
                session.conversation_history = session.conversation_history[:restore_len]
            session._speculative_history_len = None
            return

        metadata = transcript.metadata or {}
        self._p.transcript_service.bind_call_identity(call_id, session.talklee_call_id)

        # Ensure latency tracker is aligned with current turn ID.
        # Guard: do NOT reset tracker while LLM/TTS is actively processing.
        # session.turn_id is pre-incremented before _run_turn is created, so a
        # tracker turn_id mismatch during active processing is expected — it is
        # NOT an indication the tracker is stale.
        current_metrics = self._p.latency_tracker.get_metrics(call_id)
        if (not current_metrics or current_metrics.turn_id != session.turn_id) and not session.llm_active:
            self._p.latency_tracker.start_turn(call_id, session.turn_id)
            self._p.latency_tracker.mark_listening_start(call_id)

        logger.info(
            "transcript_received",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "timestamp": datetime.utcnow().isoformat(),
                "text": transcript.text,
                "is_final": transcript.is_final,
                "confidence": transcript.confidence,
                "eager": metadata.get("eager", False),
            },
        )

        if websocket and transcript.text:
            try:
                msg_type = "transcript_eager" if metadata.get("eager") else "transcript"
                await websocket.send_json({
                    "type": msg_type,
                    "text": transcript.text,
                    "is_final": transcript.is_final,
                    "confidence": transcript.confidence,
                })
            except Exception as e:
                logger.warning(f"Failed to send transcript to websocket: {e}")

        # Backchannel guard (Deepgram leaves this to the app). If the agent is
        # currently speaking and the user only made a short acknowledgement
        # ("yeah", "ok", "mhm"), that's a backchannel — NOT a turn. Don't reply
        # and don't end the agent's turn; it keeps going, like a human would.
        # (StartOfTurn already suppressed the audio barge-in for this; this stops
        # the spurious reply.) Only while the agent is talking — a "yeah" when
        # it's the caller's turn is a real answer and falls through normally.
        if getattr(session, "tts_active", False) and is_backchannel(transcript.text):
            logger.info(
                "backchannel %r during agent speech — ignored (no interrupt, no reply)",
                (transcript.text or "")[:24],
            )
            return

        if self._p.stt_provider.detect_turn_end(transcript):
            # Grow case: a turn that began as a backchannel (so the StartOfTurn
            # audio barge-in was suppressed) but turned into real speech. The
            # agent's TTS may still be playing — stop it now before we respond.
            # No-op if it already stopped (normal interruptions clear it).
            if getattr(session, "tts_active", False):
                await self._p.handle_barge_in(session, websocket)

            # Run as a task (not awaited) so the consumer stays unblocked and
            # can process a TurnResumed that arrives before the LLM completes.
            #
            # Why this matters: Deepgram's barge-in state machine occasionally
            # sends EndOfTurn → TurnResumed in that order (e.g. user pauses
            # mid-phrase → EndOfTurn fires → user continues → TurnResumed).
            # With the old `await handle_turn_end(...)` pattern the consumer
            # was blocked for the full LLM+TTS duration (~2-10s) — TurnResumed
            # sat in the queue and arrived too late to cancel the LLM call.
            # Result: AI responded to a partial/stale transcript ("But") while
            # the user's real question ("But what is your offering?") was split
            # across two EndOfTurns, producing a totally off-topic answer.
            existing = self._p._pending_llm_tasks.get(call_id)
            if existing and not existing.done():
                # A task is already in flight for this call. Two cases:
                #  * speculative (started on EagerEndOfTurn) — Deepgram
                #    guarantees this EndOfTurn's transcript matches that
                #    eager turn, so the in-flight task IS the answer. PROMOTE
                #    it to "final" (protects it from a later StartOfTurn
                #    cancelling it) and let it finish — restarting would throw
                #    away the eager-EOT latency head start.
                #  * final — a duplicate EndOfTurn; the promotion is a no-op
                #    and we simply skip the duplicate.
                # Either way we must NOT drop the turn into silence.
                existing._turn_type = "final"
                logger.debug(
                    "turn_end: existing task kept as final for %s", call_id[:12]
                )
                return
            session._speculative_history_len = len(session.conversation_history)
            task = asyncio.create_task(
                self._p.handle_turn_end(session, websocket, source="final")
            )
            # Tag: a FINAL task answers a confirmed, completed utterance. It is
            # protected — a bare StartOfTurn must never cancel it (only an
            # interruption of audio that is actually playing may).
            task._turn_type = "final"
            self._p._pending_llm_tasks[call_id] = task
            return

        if metadata.get("eager") and transcript.text:
            if not session.llm_active and call_id not in self._p._pending_llm_tasks:
                session.current_user_input = transcript.text
                # Stash the transcript's confidence alongside the text so
                # handle_turn_end can apply a turn-0 floor on garbled
                # mishears without re-acquiring the transcript object.
                session._last_transcript_confidence = transcript.confidence
                # Snapshot history length so TurnResumed can roll back any
                # messages the speculative task appends before cancellation.
                session._speculative_history_len = len(session.conversation_history)
                # Speculatively start LLM now (EagerEndOfTurn fired — 150–250ms before
                # EndOfTurn). If user keeps talking, TurnResumed cancels this task via
                # the handle_transcript "resumed" branch above (session.llm_active=False
                # + task.cancel()).
                task = asyncio.create_task(
                    self._p.handle_turn_end(session, websocket, source="speculative")
                )
                # Tag: a SPECULATIVE task is tentative (the user may keep
                # talking). It is cancellable by TurnResumed or a barge-in.
                task._turn_type = "speculative"
                self._p._pending_llm_tasks[call_id] = task
            return

        if transcript.text:
            event_type = "eager_end_of_turn" if metadata.get("eager") else "update"
            if transcript.is_final:
                event_type = "end_of_turn"

            self._p.transcript_service.accumulate_turn(
                call_id=call_id,
                role="user",
                content=transcript.text,
                confidence=transcript.confidence,
                talklee_call_id=session.talklee_call_id,
                turn_index=session.turn_id,
                event_type=event_type,
                is_final=transcript.is_final,
                audio_window_start=metadata.get("audio_window_start"),
                audio_window_end=metadata.get("audio_window_end"),
                include_in_plaintext=transcript.is_final,
                metadata=metadata,
            )
            self._p.latency_tracker.mark_stt_first_transcript(call_id)
            session.current_user_input = transcript.text
            session._last_transcript_confidence = transcript.confidence
            session.update_activity()

