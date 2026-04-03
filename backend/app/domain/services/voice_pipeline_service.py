"""
Voice Pipeline Service
Orchestrates the full voice AI pipeline: STT → LLM → TTS
Integrates conversation state machine and prompt management (Day 5)

Day 17: Fixed partial transcript handling, added incremental transcript persistence,
and integrated LLM guardrails with human-like fallback responses.
"""
import asyncio
import logging
from typing import Optional, AsyncIterator
from datetime import datetime

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import AudioChunk, TranscriptChunk, Message, MessageRole, BargeInSignal
from app.domain.models.conversation_state import ConversationState, CallOutcomeType
from app.domain.interfaces.stt_provider import STTProvider
from app.infrastructure.llm.groq import GroqLLMProvider, LLMTimeoutError
# Cartesia disabled - using Google TTS only
# from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.domain.interfaces.tts_provider import TTSProvider  # Use base type for TTS agnosticism
from app.domain.interfaces.media_gateway import MediaGateway
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.services.prompt_manager import PromptManager
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.llm_guardrails import LLMGuardrails, LLMGuardrailsConfig, get_guardrails
from app.domain.services.latency_tracker import get_latency_tracker
from app.domain.services.global_ai_config import get_global_config
from app.core.container import get_container
from app.core.postgres_adapter import Client as PostgresAdapterClient

logger = logging.getLogger(__name__)


