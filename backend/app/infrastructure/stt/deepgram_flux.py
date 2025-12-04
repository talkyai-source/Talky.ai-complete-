"""
Deepgram Flux STT Provider Implementation
Uses Deepgram SDK v5.3.0 with correct API pattern
Based on working example with threading + sync context manager
"""
import os
import asyncio
import threading
import queue
from typing import AsyncIterator, Optional

from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV2SocketClientResponse

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import TranscriptChunk, AudioChunk


class DeepgramFluxSTTProvider(STTProvider):
    """
    Deepgram Flux STT provider with ultra-low latency (~260ms) and 
    intelligent turn detection for voice agents
    
    Uses SDK v5.3.0 API with threading pattern
    """
    
    def __init__(self):
        self._client: Optional[DeepgramClient] = None
        self._config: dict = {}
        self._model: str = "flux-general-en"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
        
    async def initialize(self, config: dict) -> None:
        """Initialize Deepgram Flux client with configuration"""
        self._config = config
        
        # SDK v5 auto-loads API key from DEEPGRAM_API_KEY environment variable
        # No need to pass api_key explicitly
        self._client = DeepgramClient()
        
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
        
        # Queue to bridge sync Deepgram → async generator
        transcript_queue = queue.Queue()
        stop_event = threading.Event()
        error_container = []
        
        def sync_transcribe():
            """Run Deepgram in sync mode with threading (SDK v5 pattern)"""
            try:
                # Connect using SDK v5 pattern (sync context manager)
                with self._client.listen.v2.connect(
                    model=self._model,
                    encoding=self._encoding,
                    sample_rate=self._sample_rate
                ) as connection:
                    
                    # Event handler for messages
                    def on_message(message: ListenV2SocketClientResponse) -> None:
                        try:
                            if hasattr(message, 'type'):
                                # Handle turn detection events
                                if message.type == 'TurnInfo':
                                    event = getattr(message, 'event', None)
                                    
                                    if event == 'EndOfTurn':
                                        # Signal end of turn with empty final chunk
                                        chunk = TranscriptChunk(
                                            text="",
                                            is_final=True,
                                            confidence=1.0
                                        )
                                        transcript_queue.put(chunk)
                                    
                                    elif event == 'StartOfTurn':
                                        # User started speaking
                                        pass
                                
                                # Handle transcript results
                                elif message.type == 'Results':
                                    if hasattr(message, 'channel') and message.channel.alternatives:
                                        alt = message.channel.alternatives[0]
                                        if alt.transcript:
                                            # Calculate confidence
                                            confidence = None
                                            if hasattr(alt, 'confidence'):
                                                confidence = alt.confidence
                                            
                                            chunk = TranscriptChunk(
                                                text=alt.transcript,
                                                is_final=True,  # Flux provides final results
                                                confidence=confidence
                                            )
                                            transcript_queue.put(chunk)
                        
                        except Exception as e:
                            error_container.append(e)
                    
                    # Register event handler
                    connection.on(EventType.MESSAGE, on_message)
                    
                    # Start listening in background thread (SDK v5 pattern)
                    listen_thread = threading.Thread(
                        target=connection.start_listening,
                        daemon=True
                    )
                    listen_thread.start()
                    
                    # Send audio chunks
                    # Note: We need to consume the async iterator in a sync way
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    async def send_audio():
                        try:
                            async for audio_chunk in audio_stream:
                                if stop_event.is_set():
                                    break
                                # Use send_media() method (SDK v5)
                                connection.send_media(audio_chunk.data)
                        except Exception as e:
                            error_container.append(e)
                    
                    loop.run_until_complete(send_audio())
                    loop.close()
                    
                    # Wait a bit for final messages
                    threading.Event().wait(0.5)
            
            except Exception as e:
                error_container.append(e)
            finally:
                # Signal completion
                transcript_queue.put(None)
        
        # Run sync Deepgram code in background thread
        transcribe_thread = threading.Thread(target=sync_transcribe, daemon=True)
        transcribe_thread.start()
        
        # Yield transcripts from queue (async generator)
        try:
            while True:
                # Check for errors
                if error_container:
                    raise RuntimeError(f"Deepgram transcription failed: {error_container[0]}")
                
                # Get transcript from queue (with timeout)
                try:
                    chunk = await asyncio.get_event_loop().run_in_executor(
                        None,
                        transcript_queue.get,
                        True,  # block
                        0.1    # timeout
                    )
                    
                    if chunk is None:
                        # End of stream
                        break
                    
                    yield chunk
                
                except queue.Empty:
                    # Check if thread is still alive
                    if not transcribe_thread.is_alive():
                        break
                    continue
        
        finally:
            stop_event.set()
    
    def detect_turn_end(self, transcript_chunk: TranscriptChunk) -> bool:
        """
        Detect if the user has finished speaking based on transcript chunk
        
        Args:
            transcript_chunk: Latest transcript chunk
            
        Returns:
            bool: True if turn has ended, False otherwise
        """
        # Check if this is an end-of-turn signal (empty final chunk)
        return transcript_chunk.is_final and not transcript_chunk.text
    
    async def cleanup(self) -> None:
        """Release resources"""
        # SDK v5 handles cleanup automatically via context manager
        self._client = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "deepgram-flux"
    
    def __repr__(self) -> str:
        return f"DeepgramFluxSTTProvider(model={self._model}, sample_rate={self._sample_rate})"
