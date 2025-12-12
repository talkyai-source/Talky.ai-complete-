"""
Voice Pipeline Service
Orchestrates the full voice AI pipeline: STT → LLM → TTS
Integrates conversation state machine and prompt management (Day 5)
"""
import asyncio
import logging
from typing import Optional, AsyncIterator
from datetime import datetime

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import AudioChunk, TranscriptChunk, Message, MessageRole
from app.domain.models.conversation_state import ConversationState
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.domain.interfaces.media_gateway import MediaGateway
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.services.prompt_manager import PromptManager
from app.domain.services.transcript_service import TranscriptService

logger = logging.getLogger(__name__)


class VoicePipelineService:
    """
    Orchestrates the full voice AI pipeline.
    
    Pipeline Flow:
    1. Audio Queue (from media gateway)
    2. Deepgram Flux STT (streaming transcription)
    3. Turn Detection (EndOfTurn event)
    4. Groq LLM (streaming response generation)
    5. Cartesia TTS (streaming audio synthesis)
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
        stt_provider: DeepgramFluxSTTProvider,
        llm_provider: GroqLLMProvider,
        tts_provider: CartesiaTTSProvider,
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
        
        self._active_pipelines: dict[str, bool] = {}
    
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
                        timeout=0.1
                    )
                    
                    yield AudioChunk(
                        data=audio_data,
                        sample_rate=16000,
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
        transcript: TranscriptChunk,
        websocket: Optional[WebSocket] = None
    ) -> None:
        """
        Handle transcript chunk from STT.
        
        Args:
            session: Active call session
            transcript: Transcript chunk from STT
            websocket: Optional WebSocket for updates
        """
        call_id = session.call_id
        
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
        
        # Check for turn end
        if self.stt_provider.detect_turn_end(transcript):
            # User finished speaking
            await self.handle_turn_end(session, websocket)
        
        elif transcript.text:
            # Accumulate partial transcript
            session.current_user_input += transcript.text + " "
            
            # Update session activity
            session.update_activity()
    
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
        
        # 5. Stream LLM response with optimized parameters
        response_text = prefill_content if prefill_content else ""
        
        try:
            async for token in self.llm_provider.stream_chat(
                messages=messages_with_prefill,
                system_prompt=system_prompt,
                temperature=0.3,  # Groq recommendation for factual voice calls (0.2-0.4)
                max_tokens=150,   # Enforce brevity for voice
                top_p=1.0,        # Groq recommendation
                stop=["###", "\n\n\n"]  # Stop sequences for concise responses
            ):
                response_text += token
                session.current_ai_response += token
            
            # 6. Check if conversation should end
            should_end, end_reason = conversation_engine.should_end_conversation(
                state=new_state,
                turn_count=session.turn_id,
                context=session.conversation_context
            )
            
            if should_end:
                logger.info(
                    "conversation_ending",
                    extra={
                        "call_id": call_id,
                        "reason": end_reason,
                        "final_state": new_state.value
                    }
                )
                # Mark session for termination
                session.state = CallState.ENDING
        
        except Exception as e:
            logger.error(
                f"Error in conversation-aware LLM response: {e}",
                extra={"call_id": call_id, "error": str(e)},
                exc_info=True
            )
            # Fallback to basic response
            return await self._get_basic_llm_response(session)
        
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
        
        # Stream TTS synthesis
        async for audio_chunk in self.tts_provider.stream_synthesize(
            text=text,
            voice_id=session.voice_id,
            sample_rate=16000  # Match Vonage format
        ):
            # Send audio to media gateway
            await self.media_gateway.send_audio(
                call_id,
                audio_chunk.data
            )
        
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
