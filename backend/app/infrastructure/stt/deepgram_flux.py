"""
Deepgram Flux STT Provider Implementation
Uses direct WebSocket connection based on official Flux v2 API.

Flux State Machine Events:
- Update: Transcript update (~every 0.25s)
- StartOfTurn: User started speaking  
- EagerEndOfTurn: Early end-of-turn signal
- TurnResumed: User continued speaking
- EndOfTurn: User definitely finished speaking

Day 17: Added PCM validation before sending audio to Deepgram.

Based on: https://developers.deepgram.com/docs/flux/agent
"""
import os
import json
import base64
import asyncio
import websockets
import logging
from typing import AsyncIterator, Optional

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import TranscriptChunk, AudioChunk, BargeInSignal
from app.utils.audio_utils import validate_pcm_format

logger = logging.getLogger(__name__)


class DeepgramFluxSTTProvider(STTProvider):
    """
    Deepgram Flux STT provider with ultra-low latency and 
    intelligent turn detection for voice agents.
    
    Uses direct WebSocket connection to Deepgram v2 API.
    """
    
    def __init__(self):
        self._api_key: Optional[str] = None
        self._config: dict = {}
        self._model: str = "flux-general-en"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
        
    async def initialize(self, config: dict) -> None:
        """Initialize Deepgram Flux with configuration"""
        self._config = config
        
        # Get API key
        self._api_key = config.get("api_key") or os.getenv("DEEPGRAM_API_KEY")
        if not self._api_key:
            raise ValueError("DEEPGRAM_API_KEY not set")
        
        self._model = config.get("model", "flux-general-en")
        self._sample_rate = config.get("sample_rate", 16000)
        self._encoding = config.get("encoding", "linear16")
        
        logger.info(f"DeepgramFlux initialized: model={self._model}, sample_rate={self._sample_rate}")
    
    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio to Deepgram Flux and receive real-time transcriptions.
        
        Uses EndOfTurn pattern from official Flux documentation.
        
        Args:
            audio_stream: Async iterator of audio chunks (PCM 16-bit)
            language: Language code
            context: Optional context
            
        Yields:
            TranscriptChunk: Partial or final transcripts
        """
        if not self._api_key:
            raise RuntimeError("Deepgram API key not set. Call initialize() first.")
        
        # Build WebSocket URL with Flux parameters
        url = (
            f"wss://api.deepgram.com/v2/listen"
            f"?model={self._model}"
            f"&encoding={self._encoding}"
            f"&sample_rate={self._sample_rate}"
            f"&eot_threshold=0.7"  # Default end-of-turn threshold
        )
        
        headers = {"Authorization": f"Token {self._api_key}"}
        
        # Async queues
        transcript_queue = asyncio.Queue()
        stop_event = asyncio.Event()
        
        async def send_audio(ws):
            """Send validated audio chunks to WebSocket"""
            chunks_sent = 0
            chunks_invalid = 0
            try:
                async for audio_chunk in audio_stream:
                    if stop_event.is_set():
                        break
                    
                    # Validate PCM format (16kHz, mono, 16-bit)
                    is_valid, error = validate_pcm_format(
                        audio_chunk.data,
                        expected_rate=self._sample_rate,
                        expected_channels=1,
                        expected_bit_depth=16
                    )
                    
                    if not is_valid:
                        chunks_invalid += 1
                        if chunks_invalid <= 5:  # Log first 5 warnings only
                            logger.warning(
                                f"Invalid PCM chunk #{chunks_invalid}: {error}",
                                extra={"chunk_size": len(audio_chunk.data)}
                            )
                        continue  # Skip invalid chunks
                    
                    # Send validated PCM audio bytes
                    await ws.send(audio_chunk.data)
                    chunks_sent += 1
                    
            except Exception as e:
                logger.error(f"Flux send_audio error: {e}")
            finally:
                if chunks_invalid > 0:
                    logger.info(f"Flux audio stats: {chunks_sent} sent, {chunks_invalid} invalid")
                stop_event.set()
        
        async def receive_transcripts(ws):
            """Receive and process Flux TurnInfo events"""
            try:
                async for message in ws:
                    if stop_event.is_set():
                        break
                    
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    
                    # Handle TurnInfo messages (Flux-specific)
                    if msg_type == "TurnInfo":
                        event = data.get("event", "")
                        transcript_text = data.get("transcript", "")
                        
                        if event == "EndOfTurn":
                            # User finished speaking
                            if transcript_text and transcript_text.strip():
                                chunk = TranscriptChunk(
                                    text=transcript_text.strip(),
                                    is_final=True,
                                    confidence=data.get("end_of_turn_confidence", 1.0)
                                )
                                await transcript_queue.put(chunk)
                            
                            # Send end-of-turn signal
                            end_chunk = TranscriptChunk(
                                text="",
                                is_final=True,
                                confidence=1.0
                            )
                            await transcript_queue.put(end_chunk)
                            logger.debug(f"Flux EndOfTurn: '{transcript_text}'")
                        
                        elif event == "Update":
                            # Partial transcript update
                            if transcript_text and transcript_text.strip():
                                chunk = TranscriptChunk(
                                    text=transcript_text.strip(),
                                    is_final=False,
                                    confidence=data.get("end_of_turn_confidence")
                                )
                                await transcript_queue.put(chunk)
                        
                        elif event == "StartOfTurn":
                            # User started speaking - emit barge-in signal
                            # This allows interrupting TTS playback when user speaks
                            logger.info("Flux StartOfTurn - Barge-in detected, user interrupting")
                            barge_in = BargeInSignal()
                            await transcript_queue.put(barge_in)
                        
                        elif event == "EagerEndOfTurn":
                            logger.debug(f"Flux EagerEndOfTurn: '{transcript_text}'")
                        
                        elif event == "TurnResumed":
                            logger.debug("Flux TurnResumed")
                    
                    # Handle Results (fallback for non-Flux responses)
                    elif msg_type == "Results":
                        channel = data.get("channel", {})
                        alternatives = channel.get("alternatives", [])
                        if alternatives:
                            transcript = alternatives[0].get("transcript", "")
                            if transcript:
                                chunk = TranscriptChunk(
                                    text=transcript,
                                    is_final=False,
                                    confidence=alternatives[0].get("confidence")
                                )
                                await transcript_queue.put(chunk)
                    
                    elif msg_type == "Metadata":
                        logger.debug(f"Flux Metadata: {data}")
                        
            except websockets.exceptions.ConnectionClosed:
                logger.info("Flux WebSocket closed")
            except Exception as e:
                logger.error(f"Flux receive error: {e}")
            finally:
                stop_event.set()
                await transcript_queue.put(None)
        
        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                logger.info("Connected to Deepgram Flux WebSocket")
                
                # Start send/receive tasks
                send_task = asyncio.create_task(send_audio(ws))
                receive_task = asyncio.create_task(receive_transcripts(ws))
                
                # Yield transcripts
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            transcript_queue.get(),
                            timeout=0.02  # Reduced from 0.1s for lower latency
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
            logger.error(f"Flux connection error: {e}")
            raise
    
    def detect_turn_end(self, transcript_chunk: TranscriptChunk) -> bool:
        """Detect if user finished speaking (empty final chunk = EndOfTurn)"""
        return transcript_chunk.is_final and not transcript_chunk.text
    
    async def cleanup(self) -> None:
        """Release resources"""
        self._api_key = None
        logger.info("DeepgramFlux cleaned up")
    
    @property
    def name(self) -> str:
        return "deepgram-flux"
    
    def __repr__(self) -> str:
        return f"DeepgramFluxSTTProvider(model={self._model}, sample_rate={self._sample_rate})"
