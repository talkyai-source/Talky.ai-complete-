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
    ):
        self.stt_provider = stt_provider
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider
        self.media_gateway = media_gateway
        self.stt_sample_rate = stt_sample_rate
        self.tts_sample_rate = tts_sample_rate
        
        # Day 5: Conversation management components
        self.prompt_manager = PromptManager()
        # Note: ConversationEngine is initialized per-session with agent_config
        
        # Day 10: Transcript accumulation service
        self.transcript_service = TranscriptService()
        
        # Day 17: LLM guardrails for timeout and fallback handling
        self.guardrails = get_guardrails()
        self.latency_tracker = get_latency_tracker()
        
        self._active_pipelines: dict[str, bool] = {}
        
        # Barge-in: Track interruption events per call
        # When set, TTS should stop immediately
        self._barge_in_events: dict[str, asyncio.Event] = {}
        
        # EagerEndOfTurn: Track speculative LLM tasks for cancellation on TurnResumed
        self._pending_llm_tasks: dict[str, asyncio.Task] = {}
    
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
            self._active_pipelines.pop(call_id, None)
            self._barge_in_events.pop(call_id, None)
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
        
        # Signal TTS to stop if it's currently playing
        if call_id in self._barge_in_events:
            self._barge_in_events[call_id].set()
            logger.info(f"Barge-in event set for call {call_id}")
        
        # Cancel current AI response (it was interrupted)
        session.current_ai_response = ""
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
        
        logger.info(
            "turn_end",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "transcript": full_transcript,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        
        # Update session state
        session.state = CallState.PROCESSING
        session.llm_active = True
        self.latency_tracker.mark_speech_end(call_id)
        self.latency_tracker.mark_llm_start(call_id)
        
        # Add user message to conversation
        user_message = Message(
            role=MessageRole.USER,
            content=full_transcript
        )
        session.conversation_history.append(user_message)
        
        # Day 10: Accumulate user turn for transcript
        self.transcript_service.accumulate_turn(
            call_id=call_id,
            role="user",
            content=full_transcript
        )
        
        try:
            # Get LLM response
            llm_start = datetime.utcnow()
            
            response_text = await self.get_llm_response(
                session,
                full_transcript
            )
            self.latency_tracker.mark_llm_end(call_id)
            
            llm_latency = (datetime.utcnow() - llm_start).total_seconds() * 1000
            session.add_latency_measurement("llm", llm_latency)
            
            logger.info(
                "llm_response",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "response": response_text,
                    "latency_ms": llm_latency
                }
            )
            
            # Add assistant message to conversation (only if non-empty)
            # Empty responses can confuse the LLM on subsequent turns
            if response_text and response_text.strip():
                assistant_message = Message(
                    role=MessageRole.ASSISTANT,
                    content=response_text
                )
                session.conversation_history.append(assistant_message)
            else:
                logger.warning(
                    f"Skipping empty assistant message for call {call_id} - "
                    "this prevents conversation history corruption"
                )
            
            # Send LLM response to WebSocket for browser display
            if websocket:
                try:
                    await websocket.send_json({
                        "type": "llm_response",
                        "text": response_text,
                        "latency_ms": llm_latency
                    })
                except Exception as e:
                    logger.warning(f"Failed to send llm_response to websocket: {e}")
            
            # Day 10: Accumulate assistant turn for transcript
            self.transcript_service.accumulate_turn(
                call_id=call_id,
                role="assistant",
                content=response_text
            )
            
            # Synthesize TTS audio
            session.tts_active = True
            self.latency_tracker.mark_tts_start(call_id)
            
            tts_start = datetime.utcnow()
            
            await self.synthesize_and_send_audio(
                session,
                response_text
            )
            
            tts_latency = (datetime.utcnow() - tts_start).total_seconds() * 1000
            session.add_latency_measurement("tts", tts_latency)
            
            # Calculate total turn latency
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
            
            logger.info(
                "turn_complete",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "llm_latency_ms": llm_latency,
                    "tts_latency_ms": tts_latency,
                    "total_latency_ms": total_latency
                }
            )
            
            # Send turn_complete to WebSocket for browser display
            if websocket:
                try:
                    await websocket.send_json({
                        "type": "turn_complete",
                        "llm_latency_ms": llm_latency,
                        "tts_latency_ms": tts_latency,
                        "total_latency_ms": total_latency
                    })
                except Exception as e:
                    logger.warning(f"Failed to send turn_complete to websocket: {e}")
            
            # Day 17: Flush transcript to database incrementally
            try:
                container = get_container()
                if container.is_initialized:
                    postgres_client = PostgresAdapterClient(container.db_pool)
                    await self.transcript_service.flush_to_database(
                        call_id=call_id,
                        db_client=postgres_client,
                        tenant_id=getattr(session, 'tenant_id', None)
                    )
                else:
                    logger.debug("Skipping transcript flush: container not initialized")
            except Exception as e:
                logger.warning(f"Failed to flush transcript for {call_id}: {e}")
        
        except Exception as e:
            logger.error(
                f"Error processing turn: {e}",
                extra={"call_id": call_id, "error": str(e)},
                exc_info=True
            )
        
        finally:
            # Reset for next turn
            session.increment_turn()
            self.latency_tracker.start_turn(call_id, session.turn_id)
            self.latency_tracker.mark_listening_start(call_id)
            session.state = CallState.LISTENING
            session.llm_active = False
            session.tts_active = False
    
    async def get_llm_response(
        self,
        session: CallSession,
        user_input: str
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
                    "turn_id": session.turn_id,
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
                    "turn_id": session.turn_id,
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
                top_p=1.0,        # Groq recommendation
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
                
                response_text = self.guardrails.truncate_response(
                    response_text, 
                    max_sentences=session.agent_config.response_max_sentences
                )
                logger.info(f"After truncate_response ({len(response_text)} chars): '{response_text[:100] if response_text else 'EMPTY'}...'")
                
                # Validate against rules
                is_valid, reason = self.guardrails.validate_response(
                    response_text, 
                    session.agent_config.rules
                )
                if not is_valid:
                    logger.warning(f"Response validation failed: {reason}, using fallback")
                    session.conversation_context.increment_llm_error()
                    use_fallback = True
        
        except LLMTimeoutError:
            # LLM timeout - use human-like fallback (no AI hints)
            logger.warning(
                "llm_timeout_fallback",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "state": new_state.value
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
                turn_count=session.turn_id,
                context=session.conversation_context
            )
            
            if should_end:
                outcome = conversation_engine.determine_outcome(
                    final_state=session.conversation_state,
                    context=session.conversation_context,
                    turn_count=session.turn_id
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
        """
        Clean text for TTS by removing markdown, special characters, and emojis.
        
        This ensures the voice output sounds natural and conversational,
        without speaking out asterisks, hashes, and other formatting marks.
        
        Args:
            text: Raw text from LLM
            
        Returns:
            Cleaned text suitable for voice synthesis
        """
        import re
        
        if not text:
            return text
        
        # IMPORTANT: Order matters - process complex patterns before simple ones
        # 1. First handle markdown links (before URL removal)
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # 2. Remove code blocks and inline code
        cleaned = re.sub(r'```[\s\S]*?```', ' code block ', cleaned)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
        
        # 3. Remove standalone URLs (after markdown links)
        cleaned = re.sub(r'https?://\S+', ' link ', cleaned)
        cleaned = re.sub(r'www\.\S+', ' website ', cleaned)
        
        # 4. Remove markdown formatting (bold, italic, strikethrough)
        cleaned = re.sub(r'\*\*\*?|\*\*?|__?|~~', '', cleaned)
        
        # 5. Remove headers
        cleaned = re.sub(r'^#{1,6}\s*', '', cleaned, flags=re.MULTILINE)
        
        # 6. Remove blockquotes
        cleaned = re.sub(r'^>\s*', '', cleaned, flags=re.MULTILINE)
        
        # 7. Remove emojis (comprehensive range)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags
            "\U00002702-\U000027B0"  # dingbats
            "\U000024C2-\U0001F251"  # enclosed characters
            "\U0001F900-\U0001F9FF"  # supplemental symbols
            "\U00002600-\U000026FF"  # miscellaneous symbols
            "]+",
            flags=re.UNICODE
        )
        cleaned = emoji_pattern.sub('', cleaned)
        
        # 8. Replace common symbols with spoken equivalents
        replacements = {
            '&': ' and ',
            '@': ' at ',
            '#': ' number ',
            '$': ' dollars ',
            '%': ' percent ',
            '+': ' plus ',
            '=': ' equals ',
            '→': ' to ',
            '←': ' from ',
            '•': ', ',
            '·': ', ',
            '…': '...',
            '|': ', ',
            '™': ' trademark ',
            '®': ' registered ',
            '©': ' copyright ',
            '°': ' degrees ',
            '×': ' times ',
            '÷': ' divided by ',
            '–': '-',  # en-dash to hyphen
            '—': '-',  # em-dash to hyphen
        }
        for symbol, spoken in replacements.items():
            cleaned = cleaned.replace(symbol, spoken)
        
        # 9. Remove bullet points and list markers at start of lines
        cleaned = re.sub(r'^[\s]*[-*+•]\s+', '', cleaned, flags=re.MULTILINE)
        
        # 10. Normalize whitespace (multiple spaces -> single space)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        
        # 11. Remove excessive punctuation
        cleaned = re.sub(r'!{2,}', '!', cleaned)  # !!! -> !
        cleaned = re.sub(r'\?{2,}', '?', cleaned)  # ??? -> ?
        cleaned = re.sub(r'\.{3,}', '...', cleaned)  # .... -> ...
        
        # 12. Trim whitespace
        cleaned = cleaned.strip()
        
        return cleaned

    async def synthesize_and_send_audio(
        self,
        session: CallSession,
        text: str
    ) -> None:
        """
        Synthesize text to speech and send to output queue.
        
        Supports barge-in: if user starts speaking during TTS,
        the synthesis is interrupted immediately.
        
        Args:
            session: Active call session
            text: Text to synthesize
        """
        call_id = session.call_id
        
        # Clean text for TTS (remove markdown, emojis, etc.)
        cleaned_text = self._clean_text_for_tts(text)
        
        logger.info(
            "tts_start",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "original_text": text,
                "cleaned_text": cleaned_text
            }
        )
        
        # Create barge-in event for this call to track interruptions
        barge_in_event = asyncio.Event()
        self._barge_in_events[call_id] = barge_in_event
        
        was_interrupted = False
        
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
        
        try:
            # Mute STT microphone during TTS to prevent echo detection
            # This prevents the agent's voice from triggering StartOfTurn
            if hasattr(self.stt_provider, 'mute'):
                await self.stt_provider.mute(call_id)
                logger.debug(f"Muted STT for call {call_id} during TTS")
            
            # Stream TTS synthesis with barge-in awareness
            # Use cleaned text (no markdown, emojis, etc.)
            first_audio_chunk_sent = False
            async for audio_chunk in self.tts_provider.stream_synthesize(
                text=cleaned_text,
                voice_id=voice_id,
                sample_rate=sample_rate
            ):
                # Check for barge-in before sending each chunk
                if barge_in_event.is_set():
                    logger.info(
                        "tts_interrupted_by_barge_in",
                        extra={
                            "call_id": call_id,
                            "turn_id": session.turn_id
                        }
                    )
                    was_interrupted = True
                    break
                
                # Send audio to media gateway
                await self.media_gateway.send_audio(
                    call_id,
                    audio_chunk.data
                )
                if not first_audio_chunk_sent:
                    first_audio_chunk_sent = True
                    self.latency_tracker.mark_tts_first_chunk(call_id)
                    self.latency_tracker.mark_response_start(call_id)
        
        finally:
            self.latency_tracker.mark_tts_end(call_id)
            # Flush any remaining buffered audio
            if hasattr(self.media_gateway, 'flush_audio_buffer'):
                await self.media_gateway.flush_audio_buffer(call_id)
            
            # Unmute STT microphone after TTS (with small delay to prevent echo)
            if hasattr(self.stt_provider, 'unmute'):
                await asyncio.sleep(0.3)  # 300ms delay to let audio finish playing
                await self.stt_provider.unmute(call_id)
                logger.debug(f"Unmuted STT for call {call_id} after TTS")
            
            # Clean up barge-in event
            if call_id in self._barge_in_events:
                del self._barge_in_events[call_id]
        
        if was_interrupted:
            logger.info(
                "tts_stopped_early",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "reason": "barge_in"
                }
            )
        else:
            logger.info(
                "tts_complete",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id
                }
            )
    
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
    
    def is_pipeline_active(self, call_id: str) -> bool:
        """
        Check if pipeline is active for a call.
        
        Args:
            call_id: Call identifier
            
        Returns:
            True if pipeline is active
        """
        return self._active_pipelines.get(call_id, False)
