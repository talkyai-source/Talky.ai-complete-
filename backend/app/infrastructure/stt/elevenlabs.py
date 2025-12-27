"""
ElevenLabs Scribe v2 Realtime STT Provider
Uses WebSocket-based real-time speech-to-text transcription
Based on official ElevenLabs documentation
"""
import os
import json
import base64
import asyncio
import websockets
import logging
from typing import AsyncIterator, Optional

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import TranscriptChunk, AudioChunk

logger = logging.getLogger(__name__)


class ElevenLabsSTTProvider(STTProvider):
    """
    ElevenLabs Scribe v2 Realtime STT provider with ultra-low latency
    Uses WebSocket for real-time streaming transcription
    """
    
    def __init__(self):
        self._api_key: Optional[str] = None
        self._config: dict = {}
        self._model: str = "scribe_v2_realtime"
        self._sample_rate: int = 16000
        self._language_code: str = "en"
        
    async def initialize(self, config: dict) -> None:
        """Initialize ElevenLabs STT client with configuration"""
        self._config = config
        self._api_key = config.get("api_key") or os.getenv("ELEVENLABS_API_KEY")
        
        if not self._api_key:
            raise ValueError("ELEVENLABS_API_KEY not set")
        
        self._model = config.get("model", "scribe_v2_realtime")
        self._sample_rate = config.get("sample_rate", 16000)
        self._language_code = config.get("language_code", "en")
        
        logger.info(f"ElevenLabs STT initialized: model={self._model}, sample_rate={self._sample_rate}")
    
    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio to ElevenLabs Scribe v2 and receive real-time transcriptions
        
        Args:
            audio_stream: Async iterator of audio chunks (PCM 16-bit)
            language: Language code (ISO-639-1)
            context: Optional previous text context for better accuracy
            
        Yields:
            TranscriptChunk: Partial or committed transcripts
        """
        if not self._api_key:
            raise RuntimeError("ElevenLabs API key not set. Call initialize() first.")
        
        # Build WebSocket URL with query parameters
        audio_format = f"pcm_{self._sample_rate}"
        url = (
            f"wss://api.elevenlabs.io/v1/speech-to-text/realtime"
            f"?model_id={self._model}"
            f"&language_code={language}"
            f"&audio_format={audio_format}"
            f"&commit_strategy=vad"
            f"&vad_silence_threshold_secs=1.0"
        )
        
        headers = {"xi-api-key": self._api_key}
        
        # Async queues for communication
        transcript_queue = asyncio.Queue()
        stop_event = asyncio.Event()
        
        async def send_audio(ws):
            """Send audio chunks to WebSocket"""
            first_chunk = True
            try:
                async for audio_chunk in audio_stream:
                    if stop_event.is_set():
                        break
                    
                    # Encode audio as base64
                    audio_base64 = base64.b64encode(audio_chunk.data).decode('utf-8')
                    
                    # Build message
                    msg = {
                        "message_type": "input_audio_chunk",
                        "audio_base_64": audio_base64,
                        "sample_rate": self._sample_rate,
                        "commit": False
                    }
                    
                    # Add previous text context on first chunk if provided
                    if first_chunk and context:
                        msg["previous_text"] = context[:50]  # Best under 50 chars
                        first_chunk = False
                    
                    await ws.send(json.dumps(msg))
                    
            except Exception as e:
                logger.error(f"Error sending audio: {e}")
            finally:
                # Signal end of audio
                stop_event.set()
        
        async def receive_transcripts(ws):
            """Receive transcripts from WebSocket"""
            try:
                async for message in ws:
                    if stop_event.is_set():
                        break
                    
                    data = json.loads(message)
                    msg_type = data.get("message_type", "")
                    
                    if msg_type == "session_started":
                        logger.info(f"ElevenLabs STT session started: {data}")
                    
                    elif msg_type == "partial_transcript":
                        text = data.get("text", "")
                        if text:
                            chunk = TranscriptChunk(
                                text=text,
                                is_final=False,
                                confidence=None
                            )
                            await transcript_queue.put(chunk)
                    
                    elif msg_type == "committed_transcript":
                        text = data.get("text", "")
                        if text:
                            chunk = TranscriptChunk(
                                text=text,
                                is_final=True,
                                confidence=1.0
                            )
                            await transcript_queue.put(chunk)
                        
                        # Also send end-of-turn signal
                        end_chunk = TranscriptChunk(
                            text="",
                            is_final=True,
                            confidence=1.0
                        )
                        await transcript_queue.put(end_chunk)
                    
                    elif msg_type in ["auth_error", "quota_exceeded", "input_error", "error"]:
                        logger.error(f"ElevenLabs STT error: {data}")
                        stop_event.set()
                        break
                        
            except websockets.exceptions.ConnectionClosed:
                logger.info("ElevenLabs WebSocket closed")
            except Exception as e:
                logger.error(f"Error receiving transcripts: {e}")
            finally:
                stop_event.set()
                await transcript_queue.put(None)  # Signal end
        
        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                logger.info("Connected to ElevenLabs STT WebSocket")
                
                # Start send/receive tasks
                send_task = asyncio.create_task(send_audio(ws))
                receive_task = asyncio.create_task(receive_transcripts(ws))
                
                # Yield transcripts from queue
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            transcript_queue.get(),
                            timeout=0.1
                        )
                        
                        if chunk is None:
                            break
                        
                        yield chunk
                        
                    except asyncio.TimeoutError:
                        if stop_event.is_set() and transcript_queue.empty():
                            break
                        continue
                
                # Cleanup
                send_task.cancel()
                receive_task.cancel()
                
        except Exception as e:
            logger.error(f"ElevenLabs STT connection error: {e}")
            raise
    
    def detect_turn_end(self, transcript_chunk: TranscriptChunk) -> bool:
        """
        Detect if user finished speaking based on transcript chunk
        
        Args:
            transcript_chunk: Latest transcript chunk
            
        Returns:
            bool: True if turn ended (empty final chunk from VAD)
        """
        return transcript_chunk.is_final and not transcript_chunk.text
    
    async def cleanup(self) -> None:
        """Release resources"""
        self._api_key = None
        logger.info("ElevenLabs STT cleaned up")
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "elevenlabs-scribe"
    
    def __repr__(self) -> str:
        return f"ElevenLabsSTTProvider(model={self._model}, sample_rate={self._sample_rate})"
