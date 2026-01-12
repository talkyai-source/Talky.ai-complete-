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
        Stream synthesized audio using Cartesia WebSocket API.
        
        Based on LiveKit's official implementation for jitter-free streaming.
        Uses pcm_s16le encoding for browser compatibility.
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier
            sample_rate: Audio sample rate (default 24000 as recommended)
            **kwargs: Additional parameters:
                - language: Language code (default: "en")
                - speed: Speed 0.6-1.5 for Sonic-3
                - emotion: Emotion string (e.g., "content", "excited")
        
        Yields:
            AudioChunk: Streaming audio chunks
        """
        if not self._session:
            raise RuntimeError("Cartesia client not initialized. Call initialize() first.")
        
        selected_voice_id = voice_id or self._voice_id
        language = kwargs.get("language", self._language)
        speed = kwargs.get("speed")
        emotion = kwargs.get("emotion")
        
        # Build request payload following official Cartesia format
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
                "encoding": "pcm_s16le",  # Use Int16 (like Google TTS), convert to Float32 for browser
                "sample_rate": sample_rate
            },
            "language": language
        }
        
        # Add generation_config for Sonic-3 speed/emotion (official method)
        generation_config: Dict[str, Any] = {}
        if speed is not None:
            generation_config["speed"] = speed
        if emotion:
            generation_config["emotion"] = emotion
        if generation_config:
            payload["generation_config"] = generation_config
            logger.info(f"[Cartesia] Using generation_config: {generation_config}")
        
        logger.info(f"[Cartesia] Synthesizing: '{text[:50]}...' voice={selected_voice_id}")
        
        try:
            # Use bytes endpoint for simple streaming (SSE approach)
            headers = {
                "X-API-Key": self._api_key,
                "Cartesia-Version": self.API_VERSION,
                "Content-Type": "application/json",
                "Accept": "audio/*"
            }
            
            async with self._session.post(
                f"{self.API_BASE_URL}/tts/bytes",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"[Cartesia] API error {response.status}: {error_text}")
                    raise RuntimeError(f"Cartesia API error: {response.status}")
                
                # Stream audio chunks - read larger chunks for smooth playback
                # For pcm_s16le: 2 bytes per sample, ~500ms chunks
                chunk_size = sample_rate * 2 // 2  # ~500ms of audio (2 bytes per sample)
                
                async for chunk in response.content.iter_chunked(chunk_size):
                    if chunk:
                        # Convert Int16 to Float32 for browser playback (same as Google TTS)
                        int16_array = np.frombuffer(chunk, dtype=np.int16)
                        float32_array = (int16_array.astype(np.float32) / 32768.0)
                        float32_data = float32_array.tobytes()
                        
                        yield AudioChunk(
                            data=float32_data,
                            sample_rate=sample_rate,
                            channels=1
                        )
        
        except asyncio.TimeoutError:
            logger.error("[Cartesia] Request timeout")
            raise RuntimeError("Cartesia TTS request timeout")
        except aiohttp.ClientError as e:
            logger.error(f"[Cartesia] Client error: {e}")
            raise RuntimeError(f"Cartesia TTS error: {e}")
        except Exception as e:
            logger.error(f"[Cartesia] TTS synthesis failed: {e}")
            raise RuntimeError(f"Cartesia TTS synthesis failed: {e}")
    
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
