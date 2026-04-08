"""
Voice Pipeline Service
Orchestrates the full voice AI pipeline: STT → LLM → TTS

Now instrumented with OpenTelemetry distributed tracing.
Every turn produces a parent span covering the full STT→LLM→TTS cycle,
with child spans per stage and latency attributes on each.
"""
import asyncio
import logging
import time
from typing import Optional, AsyncIterator
from datetime import datetime

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import AudioChunk, TranscriptChunk, Message, MessageRole, BargeInSignal
from app.domain.models.conversation_state import ConversationState, CallOutcomeType
from app.domain.interfaces.stt_provider import STTProvider
from app.infrastructure.llm.groq import GroqLLMProvider, LLMTimeoutError
from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.interfaces.media_gateway import MediaGateway
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.services.prompt_manager import PromptManager
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.llm_guardrails import LLMGuardrails, LLMGuardrailsConfig, get_guardrails
from app.domain.services.latency_tracker import get_latency_tracker
from app.domain.services.global_ai_config import get_global_config
from app.core.container import get_container
from app.core.postgres_adapter import Client as PostgresAdapterClient
from app.core.telemetry import get_tracer, pipeline_span, record_latency, voice_span

logger = logging.getLogger(__name__)


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

    def _response_max_sentences_for_turn(self, turn_id: int) -> Optional[int]:
        """First turn: limit to 2 sentences for faster response start."""
        return 2 if turn_id == 0 else None

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
                self._pending_llm_tasks.pop(call_id, None)
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
            self.latency_tracker.start_turn(call_id, session.turn_id)
            self.latency_tracker.mark_listening_start(call_id)
            t_stt_start = time.monotonic()

            # Direct barge-in callback: sets the event immediately from the STT
            # background task, even while the pipeline loop is blocked in
            # handle_turn_end.  This is the only reliable way to stop TTS mid-stream.
            def _on_barge_in_direct() -> None:
                event = self._barge_in_events.get(call_id)
                if event:
                    event.set()

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
                record_latency(stt_span, "stt", (time.monotonic() - t_stt_start) * 1000)
                get_stats = getattr(self.stt_provider, "get_stream_stats", None)
                if get_stats:
                    stats = get_stats(call_id)
                    if stats:
                        for k, v in stats.items():
                            try:
                                stt_span.set_attribute(f"stt.{k}", v)
                            except Exception:
                                pass

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
            await self.handle_turn_end(session, websocket)
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
                task = asyncio.create_task(self.handle_turn_end(session, websocket))
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
    ) -> None:
        call_id = session.call_id
        full_transcript = session.current_user_input.strip()
        tenant_id = getattr(session, "tenant_id", None)

        if not full_transcript:
            logger.debug(f"Empty transcript, skipping turn", extra={"call_id": call_id})
            return

        logger.info(
            "turn_end",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "transcript": full_transcript,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        # Guard: skip if a concurrent LLM/TTS (e.g. greeting) is already running.
        # session.llm_active is set True in _send_outbound_greeting and in this
        # function; it is reset to False in the finally block below.
        if session.llm_active:
            logger.debug(
                "turn_skipped_llm_busy",
                extra={"call_id": call_id, "turn_id": session.turn_id,
                       "transcript": full_transcript[:80]},
            )
            return

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

            user_message = Message(role=MessageRole.USER, content=full_transcript)
            session.conversation_history.append(user_message)

            try:
                # ── LLM ───────────────────────────────────────────
                with pipeline_span("llm", call_id=call_id, provider="groq",
                                   tenant_id=tenant_id) as llm_span:
                    t0 = time.monotonic()
                    response_text = await self.get_llm_response(session, full_transcript)
                    llm_latency = (time.monotonic() - t0) * 1000

                    self.latency_tracker.mark_llm_end(call_id)
                    record_latency(llm_span, "llm", llm_latency)
                    llm_span.set_attribute("llm.response_chars", len(response_text))
                    session.add_latency_measurement("llm", llm_latency)

                logger.info(
                    "llm_response",
                    extra={
                        "call_id": call_id,
                        "turn_id": session.turn_id,
                        "response": response_text,
                        "latency_ms": round(llm_latency, 1),
                    },
                )

                if response_text and response_text.strip():
                    session.conversation_history.append(
                        Message(role=MessageRole.ASSISTANT, content=response_text)
                    )
                else:
                    logger.warning(f"Empty LLM response for call {call_id} — skipping history append")

                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "llm_response",
                            "text": response_text,
                            "latency_ms": round(llm_latency, 1),
                        })
                    except Exception as e:
                        logger.warning(f"Failed to send llm_response to websocket: {e}")

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

                # ── TTS ───────────────────────────────────────────
                session.tts_active = True
                self.latency_tracker.mark_tts_start(call_id)

                with pipeline_span("tts", call_id=call_id, provider="google",
                                   tenant_id=tenant_id) as tts_span:
                    t0 = time.monotonic()
                    await self.synthesize_and_send_audio(session, response_text, websocket)
                    tts_latency = (time.monotonic() - t0) * 1000

                    record_latency(tts_span, "tts", tts_latency)
                    tts_span.set_attribute("tts.response_chars", len(response_text))
                    session.add_latency_measurement("tts", tts_latency)

                total_latency = llm_latency + tts_latency
                session.add_latency_measurement("total_turn", total_latency)

                # Attach full breakdown to parent turn span
                turn_span.set_attribute("voice.turn.llm_ms", round(llm_latency, 1))
                turn_span.set_attribute("voice.turn.tts_ms", round(tts_latency, 1))
                turn_span.set_attribute("voice.turn.total_ms", round(total_latency, 1))

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
                        "total_latency_ms": round(total_latency, 1),
                    },
                )

                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "turn_complete",
                            "llm_latency_ms": round(llm_latency, 1),
                            "tts_latency_ms": round(tts_latency, 1),
                            "total_latency_ms": round(total_latency, 1),
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
            finally:
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

        # Annotate the last assistant message so the LLM knows the caller
        # did not hear the full response.  This prevents the LLM from
        # referencing content from the unheard portion and guides it to
        # respond to the caller's interruption instead.
        if session.conversation_history:
            last_msg = session.conversation_history[-1]
            if (
                last_msg.role == MessageRole.ASSISTANT
                and "[interrupted by caller]" not in last_msg.content
            ):
                last_msg.content = (
                    last_msg.content.rstrip() + " [interrupted by caller]"
                )

        session.current_ai_response = ""
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

    # ── LLM helper ────────────────────────────────────────────────

    async def get_llm_response(self, session: CallSession, user_input: str) -> str:
        """Get LLM response with guardrails applied."""
        call_id = session.call_id
        try:
            guardrails = get_guardrails()
            config = LLMGuardrailsConfig()

            messages = session.conversation_history[:]
            system_prompt = session.system_prompt

            max_sentences = self._response_max_sentences_for_turn(session.turn_id)

            tokens: list[str] = []
            async for token in self.llm_provider.stream_chat_with_timeout(
                messages,
                system_prompt=system_prompt,
            ):
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
    ) -> None:
        """Synthesize TTS audio and stream it to the media gateway."""
        call_id = session.call_id
        barge_in_event = self._barge_in_events.get(call_id)
        # Mark TTS as active here so handle_turn_end skips if a greeting
        # or a previous turn is already speaking.
        session.tts_active = True

        interrupted = False
        try:
            # If user spoke during the LLM call, the barge-in event is already set.
            # Don't start TTS — send the stop signal immediately and return.
            if barge_in_event and barge_in_event.is_set():
                logger.info(
                    "barge_in_before_tts",
                    extra={"call_id": call_id, "turn_id": session.turn_id},
                )
                barge_in_event.clear()
                try:
                    await self.media_gateway.clear_output_buffer(call_id)
                except Exception:
                    pass
                if websocket:
                    try:
                        await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                    except Exception:
                        pass
                return

            first_chunk = True
            async for audio_chunk in self.tts_provider.stream_synthesize(
                text,
                voice_id=session.voice_id,
                sample_rate=self.tts_sample_rate,
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
                    self.latency_tracker.mark_tts_first_chunk(call_id)
                    self.latency_tracker.mark_audio_start(call_id)
                    first_chunk = False
                raw = audio_chunk.data if hasattr(audio_chunk, "data") else audio_chunk
                await self.media_gateway.send_audio(call_id, raw)
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
        except Exception as e:
            logger.error(f"TTS synthesis error for call {call_id}: {e}", exc_info=True)
        finally:
            session.tts_active = False
