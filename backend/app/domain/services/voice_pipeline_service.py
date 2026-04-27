"""
Voice Pipeline Service
Orchestrates the full voice AI pipeline: STT → LLM → TTS

Now instrumented with OpenTelemetry distributed tracing.
Every turn produces a parent span covering the full STT→LLM→TTS cycle,
with child spans per stage and latency attributes on each.
"""
import asyncio
import logging
import os
import time
from typing import Optional, AsyncIterator
from datetime import datetime

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import AudioChunk, TranscriptChunk, Message, MessageRole, BargeInSignal
from app.domain.models.conversation_state import ConversationState, CallOutcomeType
from app.domain.interfaces.stt_provider import STTProvider
from app.infrastructure.llm.groq import GroqLLMProvider, LLMTimeoutError
from app.infrastructure.telephony.browser_media_gateway import SessionGoneError
from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.interfaces.media_gateway import MediaGateway
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.services.prompt_manager import PromptManager
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.llm_guardrails import LLMGuardrails, LLMGuardrailsConfig, get_guardrails
from app.services.scripts.interruption_filter import is_backchannel as _is_backchannel
from app.domain.services.latency_tracker import get_latency_tracker
from app.domain.services.global_ai_config import get_global_config
from app.domain.services.ask_ai_constants import TALKY_PRODUCT_INFO as _ASK_AI_PRODUCT_INFO, PRODUCT_KEYWORDS as _ASK_AI_PRODUCT_KEYWORDS
from app.core.container import get_container
from app.core.postgres_adapter import Client as PostgresAdapterClient
from app.core.telemetry import get_tracer, pipeline_span, record_latency, voice_span
from app.core.telephony_observability import record_turn_silent_reason
from app.services.scripts import (
    CallState as CapturedSlotsState,
    compose_system_prompt,
    update_state_from_user_turn,
)

logger = logging.getLogger(__name__)

# FIX 2 — Sliding-window history truncation to prevent context-limit crashes.
# llama-3.1-8b-instant has an 8,192-token context window.  At ~125 tokens/turn,
# overflow hits at ~55 turns.  Groq returns HTTP 400 and the apology TTS plays —
# but without truncation the NEXT turn hits 400 again → infinite apology loop.
# 20 pairs ≈ 2,500 tokens worst-case, leaving ample room for system prompt + reply.
_MAX_HISTORY_PAIRS = int(os.getenv("VOICE_MAX_HISTORY_PAIRS", "20"))


def _truncate_history(history: list, max_pairs: int = _MAX_HISTORY_PAIRS) -> list:
    """Return the last max_pairs user/assistant pairs from conversation history."""
    if len(history) <= max_pairs * 2:
        return history[:]
    return history[-(max_pairs * 2):]


