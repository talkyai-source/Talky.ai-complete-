"""
Voice Pipeline Service
Orchestrates the full voice AI pipeline: STT → LLM → TTS
"""
import asyncio
import logging
from typing import Optional, AsyncIterator
from datetime import datetime

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import AudioChunk, TranscriptChunk, Message, MessageRole
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.domain.interfaces.media_gateway import MediaGateway

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
        Get LLM response for user input.
        
        Args:
            session: Active call session
            user_input: User's transcribed speech
            
        Returns:
            LLM response text
        """
        # Stream LLM response
        response_text = ""
        
        async for token in self.llm_provider.stream_chat(
            session.conversation_history,
            session.system_prompt
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
