"""Voice preview endpoints + on-disk preview cache.

The preview cache is owned by this module — it's used by `/voices/preview`
and `/voices/prefetch`, nothing else needs it.

Endpoints:
  POST /voices/preview            - synthesize a sample for one voice (cached)
  GET  /voices/prefetch-status    - how many voices are cached on disk
  POST /voices/prefetch           - pre-download / pre-synth every voice
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.domain.models.ai_config import VoiceInfo
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider

from ._catalog import (
    _english_deepgram_static_voices,
    _find_cartesia_voice,
    _find_elevenlabs_voice,
    _find_google_voice,
    _get_all_tts_voices,
    _get_deepgram_voices_for_current_key,
    _get_live_cartesia_voices,
    _is_cartesia_voice,
    _is_google_voice,
)
from ._shared import _linear16_to_float32le_bytes

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Options"])


# Disk cache for generated float32 PCM previews (keyed by provider + voice_id).
# ElevenLabs stores MP3 in its own cache dir; this stores decoded float32 for
# both Deepgram and ElevenLabs so /voices/preview is served from disk on
# subsequent calls without hitting any external API.
_VOICE_PREVIEW_CACHE_DIR = Path("/tmp/talky-voice-preview-cache")
_PREVIEW_SAMPLE_TEXT = "Hello, I am your AI voice assistant. How can I help you today?"

# --- Realtime (gpt-realtime-2) voice previews -------------------------------
# The realtime voices (marin, cedar, …) are OpenAI voices, but they are spoken
# by the realtime speech-to-speech model — there is no separate TTS step at
# call time. To *preview* them in the UI we synthesize a one-off sample with
# OpenAI's dedicated speech endpoint (gpt-4o-mini-tts). That model returns
# 24 kHz mono pcm16, which we convert to the same float32le payload the
# cascaded path already returns so the frontend player is identical.
#
# gpt-4o-mini-tts accepts the full realtime voice set — INCLUDING marin + cedar
# (live-verified 2026-07-03: both return HTTP 200 with real audio). So every
# realtime voice previews as ITSELF — no substitution.
_OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
_REALTIME_PREVIEW_MODEL = "gpt-4o-mini-tts"
_REALTIME_PREVIEW_SAMPLE_RATE = 24000  # gpt-4o-mini-tts pcm output is 24 kHz
# Voices the /audio/speech (gpt-4o-mini-tts) model accepts directly.
_OPENAI_SPEECH_VOICES = {
    "alloy", "ash", "ballad", "cedar", "coral", "echo",
    "fable", "marin", "onyx", "nova", "sage", "shimmer", "verse",
}


def _realtime_preview_cache_key(voice_id: str) -> str:
    """Namespaced cache key so a realtime 'ash' never collides with a
    cascaded voice that happens to share the id."""
    return f"realtime-{voice_id}"


async def _synthesize_realtime_preview(voice_id: str, text: str) -> bytes:
    """Synthesize a realtime-voice sample via OpenAI's speech endpoint and
    return float32le PCM bytes (24 kHz mono). Raises HTTPException on any
    failure so the caller can surface a clean error without crashing."""
    import aiohttp

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI API key not configured",
        )

    speech_voice = voice_id if voice_id in _OPENAI_SPEECH_VOICES else None
    if not speech_voice:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Realtime voice '{voice_id}' is not previewable",
        )

    payload = {
        "model": _REALTIME_PREVIEW_MODEL,
        "voice": speech_voice,
        "input": text,
        "response_format": "pcm",
    }
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _OPENAI_SPEECH_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            ) as resp:
                body = await resp.read()
                if resp.status != 200:
                    detail = body.decode("utf-8", "replace")[:200]
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"OpenAI speech API error ({resp.status}): {detail}",
                    )
    except HTTPException:
        raise
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenAI speech request failed: {exc}",
        )

    # gpt-4o-mini-tts pcm = 24 kHz mono linear16 → float32 for the frontend.
    return _linear16_to_float32le_bytes(body)


def _preview_cache_path(voice_id: str) -> Path:
    safe = "".join(ch for ch in voice_id if ch.isalnum() or ch in {"-", "_"})
    return _VOICE_PREVIEW_CACHE_DIR / f"{safe or 'voice'}.f32"


def _load_preview_cache(voice_id: str) -> Optional[bytes]:
    path = _preview_cache_path(voice_id)
    try:
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
    except OSError:
        pass
    return None


def _save_preview_cache(voice_id: str, float32_bytes: bytes) -> None:
    try:
        _VOICE_PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _preview_cache_path(voice_id).with_suffix(".tmp")
        tmp.write_bytes(float32_bytes)
        tmp.replace(_preview_cache_path(voice_id))
    except OSError as exc:
        logger.warning("Could not write preview cache for %s: %s", voice_id, exc)


class VoicePreviewRequest(BaseModel):
    """Request for voice preview"""
    voice_id: str
    text: str = "Hello, I am your AI voice assistant. How can I help you today?"
    # Set to "realtime" to preview a gpt-realtime-2 voice (synthesized via
    # OpenAI's speech endpoint). Omit / leave None for the cascaded TTS voices.
    provider: Optional[str] = None


class VoicePreviewResponse(BaseModel):
    """Response with voice preview audio"""
    voice_id: str
    voice_name: str
    audio_base64: str
    duration_seconds: float
    latency_ms: float


@router.post("/voices/preview", response_model=VoicePreviewResponse)
async def preview_voice(request: VoicePreviewRequest):
    """
    Generate a voice preview audio sample.

    Synthesizes the given text with the specified voice
    and returns the audio as base64.

    Supports:
    - Cartesia Sonic voices
    - Google Chirp3-HD voices
    - Deepgram Aura-2 voices

    Args:
        request: VoicePreviewRequest with voice_id and optional text

    Returns:
        VoicePreviewResponse with base64 audio data
    """
    # Realtime voices take a completely separate path (OpenAI speech endpoint).
    # Handled first so it never touches the cascaded provider detection below.
    if (request.provider or "").lower() == "realtime":
        return await _preview_realtime_voice(request)

    try:
        # Serve from disk cache when available — no external API call needed.
        cached = _load_preview_cache(request.voice_id)
        if cached:
            audio_base64 = base64.b64encode(cached).decode("utf-8")
            duration_seconds = len(cached) / (24000 * 4)
            return VoicePreviewResponse(
                voice_id=request.voice_id,
                voice_name=request.voice_id,
                audio_base64=audio_base64,
                duration_seconds=duration_seconds,
                latency_ms=0.0,
            )

        tts = None
        cartesia_voices, deepgram_voices = await asyncio.gather(
            _get_live_cartesia_voices(),
            _get_deepgram_voices_for_current_key(),
        )
        deepgram_voice_map = {voice.id: voice for voice in deepgram_voices}
        deepgram_static_voice_map = {
            voice.id: voice
            for voice in _english_deepgram_static_voices()
        }

        voice_id = request.voice_id
        sample_rate = 24000

        # Determine provider by checking in order — avoids hitting ElevenLabs
        # API with Google/Deepgram voice IDs (which returns a 400 error).
        # _cartesia_voice_map is now populated by _get_live_cartesia_voices().
        is_cartesia = _is_cartesia_voice(voice_id)
        is_google = _is_google_voice(voice_id)
        is_deepgram = voice_id in deepgram_voice_map or voice_id in deepgram_static_voice_map
        elevenlabs_voice = None if (is_cartesia or is_google or is_deepgram) else await _find_elevenlabs_voice(voice_id)

        voice_info = (
            _find_cartesia_voice(voice_id)
            or _find_google_voice(voice_id)
            or deepgram_voice_map.get(voice_id)
            or deepgram_static_voice_map.get(voice_id)
            or elevenlabs_voice
        )
        voice_name = voice_info.name if voice_info else "Unknown Voice"

        if is_cartesia:
            if not os.getenv("CARTESIA_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Cartesia API key not configured",
                )
            tts = CartesiaTTSProvider()
            await tts.initialize(
                {
                    "voice_id": voice_id,
                    "model_id": "sonic-3",
                    "sample_rate": sample_rate,
                }
            )
            output_is_linear16 = False
        elif is_google:
            # Use GOOGLE_APPLICATION_CREDENTIALS from env (set in .env).
            creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            if not creds_path or not os.path.exists(creds_path):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Google service account credentials not configured. Set GOOGLE_APPLICATION_CREDENTIALS in .env",
                )
            tts = GoogleTTSStreamingProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
            output_is_linear16 = False
        elif is_deepgram:
            if not os.getenv("DEEPGRAM_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Deepgram API key not configured",
                )
            tts = DeepgramTTSProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
            output_is_linear16 = True
        elif elevenlabs_voice is not None:
            if not os.getenv("ELEVENLABS_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="ElevenLabs API key not configured",
                )
            tts = ElevenLabsTTSProvider()
            await tts.initialize(
                {
                    "voice_id": voice_id,
                    "model_id": "eleven_flash_v2_5",
                    "sample_rate": sample_rate,
                }
            )
            output_is_linear16 = True
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown voice_id",
            )

        start_time = time.time()
        audio_chunks: List[bytes] = []

        async for chunk in tts.stream_synthesize(
            text=request.text,
            voice_id=voice_id,
            sample_rate=sample_rate,
        ):
            audio_chunks.append(chunk.data)
        end_time = time.time()

        await tts.cleanup()

        combined_audio = b"".join(audio_chunks)
        if output_is_linear16:
            # Deepgram/ElevenLabs return linear16 PCM; convert to float32 for frontend.
            combined_audio = _linear16_to_float32le_bytes(combined_audio)

        # Guard: if the provider returned no audio frames at all the voice is
        # likely deprecated or inaccessible with this API key.  Return a clear
        # error instead of empty base64 — the frontend would crash trying to
        # create an AudioBuffer with 0 frames.
        if not combined_audio:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Voice '{voice_id}' returned no audio. "
                    "The voice may be deprecated or not available on this API key."
                ),
            )

        # Persist to disk so subsequent preview requests skip the API call entirely.
        _save_preview_cache(request.voice_id, combined_audio)

        audio_base64 = base64.b64encode(combined_audio).decode("utf-8")
        duration_seconds = len(combined_audio) / (sample_rate * 4)
        latency_ms = (end_time - start_time) * 1000

        return VoicePreviewResponse(
            voice_id=request.voice_id,
            voice_name=voice_name,
            audio_base64=audio_base64,
            duration_seconds=duration_seconds,
            latency_ms=latency_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Voice preview failed: {str(e)}"
        )


async def _preview_realtime_voice(request: VoicePreviewRequest) -> VoicePreviewResponse:
    """Preview a gpt-realtime-2 voice. Cached under a `realtime-` namespace and
    synthesized on miss via OpenAI's speech endpoint. Never touches the
    cascaded providers; failures surface as clean HTTP errors."""
    voice_id = request.voice_id
    cache_key = _realtime_preview_cache_key(voice_id)

    cached = _load_preview_cache(cache_key)
    if cached:
        audio_base64 = base64.b64encode(cached).decode("utf-8")
        duration_seconds = len(cached) / (_REALTIME_PREVIEW_SAMPLE_RATE * 4)
        return VoicePreviewResponse(
            voice_id=voice_id,
            voice_name=voice_id,
            audio_base64=audio_base64,
            duration_seconds=duration_seconds,
            latency_ms=0.0,
        )

    start_time = time.time()
    combined_audio = await _synthesize_realtime_preview(voice_id, request.text)
    latency_ms = (time.time() - start_time) * 1000

    if not combined_audio:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Realtime voice '{voice_id}' returned no audio. "
                "The voice may be unavailable on this API key."
            ),
        )

    _save_preview_cache(cache_key, combined_audio)

    audio_base64 = base64.b64encode(combined_audio).decode("utf-8")
    duration_seconds = len(combined_audio) / (_REALTIME_PREVIEW_SAMPLE_RATE * 4)
    return VoicePreviewResponse(
        voice_id=voice_id,
        voice_name=voice_id,
        audio_base64=audio_base64,
        duration_seconds=duration_seconds,
        latency_ms=latency_ms,
    )


@router.get("/voices/prefetch-status")
async def get_prefetch_status():
    """
    Return how many voice preview samples are cached on disk, plus whether
    each provider key is configured.
    """
    cached_ids: List[str] = []
    try:
        if _VOICE_PREVIEW_CACHE_DIR.exists():
            cached_ids = [p.stem for p in _VOICE_PREVIEW_CACHE_DIR.glob("*.f32")]
    except OSError:
        pass

    # Also count ElevenLabs MP3 cache (downloaded preview files)
    from app.infrastructure.tts.elevenlabs_catalog import (
        _ELEVENLABS_PREVIEW_CACHE_DIR,
        elevenlabs_api_key,
    )
    el_mp3_count = 0
    try:
        if _ELEVENLABS_PREVIEW_CACHE_DIR.exists():
            el_mp3_count = sum(1 for p in _ELEVENLABS_PREVIEW_CACHE_DIR.glob("*.mp3"))
    except OSError:
        pass

    return {
        "cartesia_key_configured": bool(os.getenv("CARTESIA_API_KEY")),
        "deepgram_key_configured": bool(os.getenv("DEEPGRAM_API_KEY")),
        "elevenlabs_key_configured": bool(elevenlabs_api_key()),
        "preview_samples_cached": len(cached_ids),
        "elevenlabs_mp3_samples_cached": el_mp3_count,
    }


@router.post("/voices/prefetch")
async def prefetch_all_voice_samples():
    """
    Pre-download and cache preview samples for every available voice.

    - ElevenLabs: downloads each voice's pre-recorded MP3 from the ElevenLabs CDN
      (no TTS token consumed) AND generates a float32 PCM sample via the Flash model.
    - Deepgram: synthesizes a short sample via the TTS API and caches float32 PCM.

    After this completes, all /voices/preview requests are served from disk cache
    without hitting any external API.
    """
    all_voices = await _get_all_tts_voices()
    sample_rate = 24000

    results: dict = {"ok": [], "failed": [], "skipped": []}

    async def _cache_one(voice: VoiceInfo) -> None:
        voice_id = voice.id

        # Already cached — skip.
        if _load_preview_cache(voice_id):
            results["skipped"].append(voice_id)
            return

        try:
            if voice.provider == "elevenlabs":
                # Step 1: download the pre-recorded MP3 (no token cost).
                from app.infrastructure.tts.elevenlabs_catalog import ensure_elevenlabs_preview_cached
                await ensure_elevenlabs_preview_cached(voice_id)

                # Step 2: synthesize via TTS to build the float32 PCM cache entry.
                if not os.getenv("ELEVENLABS_API_KEY"):
                    results["skipped"].append(voice_id)
                    return
                tts = ElevenLabsTTSProvider()
                await tts.initialize({
                    "voice_id": voice_id,
                    "model_id": "eleven_flash_v2_5",
                    "sample_rate": sample_rate,
                })
                chunks: List[bytes] = []
                async for chunk in tts.stream_synthesize(
                    text=_PREVIEW_SAMPLE_TEXT, voice_id=voice_id, sample_rate=sample_rate
                ):
                    chunks.append(chunk.data)
                await tts.cleanup()
                pcm = _linear16_to_float32le_bytes(b"".join(chunks))
                _save_preview_cache(voice_id, pcm)

            elif voice.provider == "cartesia":
                if not os.getenv("CARTESIA_API_KEY"):
                    results["skipped"].append(voice_id)
                    return
                tts = CartesiaTTSProvider()
                await tts.initialize(
                    {
                        "voice_id": voice_id,
                        "model_id": "sonic-3",
                        "sample_rate": sample_rate,
                    }
                )
                chunks = []
                async for chunk in tts.stream_synthesize(
                    text=_PREVIEW_SAMPLE_TEXT, voice_id=voice_id, sample_rate=sample_rate
                ):
                    chunks.append(chunk.data)
                await tts.cleanup()
                _save_preview_cache(voice_id, b"".join(chunks))

            elif voice.provider == "deepgram":
                if not os.getenv("DEEPGRAM_API_KEY"):
                    results["skipped"].append(voice_id)
                    return
                tts = DeepgramTTSProvider()
                await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
                chunks = []
                async for chunk in tts.stream_synthesize(
                    text=_PREVIEW_SAMPLE_TEXT, voice_id=voice_id, sample_rate=sample_rate
                ):
                    chunks.append(chunk.data)
                await tts.cleanup()
                pcm = _linear16_to_float32le_bytes(b"".join(chunks))
                _save_preview_cache(voice_id, pcm)

            else:
                # Google Chirp — preview works fine via on-demand TTS; skip prefetch.
                results["skipped"].append(voice_id)
                return

            results["ok"].append(voice_id)

        except Exception as exc:
            logger.warning("Prefetch failed for voice %s: %s", voice_id, exc)
            results["failed"].append({"voice_id": voice_id, "error": str(exc)})

    # Run all voices concurrently (bounded by provider rate limits in practice).
    await asyncio.gather(*(_cache_one(v) for v in all_voices), return_exceptions=True)

    return {
        "cached": len(results["ok"]),
        "skipped_already_cached": len(results["skipped"]),
        "failed": len(results["failed"]),
        "failures": results["failed"],
    }