class VoicePipelineService:
    """
    Orchestrates the full voice AI pipeline.
    
    Pipeline Flow:
    1. Audio Queue (from media gateway)
    2. STT Provider (streaming transcription - ElevenLabs/Deepgram)
    3. Turn Detection (EndOfTurn event)
    4. Groq LLM (streaming response generation)
    5. Google TTS (streaming audio synthesis) - Cartesia disabled
    6. Output Queue (back to media gateway)
    
    Handles:
    - Real-time audio streaming
    - Turn detection and barge-in
    - Latency tracking
    - Structured logging
    - Error recovery
    """
    
    def __init__(
        self,
        stt_provider: STTProvider,
        llm_provider: GroqLLMProvider,
        tts_provider: TTSProvider,  # Generic TTS provider (Google, etc.)
        media_gateway: MediaGateway,
        *,
        stt_sample_rate: int = 16000,
        tts_sample_rate: int = 24000,
        mute_during_tts: bool = True,
    ):
        self.stt_provider = stt_provider
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider
        self.media_gateway = media_gateway
        self.stt_sample_rate = stt_sample_rate
        self.tts_sample_rate = tts_sample_rate
        self.mute_during_tts = mute_during_tts
        
        # Day 5: Conversation management components
        self.prompt_manager = PromptManager()
        # Note: ConversationEngine is initialized per-session with agent_config
        
        # Day 10: Transcript accumulation service
        self.transcript_service = TranscriptService()
        
        # Day 17: LLM guardrails for timeout and fallback handling
        self.guardrails = get_guardrails()
        self.latency_tracker = get_latency_tracker()
        
        self._active_pipelines: dict[str, bool] = {}
        
        # Shared per-call interruption events. Greeting playback and normal TTS
        # must watch the same event so a StartOfTurn always stops active speech.
        self._barge_in_events: dict[str, asyncio.Event] = {}
        self._active_turn_tasks: dict[str, asyncio.Task] = {}
        
        # EagerEndOfTurn: Track speculative LLM tasks for cancellation on TurnResumed
        self._pending_llm_tasks: dict[str, asyncio.Task] = {}

    def get_or_create_barge_in_event(self, session: CallSession) -> asyncio.Event:
        """Return the shared interruption event for this call."""
        event = getattr(session, "barge_in_event", None)
        if event is None:
            event = self._barge_in_events.get(session.call_id)
        if event is None:
            event = asyncio.Event()
        session.barge_in_event = event
        self._barge_in_events[session.call_id] = event
        return event

    def clear_barge_in_event(self, session: CallSession) -> None:
        """Clear the shared interruption event after a new user turn is committed."""
        self.get_or_create_barge_in_event(session).clear()

    def get_active_turn_task(self, call_id: str) -> Optional[asyncio.Task]:
        """Return the currently running turn task for this call, if any."""
        task = self._active_turn_tasks.get(call_id)
        if task is not None and task.done():
            self._active_turn_tasks.pop(call_id, None)
            return None
        return task

    def _register_active_turn_task(self, call_id: str, task: asyncio.Task) -> None:
        """Track the active turn task and clean up bookkeeping when it finishes."""
        self._active_turn_tasks[call_id] = task

        def _cleanup(done_task: asyncio.Task) -> None:
            current_task = self._active_turn_tasks.get(call_id)
            if current_task is done_task:
                self._active_turn_tasks.pop(call_id, None)

        task.add_done_callback(_cleanup)

    async def _wait_for_active_turn_task(
        self,
        call_id: str,
        *,
        timeout_seconds: float = 0.5,
    ) -> None:
        """Wait briefly for an active turn task to finish cancelling."""
        task = self.get_active_turn_task(call_id)
        if task is None:
            return

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _response_max_sentences_for_turn(
        self,
        session: CallSession,
        user_input: str,
        *,
        has_custom_prompt: bool,
    ) -> int:
        """
        Allow slightly longer pricing/package answers for product-demo sessions.

        The default voice policy is brief answers, but plan/pricing questions
        need enough room to mention all tiers without truncating mid-answer.
        """
        base_limit = (
            session.agent_config.response_max_sentences
            if session.agent_config is not None
            else self.guardrails.config.max_sentences
        )
        if not has_custom_prompt:
            return base_limit

        lowered = user_input.lower()
        pricing_terms = (
            "price", "pricing", "cost", "costs",
            "plan", "plans", "package", "packages",
            "tier", "tiers", "subscription", "monthly",
        )
        if any(term in lowered for term in pricing_terms):
            return max(base_limit, 4)
        return base_limit
    
    async def start_pipeline(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None
    ) -> None:
        """
        Start the voice pipeline for a call session.
        
        Args:
            session: Active call session
            websocket: Optional WebSocket connection for sending updates
        """
        call_id = session.call_id
        self.transcript_service.bind_call_identity(call_id, session.talklee_call_id)
        
        logger.info(
            f"Starting voice pipeline for call {call_id}",
            extra={"call_id": call_id}
        )
        
        self._active_pipelines[call_id] = True
        
        try:
            # Update session state
            session.state = CallState.ACTIVE
            session.stt_active = True
            
            # Get audio queue from media gateway
            audio_queue = self.media_gateway.get_audio_queue(call_id)
            
            if not audio_queue:
                logger.error(
                    f"No audio queue found for call {call_id}",
                    extra={"call_id": call_id}
                )
                return
            
            # Process audio stream
            await self.process_audio_stream(session, audio_queue, websocket)
        
        except Exception as e:
            logger.error(
                f"Pipeline error for call {call_id}: {e}",
                extra={"call_id": call_id, "error": str(e)},
                exc_info=True
            )
            session.state = CallState.ERROR
        
        finally:
            active_turn_task = self.get_active_turn_task(call_id)
            if active_turn_task is not None:
                active_turn_task.cancel()
                try:
                    await active_turn_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("Ignoring active turn task error during pipeline shutdown", exc_info=True)
            self._active_pipelines.pop(call_id, None)
            self._barge_in_events.pop(call_id, None)
            self._active_turn_tasks.pop(call_id, None)
            self._pending_llm_tasks.pop(call_id, None)
            session.stt_active = False
            self.latency_tracker.cleanup_call(call_id)
            
            logger.info(
                f"Voice pipeline ended for call {call_id}",
                extra={"call_id": call_id}
            )
    
    async def process_audio_stream(
        self,
        session: CallSession,
        audio_queue: asyncio.Queue,
        websocket: Optional[WebSocket] = None
    ) -> None:
        """
        Process audio stream through STT pipeline.
        
        Args:
            session: Active call session
            audio_queue: Queue of incoming audio chunks
            websocket: Optional WebSocket for updates
        """
        call_id = session.call_id
        
        print(f"🔊 [PIPELINE] Processing audio stream for call {call_id}", flush=True)
        
        # Debug: check queue
        try:
            queue_size = audio_queue.qsize() if hasattr(audio_queue, 'qsize') else 'unknown'
            print(f"🔊 [PIPELINE] Initial audio queue size: {queue_size}", flush=True)
        except Exception as e:
            print(f"🔊 [PIPELINE] Queue check error: {e}", flush=True)
        
        # Create async generator from queue
        async def audio_stream() -> AsyncIterator[AudioChunk]:
            while self._active_pipelines.get(call_id, False):
                try:
                    # Get audio chunk with timeout
                    audio_data = await asyncio.wait_for(
                        audio_queue.get(),
                        timeout=0.02  # Reduced from 0.1s for lower latency
                    )
                    
                    yield AudioChunk(
                        data=audio_data,
                        sample_rate=self.stt_sample_rate,
                        channels=1
                    )
                
                except asyncio.TimeoutError:
                    # No audio available, continue
                    continue
                
                except Exception as e:
                    logger.error(
                        f"Error reading audio queue: {e}",
                        extra={"call_id": call_id, "error": str(e)}
                    )
                    break
        
        # Stream audio to STT
        print(f"🔊 [PIPELINE] Starting STT stream_transcribe...", flush=True)
        try:
            self.latency_tracker.start_turn(call_id, session.turn_id)
            self.latency_tracker.mark_listening_start(call_id)
            transcript_count = 0
            async for transcript in self.stt_provider.stream_transcribe(
                audio_stream(),
                language="en",
                call_id=call_id
            ):
                transcript_count += 1
                if transcript_count <= 5:
                    if isinstance(transcript, BargeInSignal):
                        print(f"🔊 [PIPELINE] Got barge-in signal #{transcript_count}", flush=True)
                    else:
                        print(f"🔊 [PIPELINE] Got transcript #{transcript_count}: {transcript.text!r}", flush=True)
                await self.handle_transcript(session, transcript, websocket)
            print(f"🔊 [PIPELINE] STT stream ended. Total transcripts: {transcript_count}", flush=True)
            get_stats = getattr(self.stt_provider, "get_stream_stats", None)
            if callable(get_stats):
                stt_stats = get_stats(call_id)
                if stt_stats:
                    logger.info(
                        "stt_stream_stats",
                        extra={"call_id": call_id, **stt_stats},
                    )
        
        except Exception as e:
            logger.error(
                f"STT streaming error: {e}",
                extra={"call_id": call_id, "error": str(e)},
                exc_info=True
            )
    
    async def handle_transcript(
        self,
        session: CallSession,
        transcript,  # Can be TranscriptChunk or BargeInSignal
        websocket: Optional[WebSocket] = None
    ) -> None:
        """
        Handle transcript chunk from STT or barge-in signal.
        
        Implements Flux EagerEndOfTurn pattern for lower latency:
        - EagerEndOfTurn: Start LLM preparation early (speculative)
        - TurnResumed: Cancel speculative LLM call
        - EndOfTurn: Finalize and send response
        
        Args:
            session: Active call session
            transcript: Transcript chunk from STT or BargeInSignal
            websocket: Optional WebSocket for updates
        """
        call_id = session.call_id
        
        # Check if this is a barge-in signal (user started speaking during TTS)
        if isinstance(transcript, BargeInSignal):
            await self.handle_barge_in(session, websocket)
            return
        
        # Handle TurnResumed - cancel any speculative processing
        if transcript.metadata and transcript.metadata.get("resumed"):
            logger.info(f"TurnResumed for call {call_id} - cancelling speculative processing")
            session.llm_active = False
            # Cancel any pending LLM tasks
            if call_id in self._pending_llm_tasks:
                task = self._pending_llm_tasks[call_id]
                if not task.done():
                    task.cancel()
                    logger.info(f"Cancelled speculative LLM task for {call_id}")
                del self._pending_llm_tasks[call_id]
            return
        
        # Log transcript with structured data
        metadata = transcript.metadata or {}
        self.transcript_service.bind_call_identity(call_id, session.talklee_call_id)

        # Ensure latency tracker is aligned with current turn ID.
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
                "eager": metadata.get("eager", False)
            }
        )
        
        # Send transcript to WebSocket for browser display
        if websocket and transcript.text:
            try:
                msg_type = "transcript"
                # Eager transcripts are shown as partial
                if metadata.get("eager"):
                    msg_type = "transcript_eager"
                
                await websocket.send_json({
                    "type": msg_type,
                    "text": transcript.text,
                    "is_final": transcript.is_final,
                    "confidence": transcript.confidence
                })
            except Exception as e:
                logger.warning(f"Failed to send transcript to websocket: {e}")
        
        # Check for turn end (EndOfTurn with empty text signals turn end)
        if self.stt_provider.detect_turn_end(transcript):
            # User finished speaking
            await self.handle_turn_end(session, websocket)
            return
        
        # Handle EagerEndOfTurn - start LLM early for lower latency
        if metadata.get("eager") and transcript.text:
            # Only start eager processing if not already processing
            if not session.llm_active and call_id not in self._pending_llm_tasks:
                logger.info(f"EagerEndOfTurn for call {call_id} - starting speculative LLM")
                # Store the eager transcript but don't process yet
                # We'll process when EndOfTurn confirms
                session.current_user_input = transcript.text
            return
        
        # Regular transcript handling
        if transcript.text:
            event_type = "update"
            include_in_plaintext = False
            if metadata.get("eager"):
                event_type = "eager_end_of_turn"
            if transcript.is_final:
                event_type = "end_of_turn"
                include_in_plaintext = True

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
                include_in_plaintext=include_in_plaintext,
                metadata=metadata,
            )

            self.latency_tracker.mark_stt_first_transcript(call_id)

            # Day 17: Fixed partial transcript handling
            # Deepgram Flux sends "Update" events that REPLACE previous partials
            # So we store the latest partial, not concatenate
            if transcript.is_final:
                # Final transcript from EndOfTurn - use as-is
                session.current_user_input = transcript.text
            else:
                # Partial transcript - replace (Flux updates are cumulative)
                session.current_user_input = transcript.text
            
            # Update session activity
            session.update_activity()
    
    async def handle_barge_in(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None
    ) -> None:
        """
        Handle barge-in: user started speaking during agent speech.
        
        This interrupts TTS playback and prepares to listen to user input.
        
        Args:
            session: Active call session
            websocket: Optional WebSocket for updates
        """
        call_id = session.call_id
        
        logger.info(
            "barge_in_detected",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "timestamp": datetime.utcnow().isoformat(),
                "tts_active": session.tts_active
            }
        )
        
        # Signal greeting/reply playback to stop immediately.
        self.get_or_create_barge_in_event(session).set()
        logger.info(f"Barge-in event set for call {call_id}")

        active_turn_task = self.get_active_turn_task(call_id)
        if active_turn_task is not None:
            active_turn_task.cancel()

        if hasattr(self.media_gateway, "clear_output_buffer"):
            await self.media_gateway.clear_output_buffer(call_id)

        # Cancel current AI response (it was interrupted)
        session.current_ai_response = ""
        session.llm_active = False
        session.tts_active = False
        
        # Update session state to listening
        session.state = CallState.LISTENING
        
        # Notify frontend to stop audio playback immediately
        if websocket:
            try:
                await websocket.send_json({
                    "type": "barge_in",
                    "message": "User started speaking, stopping TTS",
                    "timestamp": datetime.utcnow().isoformat()
                })
            except Exception as e:
                logger.warning(f"Failed to send barge_in to websocket: {e}")
    
    async def handle_turn_end(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None
    ) -> None:
        """
        Handle end of user's turn (user finished speaking).
        
        Triggers LLM processing and TTS synthesis.
        
        Args:
            session: Active call session
            websocket: Optional WebSocket for updates
        """
        call_id = session.call_id
        full_transcript = session.current_user_input.strip()
        
        if not full_transcript:
            logger.debug(
                f"Empty transcript, skipping turn",
                extra={"call_id": call_id}
            )
            return
        
        active_turn_task = self.get_active_turn_task(call_id)
        if active_turn_task is not None:
            logger.info(
                "waiting_for_previous_turn_shutdown",
                extra={"call_id": call_id, "turn_id": session.turn_id},
            )
            await self._wait_for_active_turn_task(call_id)

        active_turn_task = self.get_active_turn_task(call_id)
        if active_turn_task is not None:
            logger.warning(
                "forcing_previous_turn_cancellation",
                extra={"call_id": call_id, "turn_id": session.turn_id},
            )
            active_turn_task.cancel()
            await self._wait_for_active_turn_task(call_id, timeout_seconds=0.2)

        logger.info(
            "turn_end",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "transcript": full_transcript,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        # The previous assistant turn has now been superseded by this user turn.
        # Clear the shared interrupt so the next reply for this transcript can speak.
        self.clear_barge_in_event(session)
        processing_turn_id = session.turn_id
        session.current_user_input = ""
        session.turn_id += 1
        session.update_activity()

        turn_task = asyncio.create_task(
            self._run_turn(session, full_transcript, websocket, processing_turn_id)
        )
        self._register_active_turn_task(call_id, turn_task)

    async def _run_turn(
        self,
        session: CallSession,
        full_transcript: str,
        websocket: Optional[WebSocket],
        turn_id: int,
    ) -> None:
        """Process one committed user turn in a cancellable background task."""
        call_id = session.call_id

        session.state = CallState.PROCESSING
        session.llm_active = True
        self.latency_tracker.mark_speech_end(call_id)
        self.latency_tracker.mark_llm_start(call_id)

        user_message = Message(
            role=MessageRole.USER,
            content=full_transcript,
        )
        session.conversation_history.append(user_message)
        _user_msg_appended = True

        try:
            llm_start = datetime.utcnow()

            response_text = await self.get_llm_response(
                session,
                full_transcript,
                turn_id=turn_id,
            )
            self.latency_tracker.mark_llm_end(call_id)

            llm_latency = (datetime.utcnow() - llm_start).total_seconds() * 1000
            session.add_latency_measurement("llm", llm_latency)

            logger.info(
                "llm_response",
                extra={
                    "call_id": call_id,
                    "turn_id": turn_id,
                    "response": response_text,
                    "latency_ms": llm_latency,
                },
            )

            if websocket:
                try:
                    await websocket.send_json(
                        {
                            "type": "llm_response",
                            "text": response_text,
                            "latency_ms": llm_latency,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to send llm_response to websocket: {e}")

            session.tts_active = True
            self.latency_tracker.mark_tts_start(call_id)

            tts_start = datetime.utcnow()
            was_interrupted = await self.synthesize_and_send_audio(
                session,
                response_text,
                websocket,
                turn_id=turn_id,
            )

            tts_latency = (datetime.utcnow() - tts_start).total_seconds() * 1000
            session.add_latency_measurement("tts", tts_latency)

            if response_text and response_text.strip():
                # Commit assistant message whether or not there was a barge-in.
                # If barge-in occurred, this is a partial response — still commit
                # it to maintain user→assistant alternation in history.
                assistant_message = Message(
                    role=MessageRole.ASSISTANT,
                    content=response_text,
                )
                session.conversation_history.append(assistant_message)

                self.transcript_service.accumulate_turn(
                    call_id=call_id,
                    role="assistant",
                    content=response_text,
                    talklee_call_id=session.talklee_call_id,
                    turn_index=turn_id,
                    event_type="assistant_response",
                    is_final=True,
                    include_in_plaintext=True,
                )
                if was_interrupted:
                    logger.info(
                        "assistant_reply_committed_despite_barge_in",
                        extra={"call_id": call_id, "turn_id": turn_id},
                    )
            elif _user_msg_appended:
                # LLM produced nothing. Roll back the user message to prevent
                # consecutive user messages in history.
                if (
                    session.conversation_history
                    and session.conversation_history[-1] is user_message
                ):
                    session.conversation_history.pop()
                    logger.info(
                        "user_message_rolled_back_empty_turn",
                        extra={"call_id": call_id, "turn_id": turn_id},
                    )

            total_latency = llm_latency + tts_latency
            session.add_latency_measurement("total_turn", total_latency)

            tracked = self.latency_tracker.get_metrics(call_id)
            if tracked:
                if tracked.stt_first_transcript_ms is not None:
                    session.add_latency_measurement(
                        "stt_first_transcript", tracked.stt_first_transcript_ms
                    )
                if tracked.llm_first_token_ms is not None:
                    session.add_latency_measurement(
                        "llm_first_token", tracked.llm_first_token_ms
                    )
                if tracked.tts_first_chunk_ms is not None:
                    session.add_latency_measurement(
                        "tts_first_chunk", tracked.tts_first_chunk_ms
                    )
                if tracked.response_start_latency_ms is not None:
                    session.add_latency_measurement(
                        "response_start", tracked.response_start_latency_ms
                    )
                self.latency_tracker.log_metrics(call_id)

            if not was_interrupted:
                logger.info(
                    "turn_complete",
                    extra={
                        "call_id": call_id,
                        "turn_id": turn_id,
                        "llm_latency_ms": llm_latency,
                        "tts_latency_ms": tts_latency,
                        "total_latency_ms": total_latency,
                    },
                )

                if websocket:
                    try:
                        await websocket.send_json(
                            {
                                "type": "turn_complete",
                                "llm_latency_ms": llm_latency,
                                "tts_latency_ms": tts_latency,
                                "total_latency_ms": total_latency,
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send turn_complete to websocket: {e}")

            try:
                container = get_container()
                if container.is_initialized:
                    postgres_client = PostgresAdapterClient(container.db_pool)
                    await self.transcript_service.flush_to_database(
                        call_id=call_id,
                        db_client=postgres_client,
                        tenant_id=getattr(session, "tenant_id", None),
                        talklee_call_id=session.talklee_call_id,
                    )
                else:
                    logger.debug("Skipping transcript flush: container not initialized")
            except Exception as e:
                logger.warning(f"Failed to flush transcript for {call_id}: {e}")

        except asyncio.CancelledError:
            if (
                _user_msg_appended
                and session.conversation_history
                and session.conversation_history[-1] is user_message
            ):
                session.conversation_history.pop()
                logger.info(
                    "user_message_rolled_back_on_cancel",
                    extra={"call_id": call_id, "turn_id": turn_id},
                )
            logger.info(
                "turn_processing_cancelled",
                extra={"call_id": call_id, "turn_id": turn_id},
            )
            raise

        except Exception as e:
            if (
                _user_msg_appended
                and session.conversation_history
                and session.conversation_history[-1] is user_message
            ):
                session.conversation_history.pop()
                logger.info(
                    "user_message_rolled_back_on_error",
                    extra={"call_id": call_id, "turn_id": turn_id, "error": str(e)},
                )
            logger.error(
                f"Error processing turn: {e}",
                extra={"call_id": call_id, "error": str(e)},
                exc_info=True,
            )

        finally:
            session.state = CallState.LISTENING
            session.llm_active = False
            session.tts_active = False
            session.current_ai_response = ""

            current_metrics = self.latency_tracker.get_metrics(call_id)
            if not current_metrics or current_metrics.turn_id != session.turn_id:
                self.latency_tracker.start_turn(call_id, session.turn_id)
                self.latency_tracker.mark_listening_start(call_id)
    
    async def get_llm_response(
        self,
        session: CallSession,
        user_input: str,
        turn_id: Optional[int] = None,
    ) -> str:
        """
        Get LLM response for user input using conversation state machine.
        
        Implements Groq best practices:
        - State-aware system prompts
        - Assistant prefilling for direct responses
        - Optimized parameters (temp=0.3 for voice calls)
        - Context window management
        
        Args:
            session: Active call session
            user_input: User's transcribed speech
            
        Returns:
            LLM response text
        """
        call_id = session.call_id
        effective_turn_id = session.turn_id if turn_id is None else turn_id
        
        # ---------------------------------------------------------------
        # Determine prompt mode:
        # If the endpoint set a custom system_prompt on the session (e.g.
        # ai_options_ws / ask_ai_ws), use it directly.  This avoids the
        # PromptManager templates which contain appointment-themed
        # examples that conflict with the product-info role.
        # ---------------------------------------------------------------
        has_custom_prompt = bool(getattr(session, 'system_prompt', None) and session.system_prompt.strip())
        
        if has_custom_prompt:
            # ---- CUSTOM PROMPT PATH (voice_demo / ask_ai) ----
            # Skip ConversationEngine state machine — we don't need
            # GREETING→QUALIFICATION→CLOSING for product Q&A sessions.
            system_prompt = session.system_prompt
            
            logger.info(
                "using_custom_system_prompt",
                extra={
                    "call_id": call_id,
                    "turn_id": effective_turn_id,
                    "prompt_length": len(system_prompt),
                }
            )
        else:
            # ---- STATE MACHINE PATH (campaign calls, outbound dialer) ----
            if session.agent_config:
                conversation_engine = ConversationEngine(session.agent_config)
            else:
                logger.warning(
                    f"No agent_config in session {call_id}, using basic LLM response",
                    extra={"call_id": call_id}
                )
                return await self._get_basic_llm_response(session)
            
            # Process user input through conversation engine
            new_state, llm_instruction, detected_intent = await conversation_engine.handle_user_input(
                current_state=session.conversation_state,
                user_text=user_input,
                conversation_history=session.conversation_history,
                context=session.conversation_context
            )
            
            session.conversation_state = new_state
            
            logger.info(
                "conversation_state_transition",
                extra={
                    "call_id": call_id,
                    "turn_id": effective_turn_id,
                    "intent": detected_intent.value,
                    "new_state": new_state.value,
                    "objection_count": session.conversation_context.objection_count
                }
            )
            
            # Generate state-aware system prompt from templates
            system_prompt = self.prompt_manager.render_system_prompt(
                agent_config=session.agent_config,
                state=new_state,
                greeting_context=f"I'm calling to {session.agent_config.get_goal_description()}",
                qualification_instruction=llm_instruction,
                user_concern=user_input if detected_intent.value in ["uncertain", "objection"] else None,
                objection_count=session.conversation_context.objection_count,
                max_objections=session.agent_config.flow.max_objection_attempts,
                confirmation_details=getattr(session, 'confirmation_details', None)
            )
        
        # 3. Prepare conversation history with context window management
        max_history_messages = 10
        recent_history = session.conversation_history[-max_history_messages:] if len(session.conversation_history) > max_history_messages else session.conversation_history
        
        # 4. Implement assistant prefilling for direct responses (Groq best practice)
        messages_with_prefill = recent_history.copy()
        
        prefill_content = self._get_prefill_for_state(
            session.conversation_state if not has_custom_prompt else ConversationState.GREETING
        )
        if prefill_content:
            messages_with_prefill.append(Message(
                role=MessageRole.ASSISTANT,
                content=prefill_content
            ))
        
        # 5. Stream LLM response with timeout and guardrails
        response_text = prefill_content if prefill_content else ""
        use_fallback = False
        
        # Use global config for LLM parameters
        global_config = get_global_config()

        # Session-specific config takes priority (Ask-AI / per-endpoint overrides)
        effective_model = getattr(session, "llm_model", None) or global_config.llm_model
        effective_temperature = (
            session.llm_temperature
            if getattr(session, "llm_temperature", None) is not None
            else global_config.llm_temperature
        )
        effective_max_tokens = (
            session.llm_max_tokens
            if getattr(session, "llm_max_tokens", None) is not None
            else global_config.llm_max_tokens
        )
        
        # Enhanced debug logging
        logger.info(f"[LLM DEBUG] Preparing LLM call for {call_id}")
        logger.info(f"[LLM DEBUG] System prompt length: {len(system_prompt)} chars, custom={has_custom_prompt}")
        logger.info(f"[LLM DEBUG] Messages with prefill count: {len(messages_with_prefill)}")
        logger.info(
            "[LLM DEBUG] Effective config - "
            f"model: {effective_model}, temp: {effective_temperature}, max_tokens: {effective_max_tokens}"
        )
        
        if messages_with_prefill:
            last_msg = messages_with_prefill[-1]
            logger.info(f"[LLM DEBUG] Last message role: {last_msg.role.value}, content: '{last_msg.content[:100] if last_msg.content else 'EMPTY'}...'")
        
        try:
            # Use timeout-enabled streaming for graceful degradation
            first_token_seen = False
            async for token in self.llm_provider.stream_chat_with_timeout(
                messages=messages_with_prefill,
                timeout_seconds=self.guardrails.config.max_response_time_seconds,
                system_prompt=system_prompt,
                model=effective_model,
                temperature=effective_temperature,
                max_tokens=effective_max_tokens,
                stop=["###", "\n\n\n"]  # Stop sequences for concise responses
            ):
                if token and not first_token_seen:
                    first_token_seen = True
                    self.latency_tracker.mark_llm_first_token(call_id)
                response_text += token
                session.current_ai_response += token
            
            # Debug logging to track response through processing
            logger.info(f"LLM raw response ({len(response_text)} chars): '{response_text[:100]}...'")
            
            # CRITICAL: Check for zero-token response and use fallback early
            if not response_text.strip():
                logger.warning(f"[LLM DEBUG] Zero tokens received from LLM for call {call_id} - this should not happen!")
                logger.warning(f"[LLM DEBUG] System prompt preview: '{system_prompt[:200] if system_prompt else 'NONE'}...'")
                logger.warning(f"[LLM DEBUG] Messages: {[str(m)[:50] for m in messages_with_prefill]}")
                # Don't count this as validation error, go straight to fallback
                use_fallback = True
                response_text = ""
            else:
                # Only clean/validate if we got actual tokens
                # Clean and validate response
                response_text = self.guardrails.clean_response(response_text)
                logger.info(f"After clean_response ({len(response_text)} chars): '{response_text[:100] if response_text else 'EMPTY'}...'")

                # agent_config may be None for telephony sessions without campaign context
                # (e.g. raw SIP calls). Skip truncation/validation in that case.
                _agent_cfg = session.agent_config
                if _agent_cfg is not None:
                    max_sentences = self._response_max_sentences_for_turn(
                        session,
                        user_input,
                        has_custom_prompt=has_custom_prompt,
                    )
                    response_text = self.guardrails.truncate_response(
                        response_text,
                        max_sentences=max_sentences
                    )
                    logger.info(f"After truncate_response ({len(response_text)} chars): '{response_text[:100] if response_text else 'EMPTY'}...'")

                    # Validate against rules
                    is_valid, reason = self.guardrails.validate_response(
                        response_text,
                        _agent_cfg.rules
                    )
                    if not is_valid:
                        logger.warning(f"Response validation failed: {reason}, using fallback")
                        session.conversation_context.increment_llm_error()
                        use_fallback = True
        
        except LLMTimeoutError:
            # LLM timeout - use human-like fallback (no AI hints)
            # Use session.conversation_state because new_state is only assigned
            # in the state-machine branch; custom-prompt sessions never set it.
            logger.warning(
                "llm_timeout_fallback",
                extra={
                    "call_id": call_id,
                    "turn_id": effective_turn_id,
                    "state": session.conversation_state.value
                }
            )
            session.conversation_context.increment_llm_error()
            use_fallback = True
        
        except Exception as e:
            logger.error(
                f"LLM error, using fallback: {e}",
                extra={"call_id": call_id, "error": str(e)}
            )
            session.conversation_context.increment_llm_error()
            use_fallback = True
        
        # Apply fallback if needed
        if use_fallback:
            fallback_response, should_end = self.guardrails.get_fallback_response(
                state=session.conversation_state,
                call_id=call_id,
                error_count=session.conversation_context.llm_error_count
            )
            response_text = fallback_response
            session.current_ai_response = fallback_response
            
            if should_end:
                logger.warning(
                    "max_llm_errors_graceful_goodbye",
                    extra={"call_id": call_id, "error_count": session.conversation_context.llm_error_count}
                )
                session.state = CallState.ENDING
                session.conversation_context.set_outcome(
                    CallOutcomeType.ERROR, 
                    "max_llm_errors"
                )
        
        # 6. Check if conversation should end (only for state-machine path)
        if not has_custom_prompt and session.agent_config:
            conversation_engine = ConversationEngine(session.agent_config)
            should_end, end_reason = conversation_engine.should_end_conversation(
                state=session.conversation_state,
                turn_count=effective_turn_id,
                context=session.conversation_context
            )
            
            if should_end:
                outcome = conversation_engine.determine_outcome(
                    final_state=session.conversation_state,
                    context=session.conversation_context,
                    turn_count=effective_turn_id
                )
                
                logger.info(
                    "conversation_ending",
                    extra={
                        "call_id": call_id,
                        "reason": end_reason,
                        "final_state": session.conversation_state.value if hasattr(session.conversation_state, 'value') else str(session.conversation_state),
                        "outcome": outcome.value
                    }
                )
                session.state = CallState.ENDING
        
        return response_text.strip()
    
    def _get_prefill_for_state(self, state: ConversationState) -> str:
        """
        Get assistant prefill content for state to enforce direct responses.
        Implements Groq's assistant prefilling best practice.
        
        Args:
            state: Current conversation state
            
        Returns:
            Prefill content or empty string
        """
        # Prefilling helps skip unnecessary preambles
        # For voice calls, we want immediate, direct responses
        prefills = {
            ConversationState.GREETING: "",  # No prefill for greeting, let it be natural
            ConversationState.QUALIFICATION: "",  # Direct question works best
            ConversationState.OBJECTION_HANDLING: "",  # Empathy needs natural start
            ConversationState.CLOSING: "",  # Confirmation should be natural
            ConversationState.TRANSFER: "",  # Transfer message should be complete
            ConversationState.GOODBYE: ""  # Goodbye should be natural
        }
        
        return prefills.get(state, "")
    
    async def _get_basic_llm_response(self, session: CallSession) -> str:
        """
        Fallback: Get basic LLM response without conversation state machine.
        Used when agent_config is not available.
        
        Args:
            session: Active call session
            
        Returns:
            LLM response text
        """
        response_text = ""
        
        async for token in self.llm_provider.stream_chat(
            messages=session.conversation_history,
            system_prompt=session.system_prompt,
            temperature=0.7,
            max_tokens=150
        ):
            response_text += token
            session.current_ai_response += token
        
        return response_text
    
    def _clean_text_for_tts(self, text: str) -> str:
        """Delegate to the shared audio_utils helper to avoid code duplication."""
        from app.utils.audio_utils import clean_text_for_tts
        return clean_text_for_tts(text)

    async def synthesize_and_send_audio(
        self,
        session: CallSession,
        text: str,
        websocket: Optional[WebSocket] = None,
        turn_id: Optional[int] = None,
    ) -> bool:
        """
        Synthesize text to speech and send to output queue.
        
        Supports barge-in: if user starts speaking during TTS,
        the synthesis is interrupted immediately.
        
        Args:
            session: Active call session
            text: Text to synthesize
        """
        call_id = session.call_id
        effective_turn_id = session.turn_id if turn_id is None else turn_id
        
        # Clean text for TTS (remove markdown, emojis, etc.)
        cleaned_text = self._clean_text_for_tts(text)
        
        logger.info(
            "tts_start",
            extra={
                "call_id": call_id,
                "turn_id": effective_turn_id,
                "original_text": text,
                "cleaned_text": cleaned_text
            }
        )
        
        barge_in_event = self.get_or_create_barge_in_event(session)
        was_interrupted = barge_in_event.is_set()
        
        # Use session voice_id if available, otherwise fall back to global config
        global_config = get_global_config()

        # Priority: session.voice_id > global_config.tts_voice_id
        voice_id = getattr(session, 'voice_id', None) or global_config.tts_voice_id
        # Use pipeline-level sample rate (set from VoiceSessionConfig) instead
        # of the global default, so FreeSWITCH gets 8 kHz while browser gets 24 kHz.
        sample_rate = self.tts_sample_rate

        logger.info(
            f"TTS synthesizing voice_id={voice_id} sample_rate={sample_rate} "
            f"for call {call_id}"
        )

        did_mute_stt = False
        first_audio_chunk_sent = False
        try:
            if not was_interrupted:
                # Mute STT microphone during TTS to prevent echo detection.
                if self.mute_during_tts and hasattr(self.stt_provider, 'mute'):
                    await self.stt_provider.mute(call_id)
                    did_mute_stt = True
                    logger.debug(f"Muted STT for call {call_id} during TTS")

                if hasattr(self.media_gateway, "start_playback_tracking"):
                    self.media_gateway.start_playback_tracking(call_id)
                
                # Stream TTS synthesis with barge-in awareness.
                async for audio_chunk in self.tts_provider.stream_synthesize(
                    text=cleaned_text,
                    voice_id=voice_id,
                    sample_rate=sample_rate
                ):
                    if barge_in_event.is_set():
                        logger.info(
                            "tts_interrupted_by_barge_in",
                            extra={
                                "call_id": call_id,
                                "turn_id": effective_turn_id
                            }
                        )
                        was_interrupted = True
                        break
                    
                    await self.media_gateway.send_audio(
                        call_id,
                        audio_chunk.data
                    )
                    if not first_audio_chunk_sent:
                        first_audio_chunk_sent = True
                        self.latency_tracker.mark_tts_first_chunk(call_id)
                        self.latency_tracker.mark_response_start(call_id)
        except asyncio.CancelledError:
            was_interrupted = True
            raise
        
        finally:
            self.latency_tracker.mark_tts_end(call_id)
            # Flush remaining audio only for uninterrupted turns.
            if not was_interrupted and hasattr(self.media_gateway, 'flush_audio_buffer'):
                await self.media_gateway.flush_audio_buffer(call_id)
            elif was_interrupted and hasattr(self.media_gateway, "clear_output_buffer"):
                await self.media_gateway.clear_output_buffer(call_id)
                if websocket:
                    try:
                        await websocket.send_json(
                            {"type": "tts_interrupted", "reason": "barge_in"}
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send tts_interrupted to websocket: {e}")
            if was_interrupted:
                barge_in_event.clear()

            waited_for_browser_playback = False
            if (
                not was_interrupted
                and first_audio_chunk_sent
                and hasattr(self.media_gateway, "wait_for_playback_complete")
            ):
                if websocket:
                    try:
                        await websocket.send_json({"type": "tts_audio_complete"})
                    except Exception as e:
                        logger.warning(f"Failed to send tts_audio_complete to websocket: {e}")
                waited_for_browser_playback = True
                await self.media_gateway.wait_for_playback_complete(call_id)
            
            # Unmute STT microphone after TTS (with small delay to prevent echo)
            if did_mute_stt and hasattr(self.stt_provider, 'unmute'):
                await asyncio.sleep(0.05 if waited_for_browser_playback else 0.3)
                await self.stt_provider.unmute(call_id)
                logger.debug(f"Unmuted STT for call {call_id} after TTS")
        
        if was_interrupted:
            logger.info(
                "tts_stopped_early",
                extra={
                    "call_id": call_id,
                    "turn_id": effective_turn_id,
                    "reason": "barge_in"
                }
            )
        else:
            logger.info(
                "tts_complete",
                extra={
                    "call_id": call_id,
                    "turn_id": effective_turn_id
                }
            )
        return was_interrupted
    
    async def stop_pipeline(self, call_id: str) -> None:
        """
        Stop the voice pipeline for a call.
        
        Args:
            call_id: Call identifier
        """
        logger.info(
            f"Stopping voice pipeline for call {call_id}",
            extra={"call_id": call_id}
        )
        
        self._active_pipelines.pop(call_id, None)
        self._barge_in_events.pop(call_id, None)
        self._active_turn_tasks.pop(call_id, None)
    
    def is_pipeline_active(self, call_id: str) -> bool:
        """
        Check if pipeline is active for a call.
        
        Args:
            call_id: Call identifier
            
        Returns:
            True if pipeline is active
        """
        return self._active_pipelines.get(call_id, False)
