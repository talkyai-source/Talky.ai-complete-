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
        media_gateway: MediaGateway
    ):
        self.stt_provider = stt_provider
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider
        self.media_gateway = media_gateway
        
        # Day 5: Conversation management components
        self.prompt_manager = PromptManager()
        # Note: ConversationEngine is initialized per-session with agent_config
        
        # Day 10: Transcript accumulation service
        self.transcript_service = TranscriptService()
        
        # Day 17: LLM guardrails for timeout and fallback handling
        self.guardrails = get_guardrails()
        
        self._active_pipelines: dict[str, bool] = {}
        
        # Barge-in: Track interruption events per call
        # When set, TTS should stop immediately
        self._barge_in_events: dict[str, asyncio.Event] = {}
    
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
            self._active_pipelines[call_id] = False
            session.stt_active = False
            
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
        
        logger.info(
            f"Processing audio stream for call {call_id}",
            extra={"call_id": call_id}
        )
        
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
                        sample_rate=16000,  # STT input is 16kHz (Deepgram Flux)
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
        try:
            async for transcript in self.stt_provider.stream_transcribe(
                audio_stream(),
                language="en"
            ):
                await self.handle_transcript(session, transcript, websocket)
        
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
        
        # Log transcript with structured data
        logger.info(
            "transcript_received",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "timestamp": datetime.utcnow().isoformat(),
                "text": transcript.text,
                "is_final": transcript.is_final,
                "confidence": transcript.confidence
            }
        )
        
        # Send transcript to WebSocket for browser display
        if websocket and transcript.text:
            try:
                await websocket.send_json({
                    "type": "transcript",
                    "text": transcript.text,
                    "is_final": transcript.is_final,
                    "confidence": transcript.confidence
                })
            except Exception as e:
                logger.warning(f"Failed to send transcript to websocket: {e}")
        
        # Check for turn end
        if self.stt_provider.detect_turn_end(transcript):
            # User finished speaking
            await self.handle_turn_end(session, websocket)
        
        elif transcript.text:
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
            
            # Add assistant message to conversation
            assistant_message = Message(
                role=MessageRole.ASSISTANT,
                content=response_text
            )
            session.conversation_history.append(assistant_message)
            
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
                from app.api.v1.dependencies import get_supabase
                supabase = get_supabase()  # Not a generator, call directly
                await self.transcript_service.flush_to_database(
                    call_id=call_id,
                    supabase_client=supabase,
                    tenant_id=getattr(session, 'tenant_id', None)
                )
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
        
        # Initialize conversation engine if needed (using agent_config from session)
        if session.agent_config:
            conversation_engine = ConversationEngine(session.agent_config)
        else:
            # Fallback: use basic response without state machine
            logger.warning(
                f"No agent_config in session {call_id}, using basic LLM response",
                extra={"call_id": call_id}
            )
            return await self._get_basic_llm_response(session)
        
        # 1. Process user input through conversation engine
        new_state, llm_instruction, detected_intent = await conversation_engine.handle_user_input(
            current_state=session.conversation_state,
            user_text=user_input,
            conversation_history=session.conversation_history,
            context=session.conversation_context
        )
        
        # Update session state
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
        
        # 2. Generate state-aware system prompt
        system_prompt = self.prompt_manager.render_system_prompt(
            agent_config=session.agent_config,
            state=new_state,
            # Pass state-specific context
            greeting_context=f"I'm calling to {session.agent_config.get_goal_description()}",
            qualification_instruction=llm_instruction,
            user_concern=user_input if detected_intent.value in ["uncertain", "objection"] else None,
            objection_count=session.conversation_context.objection_count,
            max_objections=session.agent_config.flow.max_objection_attempts,
            confirmation_details=getattr(session, 'confirmation_details', None)
        )
        
        # 3. Prepare conversation history with context window management
        # Keep only last N messages to stay within token limits (Groq recommendation: <2000 tokens for system)
        max_history_messages = 10
        recent_history = session.conversation_history[-max_history_messages:] if len(session.conversation_history) > max_history_messages else session.conversation_history
        
        # 4. Implement assistant prefilling for direct responses (Groq best practice)
        # This skips unnecessary preambles like "Sure!" or "Of course!"
        messages_with_prefill = recent_history.copy()
        
        # Add assistant prefill based on state to enforce direct responses
        prefill_content = self._get_prefill_for_state(new_state)
        if prefill_content:
            messages_with_prefill.append(Message(
                role=MessageRole.ASSISTANT,
                content=prefill_content
            ))
        
        # 5. Stream LLM response with timeout and guardrails
        response_text = prefill_content if prefill_content else ""
        use_fallback = False
        
        # Use global config for LLM parameters
        from app.domain.services.global_ai_config import get_global_config
        global_config = get_global_config()
        
        # Enhanced debug logging for empty response diagnosis
        logger.info(f"[LLM DEBUG] Preparing LLM call for {call_id}")
        logger.info(f"[LLM DEBUG] System prompt length: {len(system_prompt)} chars")
        logger.info(f"[LLM DEBUG] Messages with prefill count: {len(messages_with_prefill)}")
        logger.info(f"[LLM DEBUG] Global config - model: {global_config.llm_model}, temp: {global_config.llm_temperature}, max_tokens: {global_config.llm_max_tokens}")
        
        # Log the last message (user input) for context
        if messages_with_prefill:
            last_msg = messages_with_prefill[-1]
            logger.info(f"[LLM DEBUG] Last message role: {last_msg.role.value}, content: '{last_msg.content[:100] if last_msg.content else 'EMPTY'}...'")
        
        try:
            # Use timeout-enabled streaming for graceful degradation
            async for token in self.llm_provider.stream_chat_with_timeout(
                messages=messages_with_prefill,
                timeout_seconds=self.guardrails.config.max_response_time_seconds,
                system_prompt=system_prompt,
                temperature=global_config.llm_temperature,  # From AI Options
                max_tokens=global_config.llm_max_tokens,    # From AI Options
                top_p=1.0,        # Groq recommendation
                stop=["###", "\n\n\n"]  # Stop sequences for concise responses
            ):
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
                state=new_state,
                call_id=call_id,
                error_count=session.conversation_context.llm_error_count
            )
            response_text = fallback_response
            session.current_ai_response = fallback_response
            
            if should_end:
                # Too many errors - end gracefully
                logger.warning(
                    "max_llm_errors_graceful_goodbye",
                    extra={"call_id": call_id, "error_count": session.conversation_context.llm_error_count}
                )
                session.state = CallState.ENDING
                session.conversation_context.set_outcome(
                    CallOutcomeType.ERROR, 
                    "max_llm_errors"
                )
        
        # 6. Check if conversation should end
        should_end, end_reason = conversation_engine.should_end_conversation(
            state=new_state,
            turn_count=session.turn_id,
            context=session.conversation_context
        )
        
        if should_end:
            # Determine outcome for QA tracking
            outcome = conversation_engine.determine_outcome(
                final_state=new_state,
                context=session.conversation_context,
                turn_count=session.turn_id
            )
            
            logger.info(
                "conversation_ending",
                extra={
                    "call_id": call_id,
                    "reason": end_reason,
                    "final_state": new_state.value,
                    "outcome": outcome.value
                }
            )
            # Mark session for termination
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
        
        logger.info(
            "tts_start",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "text": text
            }
        )
        
        # Create barge-in event for this call to track interruptions
        barge_in_event = asyncio.Event()
        self._barge_in_events[call_id] = barge_in_event
        
        was_interrupted = False
        
        # Use session voice_id if available, otherwise fall back to global config
        from app.domain.services.global_ai_config import get_global_config
        global_config = get_global_config()
        
        # Priority: session.voice_id > global_config.tts_voice_id
        voice_id = getattr(session, 'voice_id', None) or global_config.tts_voice_id
        sample_rate = global_config.tts_sample_rate
        
        logger.debug(f"TTS using voice_id={voice_id}, sample_rate={sample_rate}")
        
        try:
            # Stream TTS synthesis with barge-in awareness
            async for audio_chunk in self.tts_provider.stream_synthesize(
                text=text,
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
        
        finally:
            # Flush any remaining buffered audio
            if hasattr(self.media_gateway, 'flush_audio_buffer'):
                await self.media_gateway.flush_audio_buffer(call_id)
            
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
        
        self._active_pipelines[call_id] = False
    
    def is_pipeline_active(self, call_id: str) -> bool:
        """
        Check if pipeline is active for a call.
        
        Args:
            call_id: Call identifier
            
        Returns:
            True if pipeline is active
        """
        return self._active_pipelines.get(call_id, False)