class VoicePipelineService:
    """
    Orchestrates the full voice AI pipeline.

    Pipeline Flow:
    1. Audio Queue (from media gateway)
    2. STT Provider (streaming transcription — Deepgram)
    3. Turn Detection (EndOfTurn event)
    4. Groq LLM (streaming response generation)
    5. Google TTS (streaming audio synthesis)
    6. Output Queue (back to media gateway)

    Each turn is wrapped in an OTel trace spanning the full STT→LLM→TTS cycle.
    """

    def __init__(
        self,
        stt_provider: STTProvider,
        llm_provider: GroqLLMProvider,
        tts_provider: TTSProvider,
        media_gateway: MediaGateway,
        *,
        stt_sample_rate: int = 16000,
        tts_sample_rate: int = 24000,
    ):
        self.stt_provider = stt_provider
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider
        self.media_gateway = media_gateway
        self.stt_sample_rate = stt_sample_rate
        self.tts_sample_rate = tts_sample_rate

        self.prompt_manager = PromptManager()
        self.transcript_service = TranscriptService()
        self.latency_tracker = get_latency_tracker()
        self._barge_in_events: dict[str, asyncio.Event] = {}
        self._pending_llm_tasks: dict[str, asyncio.Task] = {}
        self._tracer = get_tracer()

    async def _await_task_after_cancel(self, task: asyncio.Task, call_id: str, label: str) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(
                "%s task raised before cancellation completed for call %s: %s",
                label,
                call_id,
                exc,
                exc_info=True,
            )
            return

        exc = task.exception()
        if exc is not None:
            logger.warning(
                "%s task completed with exception for call %s: %s",
                label,
                call_id,
                exc,
                exc_info=True,
            )

    def _record_silent_turn(self, call_id: str, reason: str) -> None:
        record_turn_silent_reason(reason)
        logger.warning(
            "turn_silent_reason call_id=%s reason=%s",
            call_id,
            reason,
            extra={"call_id": call_id, "turn_silent_reason": reason},
        )

    def _response_max_sentences_for_turn(self, turn_id: int) -> Optional[int]:
        """First turn: limit to 2 sentences for faster response start."""
        return 2 if turn_id == 0 else None

    @staticmethod
    def _is_repetitive_transcript(text: str) -> bool:
        """
        Detect Deepgram Flux hallucination: repetitive STT output (GitHub #1524).
        Returns True when a single word dominates >50% of a 6+ word transcript.
        Normal speech ("I'd like to know about your pricing") never hits this.
        """
        words = text.lower().split()
        if len(words) < 6:
            return False
        from collections import Counter
        top_count = Counter(words).most_common(1)[0][1]
        return (top_count / len(words)) > 0.5

    # Conjunctions that signal a natural clause break after a comma.
    # Only these trigger early TTS flush — avoids splitting at list commas
    # ("apples, oranges, and pears") by requiring a coordinating conjunction.
    _CLAUSE_CONJUNCTIONS = ("and ", "but ", "so ", "or ", "yet ", "nor ")

    @staticmethod
    def _find_sentence_end(text: str, allow_clause: bool = False) -> int:
        """
        Return the index of the first sentence-ending character followed by a
        space (clearly end-of-sentence, not mid-abbreviation).  Returns -1 if
        no boundary is found.

        Skips ellipsis (...) to avoid splitting mid-thought pauses.

        allow_clause (default False):
            When True **and** len(text) >= 80, also match a comma+conjunction
            boundary (", and", ", but", etc.) that occurs after at least 40
            characters.  This fires TTS on the first clause of a long opening
            sentence instead of waiting for the full sentence terminator,
            cutting perceived latency by 100-250ms on verbose first responses.

            Only activates when the buffer is long enough that we know we are
            stuck waiting — short responses still flush on hard punctuation.
        """
        clause_candidate = -1
        i = 0
        while i < len(text):
            ch = text[i]
            if ch in "!?":
                if i + 1 < len(text) and text[i + 1] == " ":
                    return i
            elif ch == ".":
                # Skip ellipsis: advance past ALL consecutive dots so the last
                # dot of "..." is not mistaken for a sentence terminator.
                if i + 1 < len(text) and text[i + 1] == ".":
                    while i + 1 < len(text) and text[i + 1] == ".":
                        i += 1
                    # After the ellipsis just continue scanning — don't return.
                elif i + 1 < len(text) and text[i + 1] == " ":
                    return i
            elif (
                allow_clause
                and ch == ","
                and i >= 40                          # enough text before the comma
                and i + 2 < len(text)
                and text[i + 1] == " "
                and clause_candidate < 0             # keep the earliest one
            ):
                rest = text[i + 2:]
                if any(rest.startswith(conj) for conj in VoicePipelineService._CLAUSE_CONJUNCTIONS):
                    clause_candidate = i
            i += 1

        # Return clause boundary only when no sentence boundary was found AND
        # the total buffer is long enough to justify an early flush.
        if allow_clause and clause_candidate >= 0 and len(text) >= 80:
            return clause_candidate
        return -1

    # ── Pipeline lifecycle ─────────────────────────────────────────

    async def start_pipeline(
        self,
        session: CallSession,
        agent_config=None,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id
        # Reuse the CallSession's barge-in event if one was already created
        # (e.g. by the orchestrator for the greeting).  This ensures Flux's
        # StartOfTurn callback sets the SAME event the greeting TTS loop is
        # watching — without this, greeting playback cannot be interrupted.
        existing = getattr(session, "barge_in_event", None)
        if existing is not None:
            self._barge_in_events[call_id] = existing
        else:
            self._barge_in_events[call_id] = asyncio.Event()
            session.barge_in_event = self._barge_in_events[call_id]

        # Wire barge-in event into TelephonyMediaGateway so its pacing loop can
        # exit early instead of draining a full TTS chunk before detection.
        set_barge_in = getattr(self.media_gateway, "set_barge_in_event", None)
        if set_barge_in:
            set_barge_in(call_id, self._barge_in_events[call_id])

        with voice_span("pipeline.start", call_id=call_id,
                        tenant_id=getattr(session, "tenant_id", None)) as span:
            span.set_attribute("voice.call_id", call_id)
            logger.info(
                "pipeline_start",
                extra={"call_id": call_id, "timestamp": datetime.utcnow().isoformat()},
            )
            try:
                session.stt_active = True
                await self.process_audio_stream(session, agent_config, websocket)
            except Exception as e:
                span.record_exception(e)
                logger.error(
                    f"Pipeline error: {e}",
                    extra={"call_id": call_id},
                    exc_info=True,
                )
            finally:
                # Cancel orphaned LLM task — asyncio children are NOT auto-cancelled
                # when their parent task is cancelled.
                pending_task = self._pending_llm_tasks.pop(call_id, None)
                if pending_task:
                    if not pending_task.done():
                        pending_task.cancel()
                    await self._await_task_after_cancel(pending_task, call_id, "orphaned_llm")
                # Remove barge-in event so a future session cannot inherit stale state.
                self._barge_in_events.pop(call_id, None)
                session.stt_active = False
                self.latency_tracker.cleanup_call(call_id)
                logger.info("pipeline_end", extra={"call_id": call_id})

    async def process_audio_stream(
        self,
        session: CallSession,
        agent_config=None,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id

        async def audio_stream() -> AsyncIterator[AudioChunk]:
            queue = self.media_gateway.get_audio_queue(call_id)
            if queue is None:
                return
            while session.stt_active:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.02)
                    if chunk:
                        yield AudioChunk(data=chunk) if isinstance(chunk, bytes) else chunk
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Audio stream error: {e}", extra={"call_id": call_id})
                    break

        # STT span wraps the full transcription stream
        with pipeline_span("stt", call_id=call_id, provider="deepgram",
                           tenant_id=getattr(session, "tenant_id", None)) as stt_span:
            t_stt_start = time.monotonic()

            # Direct barge-in callback: sets the event immediately from the STT
            # background task, even while the pipeline loop is blocked in
            # handle_turn_end.  This is the only reliable way to stop TTS mid-stream.
            def _on_barge_in_direct() -> None:
                event = self._barge_in_events.get(call_id)
                if event:
                    event.set()
                current_metrics = self.latency_tracker.get_metrics(call_id)
                if not current_metrics or current_metrics.turn_id != session.turn_id:
                    self.latency_tracker.start_turn(call_id, session.turn_id)
                self.latency_tracker.mark_listening_start(call_id)

            # ── Silence monitor (telephony only) ───────────────────────────────
            # After 5-7 seconds of continuous caller silence the agent asks if the
            # caller is still there.  Phrases are varied each time to avoid sounding
            # robotic.  Runs in parallel with the STT consumer loop; cancelled when
            # the pipeline exits.  Disabled for Ask AI (browser sessions).
            _SILENCE_PHRASES = [
                "Are you still there?",
                "Still with me?",
                "Hello — you still on the line?",
                "Hey, just checking — can you hear me?",
                "Did I lose you?",
                "You still there? I'm here.",
                "Just making sure I haven't lost you — you there?",
                "Hello? Are you still with me?",
            ]

            async def _silence_monitor() -> None:
                import random
                # Minimum pause after AI finishes speaking before silence counts.
                # Prevents firing immediately after TTS ends while caller is
                # drawing breath to respond.
                _TTS_GRACE_S = 3.0
                _last_event_at = datetime.utcnow()
                _tts_ended_at: Optional[datetime] = None
                _was_active: bool = False
                # Only arm after the FIRST complete AI response — user-speaks-first
                # mode means there is natural silence at call start that must not
                # trigger the monitor.
                _had_first_exchange: bool = False
                consecutive = 0
                _MAX_CONSECUTIVE = 2  # give up after 2 unanswered checks

                while session.stt_active and consecutive < _MAX_CONSECUTIVE:
                    await asyncio.sleep(1.0)  # poll every second

                    if not session.stt_active:
                        break

                    currently_active = session.tts_active or session.llm_active

                    # AI is speaking or processing — reset baseline
                    if currently_active:
                        _last_event_at = datetime.utcnow()
                        _tts_ended_at = None
                        _was_active = True
                        consecutive = 0
                        continue

                    # TTS/LLM just went inactive — start grace period clock
                    if _was_active and not currently_active:
                        _tts_ended_at = datetime.utcnow()
                        _last_event_at = _tts_ended_at
                        _had_first_exchange = True  # AI has spoken at least once
                        _was_active = False
                        consecutive = 0
                        continue  # give the caller the full grace period first

                    _was_active = False

                    # Don't arm until after the first AI response (user-speaks-first)
                    if not _had_first_exchange:
                        _last_event_at = datetime.utcnow()
                        continue

                    # Enforce post-TTS grace period before counting silence
                    if _tts_ended_at is not None:
                        if (datetime.utcnow() - _tts_ended_at).total_seconds() < _TTS_GRACE_S:
                            continue

                    # User is already speaking (StartOfTurn detected before transcript)
                    _barge_ev = self._barge_in_events.get(call_id)
                    if _barge_ev and _barge_ev.is_set():
                        _last_event_at = datetime.utcnow()
                        consecutive = 0
                        continue

                    # User spoke since our last baseline — reset
                    if session.last_activity_at > _last_event_at:
                        _last_event_at = session.last_activity_at
                        consecutive = 0
                        continue

                    # How long has it been since any activity?
                    elapsed = (datetime.utcnow() - _last_event_at).total_seconds()
                    silence_limit = random.uniform(5.0, 7.0)
                    if elapsed < silence_limit:
                        continue

                    # Silence threshold exceeded — ask with a varied phrase
                    phrase = random.choice(_SILENCE_PHRASES)
                    consecutive += 1
                    logger.info(
                        "[SilenceMonitor] %s — %.1fs silence, asking (%d/%d): %r",
                        call_id[:12], elapsed, consecutive, _MAX_CONSECUTIVE, phrase,
                    )
                    try:
                        await self.synthesize_and_send_audio(session, phrase, websocket)
                    except Exception as _sm_exc:
                        logger.debug("[SilenceMonitor] TTS failed: %s", _sm_exc)

                    # Reset baseline with grace period after our own TTS phrase
                    _tts_ended_at = datetime.utcnow()
                    _last_event_at = _tts_ended_at

                logger.debug(
                    "[SilenceMonitor] %s exiting (consecutive=%d stt_active=%s)",
                    call_id[:12], consecutive, session.stt_active,
                )

            # Start silence monitor only for telephony (not Ask AI browser sessions)
            _silence_task: Optional[asyncio.Task] = None
            if getattr(session, "campaign_id", "ask-ai") != "ask-ai":
                _silence_task = asyncio.create_task(_silence_monitor())

            try:
                async for transcript in self.stt_provider.stream_transcribe(
                    audio_stream(),
                    call_id=call_id,
                    on_barge_in=_on_barge_in_direct,
                ):
                    await self.handle_transcript(session, transcript, websocket)
            except Exception as e:
                stt_span.record_exception(e)
                logger.error(f"STT stream error: {e}", extra={"call_id": call_id})
            finally:
                if _silence_task and not _silence_task.done():
                    _silence_task.cancel()
                    try:
                        await _silence_task
                    except asyncio.CancelledError:
                        pass
                record_latency(stt_span, "stt", (time.monotonic() - t_stt_start) * 1000)
                get_stats = getattr(self.stt_provider, "get_stream_stats", None)
                if get_stats:
                    stats = get_stats(call_id)
                    if stats:
                        for k, v in stats.items():
                            try:
                                stt_span.set_attribute(f"stt.{k}", v)
                            except Exception as _e:
                                logger.debug("stt_span_attr k=%s: %s", k, _e)

    # ── Transcript handling ────────────────────────────────────────

    async def handle_transcript(
        self,
        session: CallSession,
        transcript,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id

        if isinstance(transcript, BargeInSignal):
            await self.handle_barge_in(session, websocket)
            return

        if transcript.metadata and transcript.metadata.get("resumed"):
            logger.info(f"TurnResumed for call {call_id} — cancelling speculative LLM")
            session.llm_active = False
            if call_id in self._pending_llm_tasks:
                task = self._pending_llm_tasks.pop(call_id)
                if not task.done():
                    task.cancel()
                await self._await_task_after_cancel(task, call_id, "speculative_llm")
            # Roll back any messages the speculative handle_turn_end appended
            # before being cancelled.  Without this, orphaned user/assistant
            # messages corrupt the conversation context for subsequent turns.
            restore_len = getattr(session, "_speculative_history_len", None)
            if restore_len is not None and len(session.conversation_history) > restore_len:
                session.conversation_history = session.conversation_history[:restore_len]
            session._speculative_history_len = None
            return

        metadata = transcript.metadata or {}
        self.transcript_service.bind_call_identity(call_id, session.talklee_call_id)

        # Ensure latency tracker is aligned with current turn ID.
        # Guard: do NOT reset tracker while LLM/TTS is actively processing.
        # session.turn_id is pre-incremented before _run_turn is created, so a
        # tracker turn_id mismatch during active processing is expected — it is
        # NOT an indication the tracker is stale.
        current_metrics = self.latency_tracker.get_metrics(call_id)
        if (not current_metrics or current_metrics.turn_id != session.turn_id) and not session.llm_active:
            self.latency_tracker.start_turn(call_id, session.turn_id)
            self.latency_tracker.mark_listening_start(call_id)

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

        if self.stt_provider.detect_turn_end(transcript):
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
            existing = self._pending_llm_tasks.get(call_id)
            if existing and not existing.done():
                # A speculative or prior final task is still in flight — skip.
                logger.debug(
                    "final turn_end skipped: pending task already running for %s",
                    call_id[:12],
                )
                return
            session._speculative_history_len = len(session.conversation_history)
            task = asyncio.create_task(
                self.handle_turn_end(session, websocket, source="final")
            )
            self._pending_llm_tasks[call_id] = task
            return

        if metadata.get("eager") and transcript.text:
            if not session.llm_active and call_id not in self._pending_llm_tasks:
                session.current_user_input = transcript.text
                # Snapshot history length so TurnResumed can roll back any
                # messages the speculative task appends before cancellation.
                session._speculative_history_len = len(session.conversation_history)
                # Speculatively start LLM now (EagerEndOfTurn fired — 150–250ms before
                # EndOfTurn). If user keeps talking, TurnResumed cancels this task via
                # the handle_transcript "resumed" branch above (session.llm_active=False
                # + task.cancel()).
                task = asyncio.create_task(
                    self.handle_turn_end(session, websocket, source="speculative")
                )
                self._pending_llm_tasks[call_id] = task
            return

        if transcript.text:
            event_type = "eager_end_of_turn" if metadata.get("eager") else "update"
            if transcript.is_final:
                event_type = "end_of_turn"

            self.transcript_service.accumulate_turn(
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
            self.latency_tracker.mark_stt_first_transcript(call_id)
            session.current_user_input = transcript.text
            session.update_activity()

    # ── Turn end — the full LLM + TTS cycle ───────────────────────

    async def handle_turn_end(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
        source: str = "final",
    ) -> None:
        call_id = session.call_id
        full_transcript = session.current_user_input.strip()
        tenant_id = getattr(session, "tenant_id", None)

        if not full_transcript:
            logger.debug(f"Empty transcript, skipping turn", extra={"call_id": call_id})
            return

        # Guard against the confirmed Deepgram Flux hallucination bug (GitHub #1524)
        # where the STT model outputs repetitive nonsense text ("blah blah blah…").
        # Heuristic: if a single word accounts for >50% of a 6+ word transcript,
        # treat it as a hallucination and skip — avoids sending garbage to the LLM.
        if self._is_repetitive_transcript(full_transcript):
            logger.warning(
                "Repetitive STT transcript likely hallucination, skipping turn",
                extra={"call_id": call_id, "transcript": full_transcript[:80]},
            )
            return

        # Backchannel suppression — short listening sounds ("hmm",
        # "yeah", "uh huh", "mm") are NOT real turns. Without this, the
        # LLM generates a full response to a non-event and loses the
        # conversation's thread. The persona prompts also instruct the
        # model on this at the language level — belt AND braces.
        if _is_backchannel(full_transcript):
            logger.info(
                "backchannel_suppressed transcript=%r call=%s",
                full_transcript, call_id[:12],
            )
            # Clear the session's pending input so the old transcript
            # doesn't carry into the next real turn.
            try:
                session.current_user_input = ""
            except AttributeError:
                pass
            return

        # Clear any barge-in event that was set by the user's own StartOfTurn that
        # triggered this turn.  Deepgram Flux fires StartOfTurn for ALL speech —
        # including normal listening-phase input — which sets barge_in_event via
        # _on_barge_in_direct().  Without this clear, synthesize_and_send_audio sees
        # the stale event as a "barge-in during LLM" and returns immediately without
        # playing any audio, leaving the caller in silence.
        # If the user speaks AGAIN while the LLM is generating, Deepgram fires a new
        # StartOfTurn → event is set again → TTS is correctly suppressed at that point.
        barge_in_event = self._barge_in_events.get(call_id)
        if barge_in_event:
            barge_in_event.clear()

        current_task = asyncio.current_task()
        pending_task = self._pending_llm_tasks.get(call_id)
        if pending_task and pending_task.done():
            self._pending_llm_tasks.pop(call_id, None)
            pending_task = None

        if pending_task and pending_task is not current_task:
            logger.debug(
                "turn_skipped_pending_task",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "source": source,
                    "transcript": full_transcript[:80],
                },
            )
            return

        # Guard: skip if a concurrent LLM/TTS (e.g. greeting) is already running.
        # session.llm_active is set True in _send_outbound_greeting and in this
        # function; it is reset to False in the finally block below.
        if session.llm_active and pending_task is not current_task:
            logger.debug(
                "turn_skipped_llm_busy",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "source": source,
                    "transcript": full_transcript[:80],
                },
            )
            return

        logger.info(
            "turn_end",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "source": source,
                "transcript": full_transcript,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        # Clear barge-in event now that EndOfTurn has fired (user stopped speaking).
        # Stale barge-in signals from the user's own speech turn are now irrelevant.
        # Any NEW barge-in signal that fires AFTER this point means the user started
        # speaking again WHILE the AI is processing/responding — and must NOT be wiped.
        barge_in_event = self._barge_in_events.get(call_id)
        if barge_in_event:
            barge_in_event.clear()

        # Parent span for the complete LLM+TTS turn
        with voice_span(
            "turn",
            call_id=call_id,
            tenant_id=tenant_id,
            **{"voice.turn.id": session.turn_id, "voice.turn.transcript": full_transcript[:200]},
        ) as turn_span:
            session.state = CallState.PROCESSING
            session.llm_active = True
            self.latency_tracker.mark_speech_end(call_id)
            self.latency_tracker.mark_llm_start(call_id)

            # NOTE: user message is appended inside _run_turn, which owns the
            # history snapshot + rollback on error/cancellation.  Do NOT append
            # here — it would produce a duplicate entry visible to the LLM on
            # every turn, wasting tokens and corrupting conversation context.

            try:
                # ── LLM + TTS (sentence-pipelined) ────────────────
                with pipeline_span("llm_tts", call_id=call_id, provider="groq",
                                   tenant_id=tenant_id) as llm_tts_span:
                    t0 = time.monotonic()
                    response_text, llm_latency, tts_latency = await self._run_turn(
                        session, full_transcript, websocket, session.turn_id
                    )
                    total_wall = (time.monotonic() - t0) * 1000

                    llm_tts_span.set_attribute("llm.response_chars", len(response_text))
                    llm_tts_span.set_attribute("llm.latency_ms", round(llm_latency, 1))
                    llm_tts_span.set_attribute("tts.latency_ms", round(tts_latency, 1))
                    session.add_latency_measurement("llm", llm_latency)
                    session.add_latency_measurement("tts", tts_latency)

                logger.info(
                    "llm_response",
                    extra={
                        "call_id": call_id,
                        "turn_id": session.turn_id,
                        "response": response_text,
                        "llm_latency_ms": round(llm_latency, 1),
                        "tts_latency_ms": round(tts_latency, 1),
                    },
                )

                # total_wall is the actual wall-clock time (LLM and TTS overlap with pipelining)
                session.add_latency_measurement("total_turn", total_wall)

                # Attach full breakdown to parent turn span
                turn_span.set_attribute("voice.turn.llm_ms", round(llm_latency, 1))
                turn_span.set_attribute("voice.turn.tts_ms", round(tts_latency, 1))
                turn_span.set_attribute("voice.turn.total_ms", round(total_wall, 1))

                # Pull detailed sub-metrics from LatencyTracker and attach to span
                tracked = self.latency_tracker.get_metrics(call_id)
                if tracked:
                    for attr, val in [
                        ("stt_first_transcript", tracked.stt_first_transcript_ms),
                        ("llm_first_token",      tracked.llm_first_token_ms),
                        ("tts_first_chunk",      tracked.tts_first_chunk_ms),
                        ("response_start",       tracked.response_start_latency_ms),
                        ("total",                tracked.total_latency_ms),
                    ]:
                        if val is not None and val >= 0:
                            session.add_latency_measurement(attr, val)
                            turn_span.set_attribute(f"voice.latency.{attr}_ms", round(val, 1))
                    self.latency_tracker.log_metrics(call_id)

                logger.info(
                    "turn_complete",
                    extra={
                        "call_id": call_id,
                        "turn_id": session.turn_id,
                        "llm_latency_ms": round(llm_latency, 1),
                        "tts_latency_ms": round(tts_latency, 1),
                        "total_latency_ms": round(total_wall, 1),
                    },
                )

                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "turn_complete",
                            "llm_latency_ms": round(llm_latency, 1),
                            "tts_latency_ms": round(tts_latency, 1),
                            "total_latency_ms": round(total_wall, 1),
                        })
                    except Exception as e:
                        logger.warning(f"Failed to send turn_complete to websocket: {e}")

                # Flush transcript to DB incrementally
                try:
                    container = get_container()
                    if container.is_initialized:
                        postgres_client = PostgresAdapterClient(container.db_pool)
                        await self.transcript_service.flush_to_database(
                            call_id=call_id,
                            db_client=postgres_client,
                            tenant_id=tenant_id,
                            talklee_call_id=session.talklee_call_id,
                        )
                except Exception as e:
                    logger.warning(f"Failed to flush transcript for {call_id}: {e}")

            except Exception as e:
                turn_span.record_exception(e)
                logger.error(
                    f"Error processing turn: {e}",
                    extra={"call_id": call_id, "error": str(e)},
                    exc_info=True,
                )
                # GAP 7 — LLM failure apology: play a short TTS apology so the
                # caller knows something went wrong rather than hearing silence.
                # Use a bare try so an apology TTS failure never masks the original error.
                try:
                    await self.synthesize_and_send_audio(
                        session,
                        "I'm sorry, I'm having trouble right now. Please try again in a moment.",
                        websocket,
                    )
                except Exception:
                    pass
            finally:
                pending_task = self._pending_llm_tasks.get(call_id)
                if pending_task is current_task or (pending_task and pending_task.done()):
                    self._pending_llm_tasks.pop(call_id, None)
                session.llm_active = False
                # Clear speculative snapshot — turn completed normally so
                # the messages it appended are valid and must not be rolled back.
                session._speculative_history_len = None
                session.increment_turn()

    # ── Barge-in ──────────────────────────────────────────────────

    async def handle_barge_in(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id
        logger.info(
            "barge_in_detected",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "timestamp": datetime.utcnow().isoformat(),
                "tts_active": session.tts_active,
            },
        )
        if call_id in self._barge_in_events:
            self._barge_in_events[call_id].set()

        # Cancel any in-flight speculative LLM task (EagerEndOfTurn fired before
        # the user finished speaking).  Without this, the speculative task keeps
        # running until TurnResumed arrives — wasting LLM compute and potentially
        # writing a stale user+assistant message pair to conversation history.
        speculative_task = self._pending_llm_tasks.pop(call_id, None)
        if speculative_task:
            if not speculative_task.done():
                speculative_task.cancel()
            await self._await_task_after_cancel(speculative_task, call_id, "speculative_llm")
            # Roll back any history the speculative task may have appended
            # before being cancelled (mirrors the TurnResumed rollback path).
            restore_len = getattr(session, "_speculative_history_len", None)
            if restore_len is not None and len(session.conversation_history) > restore_len:
                session.conversation_history = session.conversation_history[:restore_len]
            session._speculative_history_len = None
            logger.debug(
                "barge_in: cancelled speculative LLM task for %s", call_id[:12]
            )

        # Annotate the last assistant message so the LLM knows the caller
        # did not hear the full response.  This prevents the LLM from
        # referencing content from the unheard portion and guides it to
        # respond to the caller's interruption instead.
        #
        # IMPORTANT: only annotate when TTS was actively playing at the
        # moment the barge-in fired.  StartOfTurn also fires when the user
        # simply starts their next question after the AI has already finished
        # speaking (session.tts_active=False).  Annotating in that case
        # falsely tells the LLM "you were interrupted" — it then behaves as
        # if the previous response was incomplete and loses conversational
        # context, making replies feel disconnected.
        if session.tts_active and session.conversation_history:
            last_msg = session.conversation_history[-1]
            if (
                last_msg.role == MessageRole.ASSISTANT
                and "[interrupted by caller]" not in last_msg.content
            ):
                last_msg.content = (
                    last_msg.content.rstrip() + " [interrupted by caller]"
                )

        session.current_ai_response = ""
        session.current_user_input = ""  # Reset so stale transcript never reaches LLM
        session.tts_active = False
        session.state = CallState.LISTENING
        # Immediately tell the media gateway (and downstream C++ gateway) to
        # discard any buffered TTS audio so the caller stops hearing the AI.
        try:
            await self.media_gateway.clear_output_buffer(call_id)
        except Exception as _exc:
            logger.debug("clear_output_buffer on barge-in failed: %s", _exc)
        # Tell the TTS provider to cancel any server-side buffered audio.
        # For Deepgram this sends a Clear message that stops further audio
        # chunks from being generated — critical for fast barge-in (<200ms).
        clear_tts = getattr(self.tts_provider, "clear_queue", None)
        if clear_tts:
            try:
                await clear_tts()
            except Exception as _exc:
                logger.debug("tts clear_queue on barge-in failed: %s", _exc)
        if websocket:
            try:
                await websocket.send_json({
                    "type": "barge_in",
                    "message": "User started speaking, stopping TTS",
                    "timestamp": datetime.utcnow().isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to send barge_in to websocket: {e}")

    def clear_barge_in_event(self, session: CallSession) -> None:
        """Clear the barge-in event so pending TTS is not immediately interrupted."""
        event = self._barge_in_events.get(session.call_id)
        if event:
            event.clear()

    # ── Sentence-pipelined LLM → TTS ──────────────────────────────

    async def _stream_llm_and_tts(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
    ) -> tuple[str, float, float]:
        """
        Stream LLM tokens and pipeline TTS per sentence.

        Fires {"type": "llm_response"} on the websocket on the first token so the
        frontend can unblock its audio player before TTS begins — cutting perceived
        TTFA by the full LLM generation time on short responses.

        TTS starts as soon as the first complete sentence is detected in the token
        stream.  Subsequent sentences are TTS-ed immediately after the preceding one
        finishes, so the LLM generates sentence N+1 while sentence N plays back.

        Returns (full_response_text, llm_latency_ms, tts_latency_ms).
          llm_latency_ms = wall-clock for the entire LLM stream.
          tts_latency_ms = wall-clock from first TTS call to last TTS completion.
        """
        call_id = session.call_id
        barge_in_event = self._barge_in_events.get(call_id)
        guardrails = get_guardrails()

        messages = _truncate_history(session.conversation_history)
        system_prompt = session.system_prompt

        # Ask AI: inject product/pricing info only when the user's message contains
        # relevant keywords.  On greeting and general turns this keeps the effective
        # system prompt at ~60 tokens (vs ~360 when the product block is always present),
        # saving 40-60ms of Groq prefill latency per non-product turn.
        if session.campaign_id == "ask-ai" and messages:
            last_user_text = next(
                (m.content.lower() for m in reversed(messages) if m.role == MessageRole.USER),
                "",
            )
            if any(kw in last_user_text for kw in _ASK_AI_PRODUCT_KEYWORDS):
                system_prompt = system_prompt + "\n\n" + _ASK_AI_PRODUCT_INFO

        if session.captured_slots is not None:
            system_prompt = compose_system_prompt(system_prompt, session.captured_slots)

        max_sentences = self._response_max_sentences_for_turn(session.turn_id)

        all_tokens: list[str] = []
        buf = ""
        first_token = True
        first_sentence = True
        sentences_done = 0
        tts_was_interrupted = False

        t_llm_start = time.monotonic()
        t_tts_first: Optional[float] = None
        t_tts_end: Optional[float] = None

        try:
            async for token in self.llm_provider.stream_chat_with_timeout(
                messages,
                system_prompt=system_prompt,
            ):
                if first_token:
                    self.latency_tracker.mark_llm_first_token(call_id)
                    # Unblock the frontend audio player immediately on first token
                    # so the jitter buffer can start filling before TTS begins.
                    if websocket:
                        try:
                            await websocket.send_json({"type": "llm_response"})
                        except Exception:
                            pass
                    first_token = False

                if barge_in_event and barge_in_event.is_set():
                    tts_was_interrupted = True
                    break

                all_tokens.append(token)
                buf += token

                # Flush each complete sentence (or, for long buffers, the first
                # clause) to TTS as tokens arrive.  allow_clause activates only
                # when buf >= 80 chars so short responses are unaffected.
                while not (max_sentences and sentences_done >= max_sentences):
                    idx = self._find_sentence_end(buf, allow_clause=len(buf) >= 80)
                    if idx < 0:
                        break

                    sentence = guardrails.clean_response(buf[:idx + 1].strip())
                    buf = buf[idx + 2:] if idx + 2 <= len(buf) else ""

                    if not sentence or len(sentence) < 6:
                        continue

                    if barge_in_event and barge_in_event.is_set():
                        tts_was_interrupted = True
                        break

                    if t_tts_first is None:
                        t_tts_first = time.monotonic()
                        self.latency_tracker.mark_tts_start(call_id)

                    session.tts_active = True
                    tts_was_interrupted = await self.synthesize_and_send_audio(
                        session, sentence, websocket, track_latency=first_sentence,
                    )
                    first_sentence = False
                    t_tts_end = time.monotonic()
                    sentences_done += 1

                    if tts_was_interrupted:
                        break

                if tts_was_interrupted:
                    break

        except LLMTimeoutError:
            if sentences_done > 0 or t_tts_first is not None:
                # Partial content already sent to TTS — Groq stalled mid-stream.
                # The user heard real audio; don't append a fallback on top of it.
                logger.warning(
                    "LLM timeout for call %s after %d sentence(s) TTS'd — "
                    "dropping remaining buffer, no fallback", call_id, sentences_done
                )
                buf = ""
                # Keep all_tokens as-is so conversation history reflects what was said
            else:
                # No content reached TTS yet — safe to play fallback
                logger.warning(f"LLM timeout for call {call_id} (no TTS yet), using fallback")
                buf = "I'm sorry, could you repeat that?"
                all_tokens.clear()
                all_tokens.append(buf)
        except Exception as e:
            logger.error(f"LLM streaming error for call {call_id}: {e}", exc_info=True)
            if sentences_done > 0 or t_tts_first is not None:
                logger.warning("LLM error for %s after partial TTS — dropping buffer", call_id)
                buf = ""
            else:
                buf = "I'm sorry, I had trouble processing that. Could you say it again?"
                all_tokens.clear()
                all_tokens.append(buf)

        t_llm_done = time.monotonic()
        self.latency_tracker.mark_llm_end(call_id)

        # TTS any trailing buffer (final sentence without terminal punctuation,
        # or a one-sentence response with no terminating punctuation at all).
        if not tts_was_interrupted and buf.strip():
            if not (barge_in_event and barge_in_event.is_set()):
                if not max_sentences or sentences_done < max_sentences:
                    sentence = guardrails.clean_response(buf.strip())
                    if sentence:
                        if t_tts_first is None:
                            t_tts_first = time.monotonic()
                            self.latency_tracker.mark_tts_start(call_id)
                        session.tts_active = True
                        tts_was_interrupted = await self.synthesize_and_send_audio(
                            session, sentence, websocket, track_latency=first_sentence,
                        )
                        first_sentence = False
                        t_tts_end = time.monotonic()

        llm_latency_ms = (t_llm_done - t_llm_start) * 1000
        tts_latency_ms = (
            (t_tts_end - t_tts_first) * 1000
            if t_tts_first is not None and t_tts_end is not None
            else 0.0
        )

        # Build the full response for history / logging.
        # Apply guardrails to the assembled text (strips markdown, fillers, etc.)
        # and enforce the per-turn sentence cap.
        full_text = guardrails.clean_response("".join(all_tokens))
        if max_sentences and full_text:
            import re as _re
            parts = _re.split(r'(?<=[.!?])\s+', full_text.strip())
            full_text = " ".join(parts[:max_sentences])

        return full_text, llm_latency_ms, tts_latency_ms

    async def _run_turn(
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

        if session.captured_slots is None:
            session.captured_slots = CapturedSlotsState()
        session.captured_slots = update_state_from_user_turn(
            session.captured_slots, full_transcript
        )

        response_text = ""
        llm_latency_ms = 0.0
        tts_latency_ms = 0.0

        try:
            response_text, llm_latency_ms, tts_latency_ms = await self._stream_llm_and_tts(
                session, websocket
            )

            if response_text and response_text.strip():
                session.conversation_history.append(
                    Message(role=MessageRole.ASSISTANT, content=response_text)
                )
                self.transcript_service.accumulate_turn(
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
            session.conversation_history = session.conversation_history[:history_snapshot]
            raise
        except Exception as e:
            logger.error(f"Turn error for call {call_id}: {e}", exc_info=True)
            session.conversation_history = session.conversation_history[:history_snapshot]

        return response_text, llm_latency_ms, tts_latency_ms

    # ── LLM helper ────────────────────────────────────────────────

    async def get_llm_response(self, session: CallSession, user_input: str) -> str:
        """Get LLM response with guardrails applied."""
        call_id = session.call_id
        try:
            guardrails = get_guardrails()
            config = LLMGuardrailsConfig()

            messages = session.conversation_history[:]
            system_prompt = session.system_prompt
            if session.captured_slots is not None:
                system_prompt = compose_system_prompt(system_prompt, session.captured_slots)

            max_sentences = self._response_max_sentences_for_turn(session.turn_id)

            tokens: list[str] = []
            first_token = True
            async for token in self.llm_provider.stream_chat_with_timeout(
                messages,
                system_prompt=system_prompt,
            ):
                if first_token:
                    self.latency_tracker.mark_llm_first_token(call_id)
                    first_token = False
                tokens.append(token)
            response = "".join(tokens)

            if max_sentences and response:
                import re as _re
                parts = _re.split(r'(?<=[.!?])\s+', response.strip())
                response = " ".join(parts[:max_sentences])

            sanitized = guardrails.clean_response(response)
            return sanitized

        except LLMTimeoutError:
            logger.warning(f"LLM timeout for call {call_id}, using fallback")
            return "I'm sorry, could you repeat that?"
        except Exception as e:
            logger.error(f"LLM error for call {call_id}: {e}", exc_info=True)
            return "I'm sorry, I had trouble processing that. Could you say it again?"

    # ── TTS helper ────────────────────────────────────────────────

    async def synthesize_and_send_audio(
        self,
        session: CallSession,
        text: str,
        websocket: Optional[WebSocket] = None,
        *,
        track_latency: bool = True,
    ) -> bool:
        """
        Synthesize TTS audio and stream it to the media gateway.
        Returns True if TTS was interrupted by barge-in, False on normal completion.
        """
        call_id = session.call_id
        barge_in_event = self._barge_in_events.get(call_id)
        # Mark TTS as active here so handle_turn_end skips if a greeting
        # or a previous turn is already speaking.
        session.tts_active = True

        interrupted = False
        completed = False
        silent_reason: Optional[str] = None
        first_chunk = True
        first_chunk_sent = False  # track whether any audio reached the gateway
        try:
            # If user spoke during the LLM call, the barge-in event is already set.
            # Don't start TTS — send the stop signal immediately and return.
            if barge_in_event and barge_in_event.is_set():
                interrupted = True
                logger.info(
                    "barge_in_before_tts",
                    extra={"call_id": call_id, "turn_id": session.turn_id},
                )
                barge_in_event.clear()
                try:
                    await self.media_gateway.clear_output_buffer(call_id)
                except Exception as _e:
                    logger.debug("barge_in_clear_buffer_failed call_id=%s: %s", call_id[:8], _e)
                if websocket:
                    try:
                        await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                    except Exception as _e:
                        logger.debug("barge_in_ws_notify_failed call_id=%s: %s", call_id[:8], _e)
                # Must return `interrupted` (True), not bare `return` (None).
                # A bare return gives None to the caller, which is falsy — the
                # sentence loop in _stream_llm_and_tts would not break and would
                # immediately call TTS again with the next sentence, causing the
                # AI to start speaking again right after being interrupted.
                return interrupted

            async for audio_chunk in self.tts_provider.stream_synthesize(
                text,
                voice_id=session.voice_id,
                sample_rate=self.tts_sample_rate,
                call_id=call_id,
            ):
                if barge_in_event and barge_in_event.is_set():
                    logger.info(f"Barge-in interrupted TTS for call {call_id}")
                    interrupted = True
                    barge_in_event.clear()
                    try:
                        await self.media_gateway.clear_output_buffer(call_id)
                    except Exception as _exc:
                        logger.debug("clear_output_buffer mid-TTS failed: %s", _exc)
                    # Tell the browser to stop playing immediately — don't wait for
                    # handle_barge_in to do it after handle_turn_end completes.
                    if websocket:
                        try:
                            await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                        except Exception as _exc:
                            logger.debug("tts_interrupted WS send failed: %s", _exc)
                    break
                if first_chunk:
                    if track_latency:
                        self.latency_tracker.mark_tts_first_chunk(call_id)
                        self.latency_tracker.mark_response_start(call_id)
                        self.latency_tracker.mark_audio_start(call_id)
                    first_chunk = False
                raw = audio_chunk.data if hasattr(audio_chunk, "data") else audio_chunk
                # Int16 PCM requires an even number of bytes (2 bytes per sample).
                # Odd-length chunks from a TTS provider indicate a framing error;
                # silently passing them corrupts every subsequent sample's phase.
                # Trim the orphan byte here — the gateway's pending_byte mechanism
                # would hide it, but it's better to surface it at the source.
                if len(raw) % 2 != 0:
                    logger.warning(
                        "TTS chunk odd length %d bytes from %s for call %s — "
                        "trimming trailing byte to maintain Int16 alignment",
                        len(raw), getattr(self.tts_provider, "name", "unknown"), call_id,
                    )
                    raw = raw[:-1]
                if not raw:
                    continue
                await self.media_gateway.send_audio(call_id, raw)
                first_chunk_sent = True  # at least one chunk reached the gateway
                # Check barge-in again immediately after send: barge-in may have
                # fired during the gateway send await before the next TTS chunk arrives.
                if barge_in_event and barge_in_event.is_set():
                    logger.info(f"Barge-in (post-send) interrupted TTS for call {call_id}")
                    interrupted = True
                    barge_in_event.clear()
                    try:
                        await self.media_gateway.clear_output_buffer(call_id)
                    except Exception as _exc:
                        logger.debug("clear_output_buffer post-send failed: %s", _exc)
                    if websocket:
                        try:
                            await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                        except Exception as _exc:
                            logger.debug("tts_interrupted post-send WS send failed: %s", _exc)
                    break
            else:
                # Normal completion (not interrupted by barge-in) — flush any
                # remaining bytes in the gateway output buffer so the last
                # portion of audio is not silently dropped.
                flush = getattr(self.media_gateway, "flush_tts_buffer", None)
                if not flush:
                    flush = getattr(self.media_gateway, "flush_audio_buffer", None)
                if flush:
                    try:
                        await flush(call_id)
                    except Exception as _exc:
                        logger.debug("flush buffer failed: %s", _exc)
                completed = True
        except SessionGoneError:
            # Browser WebSocket was torn down while TTS was streaming.
            # Exit the loop silently — this is normal teardown, not an error.
            silent_reason = "session_gone"
            logger.debug("TTS loop stopped: browser session %s already gone", call_id)
        except Exception as e:
            silent_reason = "tts_exception"
            logger.error(f"TTS synthesis error for call {call_id}: {e}", exc_info=True)
            # FIX 4 — If no audio reached the gateway yet, play a one-shot fallback
            # so the caller gets an explicit signal instead of silence.  The
            # _tts_fallback_attempted flag prevents infinite recursion when the
            # fallback itself fails (e.g. TTS provider is fully down).
            if not first_chunk_sent and not getattr(session, "_tts_fallback_attempted", False):
                session._tts_fallback_attempted = True
                try:
                    await self.synthesize_and_send_audio(
                        session,
                        "I'm sorry, I couldn't respond. Please say that again.",
                        websocket,
                        track_latency=False,
                    )
                except Exception:
                    pass
        finally:
            if not interrupted and first_chunk:
                if silent_reason is None and completed:
                    silent_reason = "provider_empty_stream"
                if silent_reason is not None:
                    self._record_silent_turn(call_id, silent_reason)
            session._tts_fallback_attempted = False
            if track_latency:
                self.latency_tracker.mark_tts_end(call_id)
                if interrupted:
                    self.latency_tracker.mark_interrupted(call_id, reason="barge_in")
                elif completed:
                    self.latency_tracker.mark_completed(call_id)
            session.tts_active = False
        return interrupted
