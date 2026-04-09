"""
ElevenLabs TTS provider.

Streams PCM audio from ElevenLabs so it can plug into the existing voice
pipeline without additional transcoding on the provider side.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Dict, List, Optional

import aiohttp

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk
from app.infrastructure.tts.elevenlabs_catalog import (
    get_elevenlabs_voices_for_current_key,
)

logger = logging.getLogger(__name__)


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs TTS provider using the streaming HTTP endpoint."""

    API_BASE_URL = "https://api.elevenlabs.io"

    def __init__(self):
        self._api_key: Optional[str] = None
        self._model_id: str = "eleven_flash_v2_5"
        self._voice_id: str = ""
        self._sample_rate: int = 24000
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self, config: dict) -> None:
        self._api_key = config.get("api_key") or os.getenv("ELEVENLABS_API_KEY")
        if not self._api_key:
            raise ValueError("ElevenLabs API key not found in config or environment")

        self._model_id = config.get("model_id") or config.get("model") or "eleven_flash_v2_5"
        self._voice_id = config.get("voice_id", "")
        self._sample_rate = int(config.get("sample_rate", 24000))
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))

        logger.info(
            "[ElevenLabs] Initialized: model=%s voice=%s sample_rate=%s",
            self._model_id,
            self._voice_id,
            self._sample_rate,
        )

    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 16000,
        **kwargs,
    ) -> AsyncIterator[AudioChunk]:
        if not self._session:
            raise RuntimeError("ElevenLabs client not initialized. Call initialize() first.")

        selected_voice_id = voice_id or self._voice_id
        if not selected_voice_id:
            raise RuntimeError("ElevenLabs voice_id is required")

        selected_sample_rate = int(sample_rate or self._sample_rate or 24000)
        model_id = kwargs.get("model_id") or self._model_id
        output_format = self._output_format_for_rate(selected_sample_rate)

        params = {
            "output_format": output_format,
            "optimize_streaming_latency": "2",
        }
        payload: Dict[str, object] = {
            "text": text,
            "model_id": model_id,
        }

        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        }

        url = f"{self.API_BASE_URL}/v1/text-to-speech/{selected_voice_id}/stream"

        try:
            async with self._session.post(
                url,
                headers=headers,
                params=params,
                json=payload,
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(
                        f"ElevenLabs API error: {response.status} {error_text[:240]}"
                    )

                chunk_size = max(2048, (selected_sample_rate * 2) // 5)
                async for chunk in response.content.iter_chunked(chunk_size):
                    if not chunk:
                        continue
                    yield AudioChunk(
                        data=chunk,
                        sample_rate=selected_sample_rate,
                        channels=1,
                    )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("ElevenLabs TTS request timeout") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"ElevenLabs TTS error: {exc}") from exc

    async def get_available_voices(self) -> List[Dict]:
        voices = await get_elevenlabs_voices_for_current_key()
        return [voice.model_dump() for voice in voices]

    async def cleanup(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def name(self) -> str:
        return "elevenlabs"

    def _output_format_for_rate(self, sample_rate: int) -> str:
        mapping = {
            8000: "pcm_8000",
            16000: "pcm_16000",
            22050: "pcm_22050",
            24000: "pcm_24000",
            44100: "pcm_44100",
        }
        return mapping.get(sample_rate, "pcm_24000")
