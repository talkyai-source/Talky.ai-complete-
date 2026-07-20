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
import time
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
                # Bounded, non-blocking cancel (see _cancel_turn_task) so the
                # consumer keeps draining events instead of freezing here.
                await self._p._cancel_turn_task(task, call_id, "speculative_llm")
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
            # F-09: mark this utterance's seq as suppressed so the ALWAYS-emitted
            # empty is_final marker that follows it (Deepgram sends one after
            # every EndOfTurn) doesn't get read as a genuine EndOfTurn below and
            # barge in on the agent's TTS for a mere "yeah".
            session._suppressed_backchannel_seq = self._p._utterance_seq.get(call_id, 0)
            return

        # Grow case: an earlier fragment of THIS SAME utterance (same seq) was
        # suppressed above as a backchannel, but this chunk is no longer a
        # backchannel — the utterance grew into real content (e.g. "yeah" ->
        # "yeah, but what does it cost?"). Clear the mark so the empty EOT
        # marker below is treated as a genuine end-of-turn, not swallowed —
        # otherwise the grown utterance's only detect_turn_end=True chunk (the
        # empty marker) would be swallowed and the turn would NEVER run.
        # Gated on transcript.text (non-empty): the empty EOT marker itself
        # must NOT trip this clear, or it would erase the mark right before
        # the empty-marker suppression check below ever gets to see it.
        if transcript.text and getattr(session, "_suppressed_backchannel_seq", None) == self._p._utterance_seq.get(call_id, 0):
            session._suppressed_backchannel_seq = None

        if not transcript.text:
            _sup_seq = getattr(session, "_suppressed_backchannel_seq", None)
            if _sup_seq is not None and _sup_seq == self._p._utterance_seq.get(call_id, 0):
                session._suppressed_backchannel_seq = None  # one-shot consume
                logger.debug(
                    "empty_eot_marker_suppressed_for_backchannel call=%s seq=%s",
                    call_id[:12], _sup_seq,
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
                # A task is already in flight for this call. Determine whether
                # THIS EndOfTurn is the same utterance that launched it (a
                # genuine duplicate / eager-promotion case) or a genuinely
                # DIFFERENT, later utterance that finished while turn 1 is
                # still thinking (F-08).
                #
                # seq: bumped on every StartOfTurn that reaches the pipeline
                # (_on_barge_in_direct). A new distinct utterance normally
                # bumps it, so a seq mismatch is strong evidence of a new
                # utterance.
                #
                # content fallback: Flux's own StartOfTurn gate can suppress
                # the barge-in callback (so no seq bump) for an utterance that
                # STARTS like a backchannel but grows into content —
                # session.current_user_input still updates via Update chunks,
                # so a text mismatch catches that case too. A true Flux
                # EndOfTurn->TurnResumed->EndOfTurn split of ONE utterance has
                # both seq AND text unchanged, so it still collapses as a
                # duplicate — zero behavior change for that case.
                _current_seq = self._p._utterance_seq.get(call_id, 0)
                _existing_seq = getattr(existing, "_utterance_seq", _current_seq)
                _existing_text = (getattr(existing, "_source_text", None) or "").strip()
                _new_text = (session.current_user_input or "").strip()
                _is_distinct = (_current_seq != _existing_seq) or (
                    _new_text and _new_text != _existing_text
                )
                if not _is_distinct:
                    # Same utterance: two cases —
                    #  * speculative (started on EagerEndOfTurn) — Deepgram
                    #    guarantees this EndOfTurn's transcript matches that
                    #    eager turn, so the in-flight task IS the answer.
                    #    PROMOTE it to "final" (protects it from a later
                    #    StartOfTurn cancelling it) and let it finish —
                    #    restarting would throw away the eager-EOT latency
                    #    head start.
                    #  * final — a duplicate EndOfTurn; the promotion is a
                    #    no-op and we simply skip the duplicate.
                    # Either way we must NOT drop the turn into silence.
                    existing._turn_type = "final"
                    logger.debug(
                        "turn_end: existing task kept as final for %s", call_id[:12]
                    )
                    return
                # F-08: a genuinely different utterance finished while turn 1
                # is still running (LLM in flight / thinking). Don't drop it —
                # queue it depth-1 so turn_ender dispatches it the instant
                # turn 1 releases the turn slot. A 3rd distinct utterance
                # simply overwrites the queued slot (coalesces onto the
                # latest, matching how a live caller would expect their most
                # recent words to be the ones answered).
                session._queued_next_turn = {
                    "text": session.current_user_input,
                    "seq": _current_seq,
                    "queued_monotonic": time.monotonic(),
                }
                logger.info(
                    "turn_queued_behind_pending call=%s seq=%d",
                    call_id[:12], _current_seq,
                )
                return
            session._speculative_history_len = len(session.conversation_history)
            # Capture the transcript NOW and carry it into the turn. A barge-in
            # can reset session.current_user_input to "" before this task reads
            # it, which would strand the turn ("Empty transcript, skipping") and
            # drop the caller's words — the dropped-turn half of the silence bug.
            _user_text = session.current_user_input
            task = asyncio.create_task(
                self._p.handle_turn_end(
                    session, websocket, source="final", user_text=_user_text
                )
            )
            # Tag: a FINAL task answers a confirmed, completed utterance. It is
            # protected — a bare StartOfTurn must never cancel it (only an
            # interruption of audio that is actually playing may).
            task._turn_type = "final"
            # F-08: tag with the utterance identity that launched this task so
            # a LATER EndOfTurn arriving while this one is still running can
            # tell a genuine duplicate from a genuinely new utterance (see the
            # branch above).
            task._utterance_seq = self._p._utterance_seq.get(call_id, 0)
            task._source_text = _user_text
            self._p._pending_llm_tasks[call_id] = task
            return

        if metadata.get("eager") and transcript.text:
            # CONTAINMENT (safety): do NOT launch a speculative turn on eager.
            #
            # An EagerEndOfTurn is RETRACTABLE — Deepgram fires TurnResumed if the
            # caller keeps talking — but the turn runner commits IRREVERSIBLE
            # effects mid-run: captured_slots (CORE email / phone / yes-no) at
            # turn_runner.py:294, and agent END_CALL / hangup. The TurnResumed
            # cancel path rolls back conversation_history but NOT captured_slots,
            # so a retracted speculative turn could PERMANENTLY commit a partial
            # CORE field or hang the call up. Speculation is therefore unsafe for
            # any irreversible action.
            #
            # We trade the ~150–250ms eager head start for correctness: only the
            # CONFIRMED EndOfTurn path above (detect_turn_end) starts the real
            # turn, exactly once. We still stash the transcript + confidence so
            # that confirmed final path — which reads session.current_user_input
            # and _last_transcript_confidence — has the caller's words even when
            # they only ever arrived via eager events (no plain interim).
            if not session.llm_active and call_id not in self._p._pending_llm_tasks:
                session.current_user_input = transcript.text
                # Confidence feeds handle_turn_end's turn-0 garbled-mishear floor;
                # store it ONLY from a final recognition so an interim's low value
                # can't spuriously trip the gate (Case 2). Text is still stashed
                # unconditionally so the eager-only turn keeps the caller's words.
                if getattr(transcript, "is_final", True):
                    session._last_transcript_confidence = transcript.confidence
            return

        # Real-time machine detection on EVERY transcript, interims included.
        # A recorded greeting is continuous speech, so EndOfTurn never fires
        # and a final-only check sits deaf for the whole message (audited
        # 2026-07-08: 45-134s burned per voicemail). Handles opening
        # voicemail, call-screening services, and the post-screening
        # "not available / after the tone" endgame. Fail-soft; returns True
        # only when the call is being hung up.
        if transcript.text:
            from app.domain.services.voice_pipeline.machine_detection import (
                handle_machine_interim,
            )
            if await handle_machine_interim(
                call_id, session, transcript.text,
                media_gateway=getattr(self._p, "media_gateway", None),
            ):
                return

        # Final-transcript voicemail detection kept as the belt-and-braces
        # fallback (some machines DO pause long enough to finalize a turn).
        if transcript.text and transcript.is_final and session.turn_id <= 1:
            from app.domain.services.voice_pipeline.voicemail_detector import (
                detect_and_hang_up_voicemail,
            )
            if await detect_and_hang_up_voicemail(
                call_id, transcript.text, session.turn_id
            ):
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
            # Store confidence ONLY from a FINAL recognition (Case 2): the turn-0
            # floor treats this value as the utterance's recognition confidence,
            # but interim chunks carry provisional/low values that don't mean
            # that. Writing an interim here let a mid-utterance dip drop a good
            # first turn. Final-only makes the stored value match the gate's
            # assumption. (On Flux confidence is always None regardless.)
            if getattr(transcript, "is_final", True):
                session._last_transcript_confidence = transcript.confidence
            session.update_activity()

