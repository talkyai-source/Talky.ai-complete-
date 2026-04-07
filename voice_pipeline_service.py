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
        self._barge_in_events[call_id] = asyncio.Event()

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
            while session.stt_active:
                try:
                    chunk = await asyncio.wait_for(
                        self.media_gateway.get_audio_chunk(call_id),
                        timeout=0.02,
                    )
                    if chunk:
                        yield chunk
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

            try:
                async for transcript in self.stt_provider.stream_transcribe(
                    audio_stream(),
                    sample_rate=self.stt_sample_rate,
                    call_id=call_id,
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
            return

        metadata = transcript.metadata or {}
        self.transcript_service.bind_call_identity(call_id, session.talklee_call_id)

        current_metrics = self.latency_tracker.get_metrics(call_id)
        if not current_metrics or current_metrics.turn_id != session.turn_id:
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
                        if val is not None:
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
        session.current_ai_response = ""
        session.tts_active = False
        session.state = CallState.LISTENING
        if websocket:
            try:
                await websocket.send_json({
                    "type": "barge_in",
                    "message": "User started speaking, stopping TTS",
                    "timestamp": datetime.utcnow().isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to send barge_in to websocket: {e}")

    # ── LLM helper ────────────────────────────────────────────────

    async def get_llm_response(self, session: CallSession, user_input: str) -> str:
        """Get LLM response with guardrails applied."""
        call_id = session.call_id
        try:
            guardrails = get_guardrails()
            config = LLMGuardrailsConfig()

            messages = session.conversation_history[:]
            system_prompt = self.prompt_manager.get_system_prompt(
                getattr(session, "agent_config", None)
            )

            max_sentences = self._response_max_sentences_for_turn(session.turn_id)

            response = await self.llm_provider.generate_response(
                messages=messages,
                system_prompt=system_prompt,
                max_sentences=max_sentences,
            )

            sanitized = guardrails.sanitize_output(response, config)
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

        try:
            first_chunk = True
            async for audio_chunk in self.tts_provider.stream_synthesize(text):
                if barge_in_event and barge_in_event.is_set():
                    logger.info(f"Barge-in interrupted TTS for call {call_id}")
                    barge_in_event.clear()
                    break
                if first_chunk:
                    self.latency_tracker.mark_tts_first_chunk(call_id)
                    self.latency_tracker.mark_audio_start(call_id)
                    first_chunk = False
                await self.media_gateway.send_audio(call_id, audio_chunk)
        except Exception as e:
            logger.error(f"TTS synthesis error for call {call_id}: {e}", exc_info=True)
        finally:
            session.tts_active = False
