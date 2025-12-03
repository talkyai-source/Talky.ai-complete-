"""
Deepgram Flux STT Provider Implementation
Ultra-low latency STT with intelligent turn detection for voice agents
Based on official Deepgram Flux documentation
"""
import os
import asyncio
from typing import AsyncIterator, Optional
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV2SocketClientResponse
from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import TranscriptChunk, AudioChunk


class DeepgramFluxSTTProvider(STTProvider):
    """
    Deepgram Flux STT provider with ultra-low latency (~260ms) and 
    intelligent turn detection for voice agents
    """
    
    def __init__(self):
        self._client: Optional[AsyncDeepgramClient] = None
        self._connection = None
        self._config: dict = {}
        self._model: str = "flux-general-en"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
        self._turn_ended: bool = False
        self._eager_turn_ended: bool = False
        
    async def initialize(self, config: dict) -> None:
        """Initialize Deepgram Flux client with configuration"""
        self._config = config
        api_key = config.get("api_key") or os.getenv("DEEPGRAM_API_KEY")
        
        if not api_key:
            raise ValueError("Deepgram API key not found in config or environment")
        
        # Initialize async client
        self._client = AsyncDeepgramClient(api_key=api_key)
        
        # Configuration
        self._model = config.get("model", "flux-general-en")
        self._sample_rate = config.get("sample_rate", 16000)
        self._encoding = config.get("encoding", "linear16")
    
    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio to Deepgram Flux and receive real-time transcriptions
        
        Args:
            audio_stream: Async iterator of audio chunks
            language: Language code (embedded in model name for Flux)
            context: Optional context for better accuracy
            
        Yields:
            TranscriptChunk: Partial or final transcripts with turn detection
        """
        if not self._client:
            raise RuntimeError("Deepgram client not initialized. Call initialize() first.")
        
        # Reset turn detection flags
        self._turn_ended = False
        self._eager_turn_ended = False
        
        # Create a queue to bridge callback -> generator
        response_queue: asyncio.Queue = asyncio.Queue()
        
        try:
            # Connect to Deepgram Flux using v2 endpoint
            # SDK automatically connects to: wss://api.deepgram.com/v2/listen
            async with self._client.listen.v2.connect(
                model=self._model,
                encoding=self._encoding,
                sample_rate=str(self._sample_rate)
            ) as connection:
                self._connection = connection
                
                # Define message handler function (following official docs pattern)
                def on_message(message: ListenV2SocketClientResponse) -> None:
                    msg_type = getattr(message, "type", "Unknown")
                    
                    # Handle transcript results - Flux returns transcript directly on message
                    if hasattr(message, 'transcript') and message.transcript:
                        # Get word-level confidence if available
                        confidence = None
                        if hasattr(message, 'words') and message.words:
                            # Calculate average confidence from words
                            confidences = [w.confidence for w in message.words if hasattr(w, 'confidence')]
                            if confidences:
                                confidence = sum(confidences) / len(confidences)
                        
                        chunk = TranscriptChunk(
                            text=message.transcript,
                            is_final=True,  # Flux transcripts are final
                            confidence=confidence
                        )
                        
                        # Put chunk in queue (thread-safe way)
                        try:
                            response_queue.put_nowait(chunk)
                        except asyncio.QueueFull:
                            pass
                    
                    # Handle connection confirmation
                    elif msg_type == "Connected":
                        pass  # Connection established
                    
                    # Handle TurnInfo events (Flux specific turn detection)
                    elif msg_type == "TurnInfo":
                        event = getattr(message, "event", None)
                        
                        if event == "EndOfTurn":
                            self._turn_ended = True
                            # Signal end of turn with empty final chunk
                            chunk = TranscriptChunk(
                                text="",
                                is_final=True,
                                confidence=1.0
                            )
                            try:
                                response_queue.put_nowait(chunk)
                            except asyncio.QueueFull:
                                pass
                                
                        elif event == "EagerEndOfTurn":
                            self._eager_turn_ended = True
                            
                        elif event == "TurnResumed":
                            self._eager_turn_ended = False
                            self._turn_ended = False

                def on_error(error) -> None:
                    print(f"Deepgram error: {error}")

                # Set up event handlers (following official docs pattern)
                connection.on(EventType.OPEN, lambda _: None)
                connection.on(EventType.MESSAGE, on_message)
                connection.on(EventType.CLOSE, lambda _: None)
                connection.on(EventType.ERROR, on_error)
                
                # Start the connection listening in background
                listen_task = asyncio.create_task(connection.start_listening())
                
                # Audio sender task
                async def send_audio():
                    try:
                        async for audio_chunk in audio_stream:
                            # Send audio data using _send method (per official docs)
                            await connection._send(audio_chunk.data)
                            if self._turn_ended:
                                break
                    except Exception as e:
                        print(f"Error sending audio: {e}")

                sender_task = asyncio.create_task(send_audio())
                
                # Yield results from queue
                try:
                    while True:
                        try:
                            chunk = await asyncio.wait_for(response_queue.get(), timeout=0.1)
                            yield chunk
                            
                            # Check for end of turn signal
                            if chunk.is_final and not chunk.text:
                                pass  # Continue for next turn or break based on use case
                                
                        except asyncio.TimeoutError:
                            # Check if sender is done
                            if sender_task.done():
                                if sender_task.exception():
                                    raise sender_task.exception()
                                break
                            continue
                            
                finally:
                    sender_task.cancel()
                    try:
                        await sender_task
                    except asyncio.CancelledError:
                        pass
                    
                    # Wait for listen task to complete
                    try:
                        await asyncio.wait_for(listen_task, timeout=2.0)
                    except asyncio.TimeoutError:
                        listen_task.cancel()
        
        except Exception as e:
            raise RuntimeError(f"Deepgram Flux transcription failed: {str(e)}")
    
    async def detect_turn_end(self) -> bool:
        """
        Detect if the user has finished speaking
        
        Returns:
            bool: True if turn has ended, False otherwise
        """
        return self._turn_ended
    
    def is_eager_turn_end(self) -> bool:
        """
        Check if eager end-of-turn was detected
        This allows starting LLM processing before user fully finishes
        
        Returns:
            bool: True if eager turn end detected
        """
        return self._eager_turn_ended
    
    async def cleanup(self) -> None:
        """Release resources"""
        if self._connection:
            try:
                await self._connection.finish()
            except:
                pass
            self._connection = None
        
        if self._client:
            self._client = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "deepgram-flux"
    
    def __repr__(self) -> str:
        return f"DeepgramFluxSTTProvider(model={self._model}, sample_rate={self._sample_rate})"
