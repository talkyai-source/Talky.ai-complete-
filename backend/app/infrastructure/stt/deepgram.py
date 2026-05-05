"""
Deepgram STT Provider Implementation
Standard Deepgram STT using v2 API (non-Flux)
"""
import os
import asyncio
from typing import AsyncIterator, Dict, Optional
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV2SocketClientResponse
from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import TranscriptChunk, AudioChunk
from app.infrastructure.providers.key_pool import KeyPool, parse_keys_csv
from app.infrastructure.providers.provider_concurrency import get_provider_guard
from app.infrastructure.tts.elevenlabs_tts import _SingleKeyLease


class DeepgramSTT(STTProvider):
    """Deepgram Speech-to-Text implementation using v2 API"""

    def __init__(self):
        self._client: Optional[AsyncDeepgramClient] = None
        self._connection = None
        self._config: dict = {}
        self._model: str = "nova-2"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
        self._clients_by_key: Dict[str, AsyncDeepgramClient] = {}
        self._pool: Optional[KeyPool] = None
        self._guard = get_provider_guard("deepgram")
        self._primary_key: Optional[str] = None

    async def initialize(self, config: dict) -> None:
        """Initialize Deepgram client"""
        self._config = config

        pool_keys = parse_keys_csv(os.getenv("DEEPGRAM_API_KEYS"))
        single_key = config.get("api_key") or os.getenv("DEEPGRAM_API_KEY")
        if pool_keys and not config.get("api_key"):
            self._pool = KeyPool("deepgram", pool_keys)
            self._primary_key = pool_keys[0]
        else:
            self._pool = None
            self._primary_key = single_key

        if not self._primary_key:
            raise ValueError("Deepgram API key not found in config or environment")

        self._client = self._client_for(self._primary_key)

        # Configuration
        self._model = config.get("model", "nova-2")
        self._sample_rate = config.get("sample_rate", 16000)
        self._encoding = config.get("encoding", "linear16")

    def _client_for(self, api_key: str) -> AsyncDeepgramClient:
        client = self._clients_by_key.get(api_key)
        if client is None:
            client = AsyncDeepgramClient(api_key=api_key)
            self._clients_by_key[api_key] = client
        return client
    
    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn=None,
        on_barge_in=None,
    ) -> AsyncIterator[TranscriptChunk]:
        """Stream transcription from Deepgram using v2 API"""
        if not self._client:
            raise RuntimeError("Deepgram client not initialized. Call initialize() first.")
        
        # Create a queue to bridge callback -> generator
        response_queue: asyncio.Queue = asyncio.Queue()

        # Concurrency guard wraps the entire streaming session — each call
        # holds one slot for its lifetime. Pool selects the key.
        async with self._guard.acquire():
            key_ctx = (
                self._pool.acquire() if self._pool is not None
                else _SingleKeyLease(self._primary_key or "")
            )
            async with key_ctx as lease:
                chosen_client = (
                    self._client_for(lease.key)
                    if self._pool is not None and lease.key
                    else self._client
                )
                try:
                    async with chosen_client.listen.v2.connect(
                        model=self._model,
                        encoding=self._encoding,
                        sample_rate=str(self._sample_rate),
                        language=language,
                    ) as connection:
                        self._connection = connection

                        def on_message(message: ListenV2SocketClientResponse) -> None:
                            if hasattr(message, "transcript") and message.transcript:
                                confidence = None
                                if hasattr(message, "words") and message.words:
                                    confidences = [
                                        w.confidence for w in message.words
                                        if hasattr(w, "confidence")
                                    ]
                                    if confidences:
                                        confidence = sum(confidences) / len(confidences)
                                chunk = TranscriptChunk(
                                    text=message.transcript,
                                    is_final=True,
                                    confidence=confidence,
                                )
                                try:
                                    response_queue.put_nowait(chunk)
                                except asyncio.QueueFull:
                                    pass

                        def on_error(error) -> None:
                            print(f"Deepgram error: {error}")

                        connection.on(EventType.OPEN, lambda _: None)
                        connection.on(EventType.MESSAGE, on_message)
                        connection.on(EventType.CLOSE, lambda _: None)
                        connection.on(EventType.ERROR, on_error)

                        listen_task = asyncio.create_task(connection.start_listening())

                        async def send_audio():
                            try:
                                async for audio_chunk in audio_stream:
                                    await connection._send(audio_chunk.data)
                            except Exception as e:
                                print(f"Error sending audio: {e}")

                        sender_task = asyncio.create_task(send_audio())

                        try:
                            while True:
                                try:
                                    chunk = await asyncio.wait_for(
                                        response_queue.get(), timeout=0.1
                                    )
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

                    lease.report_success()

                except Exception as e:
                    lease.report_failure(retryable=True)
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
