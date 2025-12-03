"""
Deepgram STT Provider Implementation
Standard Deepgram STT using v2 API (non-Flux)
"""
import os
import asyncio
from typing import AsyncIterator, Optional
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV2SocketClientResponse
from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import TranscriptChunk, AudioChunk


class DeepgramSTT(STTProvider):
    """Deepgram Speech-to-Text implementation using v2 API"""
    
    def __init__(self):
        self._client: Optional[AsyncDeepgramClient] = None
        self._connection = None
        self._config: dict = {}
        self._model: str = "nova-2"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
    
    async def initialize(self, config: dict) -> None:
        """Initialize Deepgram client"""
        self._config = config
        api_key = config.get("api_key") or os.getenv("DEEPGRAM_API_KEY")
        
        if not api_key:
            raise ValueError("Deepgram API key not found in config or environment")
        
        # Initialize async client
        self._client = AsyncDeepgramClient(api_key=api_key)
        
        # Configuration
        self._model = config.get("model", "nova-2")
        self._sample_rate = config.get("sample_rate", 16000)
        self._encoding = config.get("encoding", "linear16")
    
    async def stream_transcribe(
        self, 
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """Stream transcription from Deepgram using v2 API"""
        if not self._client:
            raise RuntimeError("Deepgram client not initialized. Call initialize() first.")
        
        # Create a queue to bridge callback -> generator
        response_queue: asyncio.Queue = asyncio.Queue()
        
        try:
            # Connect to Deepgram using v2 endpoint
            async with self._client.listen.v2.connect(
                model=self._model,
                encoding=self._encoding,
                sample_rate=str(self._sample_rate),
                language=language
            ) as connection:
                self._connection = connection
                
                # Define message handler function
                def on_message(message: ListenV2SocketClientResponse) -> None:
                    # Handle transcript results
                    if hasattr(message, 'transcript') and message.transcript:
                        confidence = None
                        if hasattr(message, 'words') and message.words:
                            confidences = [w.confidence for w in message.words if hasattr(w, 'confidence')]
                            if confidences:
                                confidence = sum(confidences) / len(confidences)
                        
                        chunk = TranscriptChunk(
                            text=message.transcript,
                            is_final=True,
                            confidence=confidence
                        )
                        
                        try:
                            response_queue.put_nowait(chunk)
                        except asyncio.QueueFull:
                            pass

                def on_error(error) -> None:
                    print(f"Deepgram error: {error}")

                # Set up event handlers
                connection.on(EventType.OPEN, lambda _: None)
                connection.on(EventType.MESSAGE, on_message)
                connection.on(EventType.CLOSE, lambda _: None)
                connection.on(EventType.ERROR, on_error)
                
                # Start the connection listening
                listen_task = asyncio.create_task(connection.start_listening())
                
                # Audio sender task
                async def send_audio():
                    try:
                        async for audio_chunk in audio_stream:
                            await connection._send(audio_chunk.data)
                    except Exception as e:
                        print(f"Error sending audio: {e}")

                sender_task = asyncio.create_task(send_audio())
                
                # Yield results from queue
                try:
                    while True:
                        try:
                            chunk = await asyncio.wait_for(response_queue.get(), timeout=0.1)
                            yield chunk
                        except asyncio.TimeoutError:
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
                    
                    try:
                        await asyncio.wait_for(listen_task, timeout=2.0)
                    except asyncio.TimeoutError:
                        listen_task.cancel()
        
        except Exception as e:
            raise RuntimeError(f"Deepgram transcription failed: {str(e)}")
    
    async def detect_turn_end(self) -> bool:
        """Detect turn end using VAD"""
        return False
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
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
        return "deepgram"
