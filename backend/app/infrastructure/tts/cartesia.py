"""
Cartesia TTS Provider Implementation
Official WebSocket-based streaming with generation_config support for Sonic-3

Based on LiveKit's implementation for jitter-free audio streaming.
Uses pcm_s16le encoding at 24kHz as recommended by Cartesia.
"""
import os
import json
import base64
import asyncio
import logging
from typing import AsyncIterator, List, Dict, Optional, Any
import aiohttp
import numpy as np

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk

logger = logging.getLogger(__name__)


class CartesiaTTSProvider(TTSProvider):
    """Cartesia Sonic-3 TTS provider with WebSocket streaming for jitter-free audio"""
    
    # Cartesia API constants
    API_BASE_URL = "https://api.cartesia.ai"
    API_VERSION = "2024-06-10"
    
    def __init__(self):
        self._api_key: Optional[str] = None
        self._model_id: str = "sonic-3"
        self._voice_id: str = ""
        self._sample_rate: int = 24000  # Cartesia's recommended sample rate
        self._encoding: str = "pcm_s16le"  # 16-bit signed little-endian PCM
        self._language: str = "en"
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def initialize(self, config: dict) -> None:
        """Initialize Cartesia client with configuration"""
        self._api_key = config.get("api_key") or os.getenv("CARTESIA_API_KEY")
        
        if not self._api_key:
            raise ValueError("Cartesia API key not found in config or environment")
        
        # Configuration
        self._model_id = config.get("model_id", "sonic-3")
        self._voice_id = config.get("voice_id", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")
        self._sample_rate = config.get("sample_rate", 24000)
        self._encoding = config.get("encoding", "pcm_s16le")
        self._language = config.get("language", "en")
        
        # Create aiohttp session
        self._session = aiohttp.ClientSession()
        
        logger.info(f"[Cartesia] Initialized: model={self._model_id}, voice={self._voice_id}, sample_rate={self._sample_rate}")
    
    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 24000,
        **kwargs
    ) -> AsyncIterator[AudioChunk]:
        """
        Stream synthesized audio using Cartesia WebSocket API (true streaming).

        Uses the WebSocket endpoint so Cartesia starts sending audio ~40ms after
        the request — first audio arrives before synthesis is complete.

        The previous implementation used POST /tts/bytes (REST) which waits for
        the entire audio to be generated before sending any bytes.  That endpoint
        is non-streaming despite the iter_chunked read loop; every sentence
        incurred 400-900ms silence before the first audio sample reached the user.

        Args:
            text: Text to synthesize (one sentence from the pipeline)
            voice_id: Cartesia voice ID
            sample_rate: Output sample rate (default 24000)
            **kwargs: language, speed, emotion

        Yields:
            AudioChunk with Float32 PCM data (gateway converts to Int16)
        """
        if not self._session:
            raise RuntimeError("Cartesia client not initialized. Call initialize() first.")

        selected_voice_id = voice_id or self._voice_id
        language = kwargs.get("language", self._language)
        speed = kwargs.get("speed")
        emotion = kwargs.get("emotion")

        ws_url = (
            f"wss://api.cartesia.ai/tts/websocket"
            f"?api_key={self._api_key}"
            f"&cartesia_version={self.API_VERSION}"
        )

        voice_config: Dict[str, Any] = {"mode": "id", "id": selected_voice_id}
        payload: Dict[str, Any] = {
            "model_id": self._model_id,
            "transcript": text,
            "voice": voice_config,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": sample_rate,
            },
            "language": language,
            "context_id": os.urandom(8).hex(),
            "continue": False,
        }

        generation_config: Dict[str, Any] = {}
        if speed is not None:
            generation_config["speed"] = speed
        if emotion:
            generation_config["emotion"] = emotion
        if generation_config:
            payload["generation_config"] = generation_config

        logger.debug("[Cartesia] WS stream: '%s...' voice=%s", text[:50], selected_voice_id)

        try:
            async with self._session.ws_connect(
                ws_url,
                timeout=aiohttp.ClientTimeout(connect=3.0, sock_read=10.0),
            ) as ws:
                await ws.send_str(json.dumps(payload))

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)

                        if data.get("data"):
                            # Audio arrives as base64-encoded Int16 PCM
                            audio_bytes = base64.b64decode(data["data"])
                            if not audio_bytes:
                                continue
                            # Align to Int16 frame boundary (2 bytes/sample)
                            if len(audio_bytes) % 2 != 0:
                                audio_bytes = audio_bytes[:-1]
                            # Convert Int16 → Float32 so the media gateway's
                            # tts_source_format="f32le" path applies tanh saturation
                            int16_arr = np.frombuffer(audio_bytes, dtype=np.int16)
                            float32_data = (int16_arr.astype(np.float32) / 32768.0).tobytes()
                            yield AudioChunk(
                                data=float32_data,
                                sample_rate=sample_rate,
                                channels=1,
                            )

                        elif data.get("done"):
                            break

                        elif data.get("type") == "error":
                            logger.error("[Cartesia WS] Error: %s", data)
                            raise RuntimeError(f"Cartesia WS error: {data}")

                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        logger.warning("[Cartesia WS] Connection closed: %s", msg)
                        break

        except asyncio.TimeoutError:
            raise RuntimeError("Cartesia TTS WebSocket timeout")
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Cartesia TTS WebSocket error: {exc}")
        except Exception as exc:
            if not isinstance(exc, RuntimeError):
                logger.error("[Cartesia] synthesis failed: %s", exc, exc_info=True)
                raise RuntimeError(f"Cartesia TTS synthesis failed: {exc}")
            raise
    
    async def stream_synthesize_websocket(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 24000,
        **kwargs
    ) -> AsyncIterator[AudioChunk]:
        """
        Alternative WebSocket-based streaming for lowest latency.
        Uses the official Cartesia WebSocket API.
        """
        if not self._session:
            raise RuntimeError("Cartesia client not initialized")
        
        selected_voice_id = voice_id or self._voice_id
        language = kwargs.get("language", self._language)
        speed = kwargs.get("speed")
        emotion = kwargs.get("emotion")
        
        ws_url = f"wss://api.cartesia.ai/tts/websocket?api_key={self._api_key}&cartesia_version={self.API_VERSION}"
        
        voice_config: Dict[str, Any] = {
            "mode": "id",
            "id": selected_voice_id
        }
        
        payload: Dict[str, Any] = {
            "model_id": self._model_id,
            "transcript": text,
            "voice": voice_config,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": sample_rate
            },
            "language": language,
            "context_id": os.urandom(8).hex(),
            "continue": False  # Single request
        }
        
        # Add generation_config for Sonic-3
        generation_config: Dict[str, Any] = {}
        if speed is not None:
            generation_config["speed"] = speed
        if emotion:
            generation_config["emotion"] = emotion
        if generation_config:
            payload["generation_config"] = generation_config
        
        try:
            async with self._session.ws_connect(ws_url) as ws:
                # Send the TTS request
                await ws.send_str(json.dumps(payload))
                
                # Receive audio chunks
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        
                        if data.get("data"):
                            # Audio data is base64 encoded Int16 PCM
                            audio_bytes = base64.b64decode(data["data"])
                            
                            # Convert Int16 to Float32 for browser playback
                            int16_array = np.frombuffer(audio_bytes, dtype=np.int16)
                            float32_array = (int16_array.astype(np.float32) / 32768.0)
                            float32_data = float32_array.tobytes()
                            
                            yield AudioChunk(
                                data=float32_data,
                                sample_rate=sample_rate,
                                channels=1
                            )
                        elif data.get("done"):
                            break
                        elif data.get("type") == "error":
                            logger.error(f"[Cartesia WS] Error: {data}")
                            raise RuntimeError(f"Cartesia WS error: {data}")
                    
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        
        except Exception as e:
            logger.error(f"[Cartesia WS] Error: {e}")
            raise RuntimeError(f"Cartesia WebSocket error: {e}")
    
    async def get_available_voices(self) -> List[Dict]:
        """Get list of available Cartesia voices"""
        if not self._session:
            raise RuntimeError("Cartesia client not initialized")
        
        try:
            headers = {
                "X-API-Key": self._api_key,
                "Cartesia-Version": self.API_VERSION
            }
            
            async with self._session.get(
                f"{self.API_BASE_URL}/voices",
                headers=headers
            ) as response:
                if response.status == 200:
                    voices = await response.json()
                    return [
                        {
                            "id": v.get("id"),
                            "name": v.get("name"),
                            "language": v.get("language", "en"),
                            "description": v.get("description", "")
                        }
                        for v in voices
                    ]
                else:
                    raise RuntimeError(f"Failed to fetch voices: {response.status}")
        
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Cartesia voices: {e}")
    
    async def cleanup(self) -> None:
        """Release resources"""
        if self._session:
            await self._session.close()
            self._session = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "cartesia"
    
    def __repr__(self) -> str:
        return f"CartesiaTTSProvider(model={self._model_id}, voice={self._voice_id})"
