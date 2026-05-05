"""
ElevenLabs TTS provider.

Streams PCM audio from ElevenLabs so it can plug into the existing voice
pipeline without additional transcoding on the provider side.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import AsyncIterator, Dict, List, Optional

import aiohttp

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk
from app.infrastructure.providers.key_pool import KeyPool, parse_keys_csv
from app.infrastructure.providers.provider_concurrency import get_provider_guard
from app.infrastructure.tts.elevenlabs_catalog import (
    get_elevenlabs_voices_for_current_key,
)

logger = logging.getLogger(__name__)


# Retry configuration — mirrors Groq LLM retry pattern
_EL_MAX_RETRIES = 2
_EL_RETRY_BASE_DELAY = 0.3  # 300ms base, exponential with jitter


class _SingleKeyLease:
    """Drop-in replacement for KeyPool.acquire() when only one key is configured."""

    def __init__(self, key: str) -> None:
        self.key = key

    async def __aenter__(self) -> "_SingleKeyLease":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def report_failure(self, retryable: bool = True) -> None:
        return None

    def report_success(self) -> None:
        return None


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs TTS provider using the streaming HTTP endpoint."""

    API_BASE_URL = "https://api.elevenlabs.io"

    def __init__(self):
        self._api_key: Optional[str] = None
        self._model_id: str = "eleven_flash_v2_5"
        self._voice_id: str = ""
        self._sample_rate: int = 24000
        # Persistent session with a connector that keeps TCP connections alive
        # across synthesis calls — avoids a new TLS handshake (~50-100ms) per
        # sentence.  TCPConnector limit=10 is generous for a voice pipeline that
        # makes one request at a time.
        self._session: Optional[aiohttp.ClientSession] = None
        self._pool: Optional[KeyPool] = None
        self._guard = get_provider_guard("elevenlabs")

    async def initialize(self, config: dict) -> None:
        # Multi-key pool from ELEVENLABS_API_KEYS overrides the single-key path.
        # Tenant-scoped keys (config["api_key"]) still take precedence — the pool
        # is the platform-level fallback.
        pool_keys = parse_keys_csv(os.getenv("ELEVENLABS_API_KEYS"))
        single_key = config.get("api_key") or os.getenv("ELEVENLABS_API_KEY")
        if pool_keys and not config.get("api_key"):
            self._pool = KeyPool("elevenlabs", pool_keys)
            self._api_key = pool_keys[0]  # first key for warmup / catalog only
        else:
            self._api_key = single_key
            self._pool = None
        if not self._api_key:
            raise ValueError("ElevenLabs API key not found in config or environment")

        self._model_id = config.get("model_id") or config.get("model") or "eleven_flash_v2_5"
        self._voice_id = config.get("voice_id", "")
        self._sample_rate = int(config.get("sample_rate", 24000))

        # keepalive_timeout=30s keeps the connection to ElevenLabs alive between
        # sentences so subsequent synthesis calls skip the TLS handshake.
        # limit_per_host=50 matches the server's max_concurrent_pipelines default.
        # The previous limit=10 (global cap) serialized requests at 10+ concurrent
        # sessions — limit_per_host scopes the cap to api.elevenlabs.io only.
        connector = aiohttp.TCPConnector(limit_per_host=50, keepalive_timeout=30)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(connect=3.0, sock_read=15.0),
        )

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

        # `optimize_streaming_latency` was a deprecated query parameter that
        # ElevenLabs removed.  Flash v2.5 has low latency built-in — no param needed.
        params = {"output_format": output_format}
        payload: Dict[str, object] = {
            "text": text,
            "model_id": model_id,
        }
        url = f"{self.API_BASE_URL}/v1/text-to-speech/{selected_voice_id}/stream"

        # Concurrency guard wraps the entire call (incl. retries) so 50
        # callers can't fan out to 50 simultaneous in-flight requests.
        async with self._guard.acquire():
            last_err: Optional[Exception] = None
            for attempt in range(_EL_MAX_RETRIES + 1):
                key_ctx = (
                    self._pool.acquire() if self._pool is not None
                    else _SingleKeyLease(self._api_key)
                )
                async with key_ctx as lease:
                    headers = {
                        "xi-api-key": lease.key,
                        "Content-Type": "application/json",
                    }
                    try:
                        async with self._session.post(
                            url, headers=headers, params=params, json=payload,
                        ) as response:
                            if response.status not in (200, 206):
                                error_text = await response.text()
                                is_retryable = response.status in (429, 500, 502, 503, 504)
                                err = RuntimeError(
                                    f"ElevenLabs API error: {response.status} {error_text[:240]}"
                                )
                                if is_retryable:
                                    lease.report_failure(retryable=True)
                                if is_retryable and attempt < _EL_MAX_RETRIES:
                                    last_err = err
                                    delay = min(
                                        _EL_RETRY_BASE_DELAY * (2 ** attempt), 5.0
                                    ) * (0.5 + random.random())
                                    logger.warning(
                                        "[ElevenLabs] %s — retry %d/%d in %.2fs",
                                        response.status, attempt + 1, _EL_MAX_RETRIES, delay,
                                    )
                                    await asyncio.sleep(delay)
                                    continue
                                raise err

                            # Stream in small fixed-size frames so the first
                            # audio reaches the gateway quickly. 2048 ≈ 42ms @ 24k.
                            async for chunk in response.content.iter_chunked(2048):
                                if not chunk:
                                    continue
                                yield AudioChunk(
                                    data=chunk,
                                    sample_rate=selected_sample_rate,
                                    channels=1,
                                )
                            lease.report_success()
                            return  # success — exit retry loop

                    except asyncio.TimeoutError as exc:
                        lease.report_failure(retryable=True)
                        last_err = exc
                        if attempt < _EL_MAX_RETRIES:
                            delay = min(
                                _EL_RETRY_BASE_DELAY * (2 ** attempt), 5.0
                            ) * (0.5 + random.random())
                            logger.warning(
                                "[ElevenLabs] timeout — retry %d/%d in %.2fs",
                                attempt + 1, _EL_MAX_RETRIES, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise RuntimeError("ElevenLabs TTS request timeout") from exc
                    except aiohttp.ClientError as exc:
                        lease.report_failure(retryable=True)
                        raise RuntimeError(f"ElevenLabs TTS error: {exc}") from exc

            if last_err:
                raise RuntimeError(
                    f"ElevenLabs TTS failed after {_EL_MAX_RETRIES} retries: {last_err}"
                )

    async def connect_for_call(self, call_id: str) -> None:
        """
        Pre-warm the HTTPS connection to ElevenLabs before the first synthesis.
        aiohttp reuses keep-alive connections from the pool, so paying TCP+TLS
        once here means subsequent synthesis calls skip that handshake entirely.
        """
        if not self._session or not self._voice_id:
            logger.debug("elevenlabs connect_for_call: not ready, skipping warmup")
            return
        try:
            output_format = self._output_format_for_rate(self._sample_rate)
            url = f"{self.API_BASE_URL}/v1/text-to-speech/{self._voice_id}/stream"
            headers = {"xi-api-key": self._api_key, "Content-Type": "application/json"}
            payload = {"text": ".", "model_id": self._model_id}
            async with self._session.post(
                url,
                headers=headers,
                params={"output_format": output_format},
                json=payload,
            ) as resp:
                await resp.read()  # drain so the connection returns to the pool
            label = call_id[:12] if call_id and call_id != "prewarm" else "prewarm"
            logger.debug("elevenlabs warmup complete for call_id=%s", label)
        except Exception as exc:
            logger.warning("elevenlabs connect_for_call failed (non-fatal): %s", exc)

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
