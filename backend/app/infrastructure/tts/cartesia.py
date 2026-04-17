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
        # Persistent per-call WebSocket connections.  Opening a fresh WS for
        # every sentence added 300–600 ms of TLS/upgrade latency to the first
        # audio chunk of each turn.  With a persistent WS the handshake is
        # paid once per call; subsequent sentences multiplex via context_id.
        # Ref: https://docs.cartesia.ai/api-reference/tts/websocket
        self._call_ws: Dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._call_ws_locks: Dict[str, asyncio.Lock] = {}
    
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
    
    def _ws_url(self) -> str:
        return (
            f"wss://api.cartesia.ai/tts/websocket"
            f"?api_key={self._api_key}"
            f"&cartesia_version={self.API_VERSION}"
        )

    def _build_payload(
        self,
        text: str,
        voice_id: str,
        sample_rate: int,
        language: str,
        speed,
        emotion,
    ) -> Dict[str, Any]:
        voice_config: Dict[str, Any] = {"mode": "id", "id": voice_id}
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
        return payload

    async def connect_for_call(self, call_id: str) -> None:
        """
        Open (or re-use) a persistent Cartesia TTS WebSocket for `call_id`.

        Idempotent — safe to call multiple times.  Intended to be fired in
        parallel with ARI media setup from telephony_bridge._on_new_call so the
        TLS/upgrade round-trip (~300–600 ms) completes before the first
        sentence is synthesised.
        """
        if not self._session:
            raise RuntimeError("Cartesia client not initialized. Call initialize() first.")
        existing = self._call_ws.get(call_id)
        if existing is not None and not existing.closed:
            return
        _t0 = asyncio.get_event_loop().time()
        try:
            ws = await self._session.ws_connect(
                self._ws_url(),
                timeout=aiohttp.ClientTimeout(connect=3.0, sock_read=30.0),
                heartbeat=20.0,
            )
        except Exception as exc:
            logger.warning("cartesia_ws_connect_failed call_id=%s: %s", call_id, exc)
            return
        handshake_ms = (asyncio.get_event_loop().time() - _t0) * 1000.0
        self._call_ws[call_id] = ws
        self._call_ws_locks.setdefault(call_id, asyncio.Lock())
        logger.info(
            "cartesia_ws_opened call_id=%s handshake_ms=%.0f",
            call_id, handshake_ms,
            extra={"call_id": call_id, "tts_ws_handshake_ms": round(handshake_ms)},
        )

    async def disconnect_for_call(self, call_id: str) -> None:
        """Close the persistent WS for `call_id` (called from call-end path)."""
        ws = self._call_ws.pop(call_id, None)
        self._call_ws_locks.pop(call_id, None)
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass

    async def _get_or_open_ws(self, call_id: str):
        ws = self._call_ws.get(call_id)
        if ws is None or ws.closed:
            await self.connect_for_call(call_id)
            ws = self._call_ws.get(call_id)
        return ws

    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 24000,
        **kwargs
    ) -> AsyncIterator[AudioChunk]:
        """
        Stream synthesized audio using Cartesia WebSocket API (true streaming).

        When `call_id` is supplied (kwargs), re-uses a persistent per-call
        WebSocket — only the first turn pays the TLS/upgrade cost.  Subsequent
        sentences reuse the connection with a fresh `context_id` per Cartesia's
        multiplexing contract.

        When `call_id` is absent (ad-hoc callers such as the AI Options
        benchmark endpoints), falls back to a transient per-request WS to
        preserve the previous behaviour.

        Args:
            text: Text to synthesize (one sentence from the pipeline)
            voice_id: Cartesia voice ID
            sample_rate: Output sample rate (default 24000)
            **kwargs: language, speed, emotion, call_id

        Yields:
            AudioChunk with Float32 PCM data (gateway converts to Int16)
        """
        if not self._session:
            raise RuntimeError("Cartesia client not initialized. Call initialize() first.")

        selected_voice_id = voice_id or self._voice_id
        language = kwargs.get("language", self._language)
        speed = kwargs.get("speed")
        emotion = kwargs.get("emotion")
        call_id = kwargs.get("call_id")

        payload = self._build_payload(
            text, selected_voice_id, sample_rate, language, speed, emotion
        )

        logger.debug("[Cartesia] WS stream: '%s...' voice=%s", text[:50], selected_voice_id)

        if call_id:
            # Persistent per-call WebSocket path — single handshake per call.
            lock = self._call_ws_locks.setdefault(call_id, asyncio.Lock())
            async with lock:
                ws = await self._get_or_open_ws(call_id)
                if ws is None:
                    # Fallback to transient WS if connect failed
                    async for chunk in self._stream_transient(payload, sample_rate):
                        yield chunk
                    return
                chunks_yielded = False
                try:
                    async for chunk in self._stream_over_ws(ws, payload, sample_rate):
                        chunks_yielded = True
                        yield chunk
                    return
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
                    # If any chunks were already yielded the caller has partial
                    # audio; retrying would duplicate it.  Surface the error.
                    if chunks_yielded:
                        await self.disconnect_for_call(call_id)
                        raise RuntimeError(
                            f"Cartesia WS failed mid-generation: {exc}"
                        )
                    # Clean reset — WS died before any audio.  Reconnect + retry
                    # once with a fresh context_id.
                    logger.warning(
                        "cartesia_ws_reconnect call_id=%s reason=%s", call_id, exc
                    )
                    await self.disconnect_for_call(call_id)
                    ws = await self._get_or_open_ws(call_id)
                    if ws is None:
                        async for chunk in self._stream_transient(payload, sample_rate):
                            yield chunk
                        return
                    payload = self._build_payload(
                        text, selected_voice_id, sample_rate, language, speed, emotion
                    )
                    async for chunk in self._stream_over_ws(ws, payload, sample_rate):
                        yield chunk
                    return

        # No call_id → legacy transient WS (one handshake per synthesis).
        async for chunk in self._stream_transient(payload, sample_rate):
            yield chunk

    async def _stream_transient(
        self, payload: Dict[str, Any], sample_rate: int
    ) -> AsyncIterator[AudioChunk]:
        """Open a short-lived WS, stream one generation, close.  Legacy path."""
        try:
            async with self._session.ws_connect(
                self._ws_url(),
                timeout=aiohttp.ClientTimeout(connect=3.0, sock_read=10.0),
            ) as ws:
                async for chunk in self._stream_over_ws(ws, payload, sample_rate):
                    yield chunk
        except asyncio.TimeoutError:
            raise RuntimeError("Cartesia TTS WebSocket timeout")
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Cartesia TTS WebSocket error: {exc}")

    async def _stream_over_ws(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        payload: Dict[str, Any],
        sample_rate: int,
    ) -> AsyncIterator[AudioChunk]:
        """Send one generation and yield its audio chunks until `done` or error."""
        await ws.send_str(json.dumps(payload))
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)

                if data.get("data"):
                    audio_bytes = base64.b64decode(data["data"])
                    if not audio_bytes:
                        continue
                    if len(audio_bytes) % 2 != 0:
                        audio_bytes = audio_bytes[:-1]
                    int16_arr = np.frombuffer(audio_bytes, dtype=np.int16)
                    float32_data = (int16_arr.astype(np.float32) / 32768.0).tobytes()
                    yield AudioChunk(
                        data=float32_data,
                        sample_rate=sample_rate,
                        channels=1,
                    )
                elif data.get("done"):
                    # Generation complete; WS stays open for the next context_id.
                    return
                elif data.get("type") == "error":
                    logger.error("[Cartesia WS] Error: %s", data)
                    raise RuntimeError(f"Cartesia WS error: {data}")
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                logger.warning("[Cartesia WS] Connection closed: %s", msg)
                raise RuntimeError("Cartesia WS closed mid-generation")
    
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
        # Close any persistent per-call WebSockets before tearing the session.
        for call_id in list(self._call_ws.keys()):
            try:
                await self.disconnect_for_call(call_id)
            except Exception:
                pass
        if self._session:
            await self._session.close()
            self._session = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "cartesia"
    
    def __repr__(self) -> str:
        return f"CartesiaTTSProvider(model={self._model_id}, voice={self._voice_id})"
