"""
AI Options Endpoint

Provides API for:
- Listing available LLM, STT, TTS providers and models
- Testing providers with latency measurement
- Saving/loading provider configuration

This endpoint is SEPARATE from the existing voice pipeline.
The selected configuration is used for actual phone calls.
"""
import os
import time
import base64
import struct
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Set

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.dotenv_compat import load_dotenv

# Load environment variables from .env file
load_dotenv()

from app.domain.models.ai_config import (
    AIProviderConfig,
    ProviderListResponse,
    LLMTestRequest,
    LLMTestResponse,
    TTSTestRequest,
    TTSTestResponse,
    VoiceInfo,
    GROQ_MODELS,
    GEMINI_MODELS,
    DEEPGRAM_MODELS,
    CARTESIA_MODELS,
    GOOGLE_TTS_MODELS,
    DEEPGRAM_TTS_MODELS,
    ELEVENLABS_TTS_MODELS,
    CARTESIA_VOICES,
    GOOGLE_CHIRP3_VOICES,
    DEEPGRAM_AURA2_VOICES,
)
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.llm.gemini import GeminiLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider
from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider
from app.infrastructure.tts.elevenlabs_catalog import (
    ensure_elevenlabs_preview_cached,
    get_elevenlabs_tts_models_for_current_key,
    get_elevenlabs_voice_by_id,
    get_elevenlabs_voices_for_current_key,
    get_elevenlabs_last_error,
    elevenlabs_enabled,
)
from app.domain.models.conversation import Message, MessageRole
from app.api.v1.dependencies import get_current_user, get_db_client
from app.core.postgres_adapter import Client


router = APIRouter(prefix="/ai-options", tags=["AI Options"])
logger = logging.getLogger(__name__)

_DEEPGRAM_MODELS_URL = "https://api.deepgram.com/v1/models"
_DEEPGRAM_MODELS_CACHE_TTL_SECONDS = 300.0
_deepgram_voice_cache_ids: Optional[Set[str]] = None
_deepgram_voice_cache_expires_at: float = 0.0
_deepgram_voice_cache_lock = asyncio.Lock()

_CARTESIA_VOICES_URL = "https://api.cartesia.ai/voices"
_CARTESIA_VOICE_CACHE_TTL_SECONDS = 300.0
_cartesia_voice_cache: Optional[List[VoiceInfo]] = None
_cartesia_voice_cache_expires_at: float = 0.0
_cartesia_voice_cache_lock = asyncio.Lock()
# Sync map updated by _get_live_cartesia_voices(); pre-seeded with static list so
# sync helpers work immediately before any async fetch has run.
_cartesia_voice_map: dict = {v.id: v for v in CARTESIA_VOICES}

# Disk cache for generated float32 PCM previews (keyed by provider + voice_id).
# ElevenLabs stores MP3 in its own cache dir; this stores decoded float32 for
# both Deepgram and ElevenLabs so /voices/preview is served from disk on
# subsequent calls without hitting any external API.
_VOICE_PREVIEW_CACHE_DIR = Path("/tmp/talky-voice-preview-cache")
_PREVIEW_SAMPLE_TEXT = "Hello, I am your AI voice assistant. How can I help you today?"


async def _fetch_tenant_config(conn, tenant_id: str) -> Optional[AIProviderConfig]:
    row = await conn.fetchrow(
        """
        SELECT
            llm_provider,
            llm_model,
            llm_temperature,
            llm_max_tokens,
            stt_provider,
            stt_model,
            stt_language,
            tts_provider,
            tts_model,
            tts_voice_id,
            tts_sample_rate
        FROM tenant_ai_configs
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if not row:
        return None

    return AIProviderConfig(
        llm_provider=row["llm_provider"],
        llm_model=row["llm_model"],
        llm_temperature=row["llm_temperature"],
        llm_max_tokens=row["llm_max_tokens"],
        stt_provider=row["stt_provider"],
        stt_model=row["stt_model"],
        stt_language=row["stt_language"],
        tts_provider=row["tts_provider"],
        tts_model=row["tts_model"],
        tts_voice_id=row["tts_voice_id"],
        tts_sample_rate=row["tts_sample_rate"],
    )


async def _upsert_tenant_config(conn, tenant_id: str, config: AIProviderConfig) -> None:
    await conn.execute(
        """
        INSERT INTO tenant_ai_configs (
            tenant_id,
            llm_provider,
            llm_model,
            llm_temperature,
            llm_max_tokens,
            stt_provider,
            stt_model,
            stt_language,
            tts_provider,
            tts_model,
            tts_voice_id,
            tts_sample_rate
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
        )
        ON CONFLICT (tenant_id) DO UPDATE SET
            llm_provider = EXCLUDED.llm_provider,
            llm_model = EXCLUDED.llm_model,
            llm_temperature = EXCLUDED.llm_temperature,
            llm_max_tokens = EXCLUDED.llm_max_tokens,
            stt_provider = EXCLUDED.stt_provider,
            stt_model = EXCLUDED.stt_model,
            stt_language = EXCLUDED.stt_language,
            tts_provider = EXCLUDED.tts_provider,
            tts_model = EXCLUDED.tts_model,
            tts_voice_id = EXCLUDED.tts_voice_id,
            tts_sample_rate = EXCLUDED.tts_sample_rate,
            updated_at = NOW()
        """,
        tenant_id,
        config.llm_provider,
        config.llm_model,
        config.llm_temperature,
        config.llm_max_tokens,
        config.stt_provider,
        config.stt_model,
        config.stt_language,
        config.tts_provider,
        config.tts_model,
        config.tts_voice_id,
        config.tts_sample_rate,
    )


def _find_google_voice(voice_id: str) -> Optional[VoiceInfo]:
    for voice in GOOGLE_CHIRP3_VOICES:
        if voice.id == voice_id:
            return voice
    return None


def _find_cartesia_voice(voice_id: str) -> Optional[VoiceInfo]:
    # Prefer live-fetched map; fall back to static list on first call.
    if _cartesia_voice_map:
        return _cartesia_voice_map.get(voice_id)
    for voice in CARTESIA_VOICES:
        if voice.id == voice_id:
            return voice
    return None


def _is_google_voice(voice_id: str) -> bool:
    return _find_google_voice(voice_id) is not None


def _is_cartesia_voice(voice_id: str) -> bool:
    if _cartesia_voice_map:
        return voice_id in _cartesia_voice_map
    return _find_cartesia_voice(voice_id) is not None


def _is_english_language(language: Optional[str]) -> bool:
    if not language:
        return False
    normalized = language.strip().lower()
    return normalized == "en" or normalized.startswith("en-") or normalized == "english"


def _english_google_voices() -> List[VoiceInfo]:
    return [voice for voice in GOOGLE_CHIRP3_VOICES if _is_english_language(voice.language)]


def _english_deepgram_static_voices() -> List[VoiceInfo]:
    return [voice for voice in DEEPGRAM_AURA2_VOICES if _is_english_language(voice.language)]


async def _get_live_deepgram_aura2_voice_ids() -> Optional[Set[str]]:
    """
    Query Deepgram's official models endpoint and return currently available
    Aura-2 model IDs for the configured API key.

    Reference:
    https://developers.deepgram.com/reference/speech-to-text/list-models
    """
    global _deepgram_voice_cache_ids, _deepgram_voice_cache_expires_at

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        return None

    now = time.time()
    if _deepgram_voice_cache_ids is not None and now < _deepgram_voice_cache_expires_at:
        return set(_deepgram_voice_cache_ids)

    async with _deepgram_voice_cache_lock:
        now = time.time()
        if _deepgram_voice_cache_ids is not None and now < _deepgram_voice_cache_expires_at:
            return set(_deepgram_voice_cache_ids)

        try:
            timeout = aiohttp.ClientTimeout(total=8)
            headers = {"Authorization": f"Token {api_key}"}
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_DEEPGRAM_MODELS_URL, headers=headers) as response:
                    if response.status != 200:
                        logger.warning(
                            "Deepgram models lookup failed with status %s; using docs list fallback.",
                            response.status,
                        )
                        return None
                    payload = await response.json()
        except Exception as exc:
            logger.warning(
                "Deepgram models lookup failed (%s); using docs list fallback.",
                exc,
            )
            return None

        ids: Set[str] = set()
        for model in payload.get("tts", []):
            canonical_name = model.get("canonical_name") or model.get("name")
            architecture = (model.get("architecture") or "").strip().lower()
            if not isinstance(canonical_name, str):
                continue
            if not canonical_name.startswith("aura-2-"):
                continue
            if architecture and architecture != "aura-2":
                continue
            ids.add(canonical_name)

        if not ids:
            logger.warning(
                "Deepgram models endpoint returned no Aura-2 voices; using docs list fallback."
            )
            return None

        _deepgram_voice_cache_ids = ids
        _deepgram_voice_cache_expires_at = time.time() + _DEEPGRAM_MODELS_CACHE_TTL_SECONDS
        return set(ids)


async def _get_deepgram_voices_for_current_key() -> List[VoiceInfo]:
    """
    Return Deepgram voices filtered to the current key's available Aura-2 models.
    Falls back to the docs-verified static list if lookup fails.
    """
    fallback_english = _english_deepgram_static_voices()
    live_ids = await _get_live_deepgram_aura2_voice_ids()
    if not live_ids:
        return fallback_english

    filtered = [
        voice
        for voice in DEEPGRAM_AURA2_VOICES
        if voice.id in live_ids and _is_english_language(voice.language)
    ]
    if not filtered:
        logger.warning(
            "No overlap between docs-verified Aura-2 voices and live account models; using English docs list fallback."
        )
        return fallback_english
    return filtered


async def _get_live_cartesia_voices() -> List[VoiceInfo]:
    """
    Fetch all public English voices from the Cartesia API, cache for 5 minutes,
    and update the module-level _cartesia_voice_map for sync lookups.
    Falls back to the static CARTESIA_VOICES list when the API is unreachable.
    """
    global _cartesia_voice_cache, _cartesia_voice_cache_expires_at, _cartesia_voice_map

    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        _cartesia_voice_map = {v.id: v for v in CARTESIA_VOICES}
        return list(CARTESIA_VOICES)

    now = time.time()
    if _cartesia_voice_cache is not None and now < _cartesia_voice_cache_expires_at:
        return list(_cartesia_voice_cache)

    async with _cartesia_voice_cache_lock:
        now = time.time()
        if _cartesia_voice_cache is not None and now < _cartesia_voice_cache_expires_at:
            return list(_cartesia_voice_cache)

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            headers = {"X-API-Key": api_key, "Cartesia-Version": "2024-06-10"}
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_CARTESIA_VOICES_URL, headers=headers) as response:
                    if response.status != 200:
                        logger.warning(
                            "Cartesia voices API returned status %s; using static fallback.",
                            response.status,
                        )
                        _cartesia_voice_map = {v.id: v for v in CARTESIA_VOICES}
                        return list(CARTESIA_VOICES)
                    payload = await response.json()
        except Exception as exc:
            logger.warning("Cartesia voices fetch failed (%s); using static fallback.", exc)
            _cartesia_voice_map = {v.id: v for v in CARTESIA_VOICES}
            return list(CARTESIA_VOICES)

        voices: List[VoiceInfo] = []
        for entry in payload:
            voice_id = entry.get("id", "")
            name = entry.get("name", "")
            language = (entry.get("language") or "").strip().lower()
            is_public = entry.get("is_public", True)
            if not voice_id or not name:
                continue
            if not is_public:
                continue
            # Keep only English voices (language == "en" or starts with "en-")
            if not (language == "en" or language.startswith("en-")):
                continue
            voices.append(
                VoiceInfo(
                    id=voice_id,
                    name=name,
                    provider="cartesia",
                    language="en",
                    gender=entry.get("gender"),
                    preview_url=None,
                )
            )

        if not voices:
            logger.warning("Cartesia API returned no public English voices; using static fallback.")
            _cartesia_voice_map = {v.id: v for v in CARTESIA_VOICES}
            return list(CARTESIA_VOICES)

        # Sort alphabetically by name for consistent UI ordering.
        voices.sort(key=lambda v: v.name.lower())

        _cartesia_voice_cache = voices
        _cartesia_voice_cache_expires_at = time.time() + _CARTESIA_VOICE_CACHE_TTL_SECONDS
        _cartesia_voice_map = {v.id: v for v in voices}
        logger.info("Cartesia live voice list refreshed: %d English voices.", len(voices))
        return list(voices)


async def _find_elevenlabs_voice(voice_id: str) -> Optional[VoiceInfo]:
    if not elevenlabs_enabled():
        return None
    return await get_elevenlabs_voice_by_id(voice_id)


async def _get_all_tts_voices() -> List[VoiceInfo]:
    cartesia_voices, deepgram_voices, elevenlabs_voices = await asyncio.gather(
        _get_live_cartesia_voices(),
        _get_deepgram_voices_for_current_key(),
        get_elevenlabs_voices_for_current_key(),
    )
    return [*cartesia_voices, *_english_google_voices(), *deepgram_voices, *elevenlabs_voices]


def _linear16_to_float32le_bytes(pcm16_data: bytes) -> bytes:
    """
    Convert little-endian linear16 PCM bytes to float32 little-endian bytes.
    Frontend preview playback expects float32 PCM payload.
    """
    if not pcm16_data:
        return b""
    sample_count = len(pcm16_data) // 2
    if sample_count == 0:
        return b""
    samples = struct.unpack(f"<{sample_count}h", pcm16_data[: sample_count * 2])
    return b"".join(
        struct.pack("<f", max(-1.0, min(1.0, sample / 32768.0)))
        for sample in samples
    )


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


@router.get("/providers", response_model=ProviderListResponse)
async def list_providers():
    """
    Get all available AI providers and their models.
    
    Returns:
        ProviderListResponse with LLM, STT, and TTS options
    """
    elevenlabs_models = (
        await get_elevenlabs_tts_models_for_current_key()
        if elevenlabs_enabled()
        else []
    )
    tts_providers = ["cartesia", "google", "deepgram"]
    tts_models = [
        *(model.model_dump() for model in CARTESIA_MODELS),
        *(model.model_dump() for model in GOOGLE_TTS_MODELS),
        *(model.model_dump() for model in DEEPGRAM_TTS_MODELS),
    ]
    if elevenlabs_enabled():
        tts_providers.append("elevenlabs")
        tts_models.extend(model.model_dump() for model in (elevenlabs_models or ELEVENLABS_TTS_MODELS))

    # LLM providers — Gemini is exposed only when the API key is configured;
    # without it, leaving the option in the dropdown leads to 503s on save.
    llm_providers: list[str] = ["groq"]
    llm_models = [model.model_dump() for model in GROQ_MODELS]
    if os.getenv("GEMINI_API_KEY"):
        llm_providers.append("gemini")
        llm_models.extend(model.model_dump() for model in GEMINI_MODELS)

    return ProviderListResponse(
        llm={
            "providers": llm_providers,
            "models": llm_models,
        },
        stt={
            "providers": ["deepgram"],
            "models": [model.model_dump() for model in DEEPGRAM_MODELS]
        },
        tts={
            "providers": tts_providers,
            "models": tts_models,
        }
    )


@router.get("/voices")
async def list_voices():
    """
    Get all available TTS voices.  Returns a JSON object with a `voices` list
    and an optional `elevenlabs_error` string when the ElevenLabs API key is
    invalid or the API returned an error.
    """
    voices = await _get_all_tts_voices()
    el_error = get_elevenlabs_last_error() if elevenlabs_enabled() else None
    response: dict = {"voices": [v.model_dump() for v in voices]}
    if el_error:
        response["elevenlabs_error"] = el_error
    return response


@router.get("/voices/{voice_id}/sample")
async def get_voice_sample(voice_id: str):
    """
    Serve a cached ElevenLabs preview sample without generating fresh TTS.
    """
    voice = await _find_elevenlabs_voice(voice_id)
    if voice is None or voice.provider != "elevenlabs":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preview sample not available for this voice",
        )

    sample_path = await ensure_elevenlabs_preview_cached(voice_id)
    if sample_path is None or not sample_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Voice sample unavailable",
        )

    return FileResponse(
        sample_path,
        media_type="audio/mpeg",
        filename=f"{voice_id}.mp3",
        headers={"cache-control": "public, max-age=86400"},
    )


class VoicePreviewRequest(BaseModel):
    """Request for voice preview"""
    voice_id: str
    text: str = "Hello, I am your AI voice assistant. How can I help you today?"


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


@router.post("/test/llm", response_model=LLMTestResponse)
async def test_llm(request: LLMTestRequest):
    """
    Test LLM with a message and measure latency.
    
    Streams the response and tracks:
    - First token latency
    - Total response time
    - Token count
    
    Args:
        request: LLMTestRequest with model, message, and parameters
    
    Returns:
        LLMTestResponse with response text and latency metrics
    """
    gemini_model_ids = {m.id for m in GEMINI_MODELS}
    is_gemini = request.model in gemini_model_ids

    if is_gemini:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gemini API key not configured. Set GEMINI_API_KEY in .env."
            )
    else:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Groq API key not configured"
            )

    try:
        llm = GeminiLLMProvider() if is_gemini else GroqLLMProvider()
        await llm.initialize({
            "api_key": api_key,
            "model": request.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens
        })
        
        messages = [Message(role=MessageRole.USER, content=request.message)]
        system_prompt = "You are a helpful assistant. Keep responses concise and natural."
        
        start_time = time.time()
        first_token_time: Optional[float] = None
        response_text = ""
        token_count = 0
        
        async for token in llm.stream_chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            model=request.model
        ):
            if first_token_time is None:
                first_token_time = time.time()
            response_text += token
            token_count += 1
        
        end_time = time.time()
        
        await llm.cleanup()
        
        first_token_ms = ((first_token_time or end_time) - start_time) * 1000
        total_latency_ms = (end_time - start_time) * 1000
        
        return LLMTestResponse(
            response=response_text,
            latency_ms=total_latency_ms,
            first_token_ms=first_token_ms,
            total_tokens=token_count,
            model=request.model
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM test failed: {str(e)}"
        )


@router.post("/test/tts", response_model=TTSTestResponse)
async def test_tts(request: TTSTestRequest):
    """
    Test TTS with text and measure latency.
    
    Synthesizes audio and tracks:
    - First audio chunk latency
    - Total synthesis time
    - Audio duration
    
    Args:
        request: TTSTestRequest with model, voice_id, text, sample_rate
    
    Returns:
        TTSTestResponse with base64 audio and latency metrics
    """
    try:
        tts = None
        voice_id = request.voice_id
        sample_rate = request.sample_rate or 24000
        _, deepgram_voices = await asyncio.gather(
            _get_live_cartesia_voices(),
            _get_deepgram_voices_for_current_key(),
        )
        deepgram_voice_ids = {voice.id for voice in deepgram_voices}
        deepgram_static_voice_ids = {voice.id for voice in _english_deepgram_static_voices()}
        elevenlabs_voice = await _find_elevenlabs_voice(voice_id)

        if _is_cartesia_voice(voice_id):
            if not os.getenv("CARTESIA_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Cartesia API key not configured",
                )
            tts = CartesiaTTSProvider()
            await tts.initialize(
                {
                    "voice_id": voice_id,
                    "model_id": request.model or "sonic-3",
                    "sample_rate": sample_rate,
                }
            )
            output_is_linear16 = False
            model_name = request.model or "sonic-3"
        elif _is_google_voice(voice_id):
            import os as _os

            backend_dir = _os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))))
            )
            creds_path = _os.path.join(backend_dir, "config", "google-service-account.json")
            if not _os.path.exists(creds_path):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Google service account file not found",
                )
            _os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
            tts = GoogleTTSStreamingProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
            output_is_linear16 = False
            model_name = "Chirp3-HD"
        elif voice_id in deepgram_voice_ids or voice_id in deepgram_static_voice_ids:
            if not os.getenv("DEEPGRAM_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Deepgram API key not configured",
                )
            tts = DeepgramTTSProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
            output_is_linear16 = True
            model_name = "aura-2"
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
                    "model_id": request.model or "eleven_flash_v2_5",
                    "sample_rate": sample_rate,
                }
            )
            output_is_linear16 = True
            model_name = request.model or "eleven_flash_v2_5"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown voice_id",
            )

        start_time = time.time()
        first_audio_time: Optional[float] = None
        audio_chunks: List[bytes] = []

        async for chunk in tts.stream_synthesize(
            text=request.text,
            voice_id=voice_id,
            sample_rate=sample_rate
        ):
            if first_audio_time is None:
                first_audio_time = time.time()
            audio_chunks.append(chunk.data)

        end_time = time.time()

        await tts.cleanup()

        combined_audio = b"".join(audio_chunks)
        if output_is_linear16:
            combined_audio = _linear16_to_float32le_bytes(combined_audio)
        audio_base64 = base64.b64encode(combined_audio).decode("utf-8")

        duration_seconds = len(combined_audio) / (sample_rate * 4)

        first_audio_ms = ((first_audio_time or end_time) - start_time) * 1000
        total_latency_ms = (end_time - start_time) * 1000

        return TTSTestResponse(
            audio_base64=audio_base64,
            latency_ms=total_latency_ms,
            first_audio_ms=first_audio_ms,
            duration_seconds=duration_seconds,
            model=model_name,
            voice_id=voice_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TTS test failed: {str(e)}"
        )


@router.get("/config", response_model=AIProviderConfig)
async def get_config(
    current_user=Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Get current AI provider configuration for the user's tenant.
    
    Returns:
        AIProviderConfig with current settings
    """
    from app.domain.services.global_ai_config import set_global_config

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant",
        )

    async with db_client.pool.acquire() as conn:
        config = await _fetch_tenant_config(conn, tenant_id)
        if config is None:
            config = AIProviderConfig()
            await _upsert_tenant_config(conn, tenant_id, config)
        elif config.tts_provider == "deepgram":
            deepgram_voices = await _get_deepgram_voices_for_current_key()
            valid_voice_ids = {voice.id for voice in deepgram_voices}
            if valid_voice_ids and config.tts_voice_id not in valid_voice_ids:
                old_voice_id = config.tts_voice_id
                config.tts_voice_id = deepgram_voices[0].id
                config.tts_model = "aura-2"
                if config.tts_sample_rate not in {8000, 16000, 24000, 32000, 48000}:
                    config.tts_sample_rate = 24000
                await _upsert_tenant_config(conn, tenant_id, config)
                logger.info(
                    "Auto-corrected invalid Deepgram voice id '%s' to '%s' for tenant %s",
                    old_voice_id,
                    config.tts_voice_id,
                    tenant_id,
                )
        elif config.tts_provider == "cartesia":
            cartesia_voices = await _get_live_cartesia_voices()
            valid_voice_ids = {voice.id for voice in cartesia_voices}
            if valid_voice_ids and config.tts_voice_id not in valid_voice_ids:
                old_voice_id = config.tts_voice_id
                config.tts_voice_id = cartesia_voices[0].id
                config.tts_model = "sonic-3"
                if config.tts_sample_rate not in {8000, 16000, 24000, 32000, 44100}:
                    config.tts_sample_rate = 24000
                await _upsert_tenant_config(conn, tenant_id, config)
                logger.info(
                    "Auto-corrected stale Cartesia voice id '%s' to '%s' for tenant %s",
                    old_voice_id,
                    config.tts_voice_id,
                    tenant_id,
                )
        elif config.tts_provider == "elevenlabs":
            elevenlabs_voices = await get_elevenlabs_voices_for_current_key()
            valid_voice_ids = {voice.id for voice in elevenlabs_voices}
            if valid_voice_ids and config.tts_voice_id not in valid_voice_ids:
                elevenlabs_models = await get_elevenlabs_tts_models_for_current_key()
                old_voice_id = config.tts_voice_id
                config.tts_voice_id = elevenlabs_voices[0].id
                config.tts_model = elevenlabs_models[0].id if elevenlabs_models else "eleven_flash_v2_5"
                if config.tts_sample_rate not in {8000, 16000, 22050, 24000, 44100}:
                    config.tts_sample_rate = 24000
                await _upsert_tenant_config(conn, tenant_id, config)
                logger.info(
                    "Auto-corrected invalid ElevenLabs voice id '%s' to '%s' for tenant %s",
                    old_voice_id,
                    config.tts_voice_id,
                    tenant_id,
                )

    # Keep voice pipeline in sync with tenant-selected config.
    set_global_config(config)
    return config


class AIProviderConfigWithWarnings(BaseModel):
    """AIProviderConfig plus soft latency warnings returned by save_config."""
    config: AIProviderConfig
    latency_warnings: list[str] = []


@router.post("/config", response_model=AIProviderConfigWithWarnings)
async def save_config(
    config: AIProviderConfig,
    current_user=Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Save AI provider configuration GLOBALLY.

    This configuration is used for ALL voice interactions:
    - Dummy calls
    - Real phone calls
    - SIP calls
    - Voice pipeline throughout the application

    Args:
        config: AIProviderConfig with desired settings

    Returns:
        AIProviderConfigWithWarnings — saved config plus soft latency advisory warnings
    """
    from app.domain.models.ai_config import GoogleTTSModel, DeepgramTTSModel
    from app.domain.services.global_ai_config import set_global_config

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant",
        )

    if config.tts_provider not in {"cartesia", "google", "deepgram", "elevenlabs"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TTS provider. Supported providers: cartesia, google, deepgram, elevenlabs.",
        )

    # Validate LLM provider + model. Sourced from a single union so adding a
    # new provider only requires extending GEMINI_MODELS / GROQ_MODELS — never
    # editing this validator.
    _llm_models_by_provider: dict[str, list[str]] = {
        "groq": [m.id for m in GROQ_MODELS],
        "gemini": [m.id for m in GEMINI_MODELS],
    }

    if config.llm_provider not in _llm_models_by_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid LLM provider '{config.llm_provider}'. "
                f"Supported: {sorted(_llm_models_by_provider.keys())}"
            ),
        )

    # Cross-field check: model must belong to the selected provider, otherwise
    # the orchestrator will pass a Groq model name to Gemini (or vice-versa)
    # and fail at first stream call.
    valid_llm_models = _llm_models_by_provider[config.llm_provider]
    if config.llm_model not in valid_llm_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid LLM model '{config.llm_model}' for provider "
                f"'{config.llm_provider}'. Must be one of: {valid_llm_models}"
            ),
        )

    # Refuse to save a Gemini config if the API key isn't present — caught
    # here gives a clear 503 instead of a confusing pipeline error mid-call.
    if config.llm_provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini API key not configured. Set GEMINI_API_KEY in .env.",
        )

    if config.tts_provider == "cartesia":
        if not os.getenv("CARTESIA_API_KEY"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cartesia API key not configured. Set CARTESIA_API_KEY in .env.",
            )
        valid_tts_models = [m.id for m in CARTESIA_MODELS]
        live_cartesia = await _get_live_cartesia_voices()
        valid_voice_ids = {voice.id for voice in live_cartesia}
    elif config.tts_provider == "google":
        valid_tts_models = [m.id for m in GOOGLE_TTS_MODELS]
        valid_voice_ids = {voice.id for voice in _english_google_voices()}
    elif config.tts_provider == "deepgram":
        valid_tts_models = [m.id for m in DEEPGRAM_TTS_MODELS]
        deepgram_voices = await _get_deepgram_voices_for_current_key()
        valid_voice_ids = {voice.id for voice in deepgram_voices}
    else:
        valid_tts_models = [m.id for m in await get_elevenlabs_tts_models_for_current_key()]
        elevenlabs_voices = await get_elevenlabs_voices_for_current_key()
        valid_voice_ids = {voice.id for voice in elevenlabs_voices}

    if config.tts_model not in valid_tts_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid TTS model. Must be one of: {valid_tts_models}"
        )
    if config.tts_voice_id not in valid_voice_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TTS voice for current provider or not available for this Deepgram key",
        )

    if (
        config.tts_provider == "google"
        and config.tts_model == GoogleTTSModel.CHIRP3_HD.value
        and config.tts_sample_rate != 24000
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chirp3-HD requires sample rate 24000",
        )

    if (
        config.tts_provider == "deepgram"
        and config.tts_model == DeepgramTTSModel.AURA_2.value
        and config.tts_sample_rate not in {8000, 16000, 24000, 32000, 48000}
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aura-2 sample_rate must be one of 8000, 16000, 24000, 32000, 48000",
        )

    if (
        config.tts_provider == "elevenlabs"
        and config.tts_sample_rate not in {8000, 16000, 22050, 24000, 44100}
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ElevenLabs sample_rate must be one of 8000, 16000, 22050, 24000, 44100",
        )

    async with db_client.pool.acquire() as conn:
        await _upsert_tenant_config(conn, tenant_id, config)

    # Compute soft latency warnings — advisory only, never blocks saving.
    # Sources: Groq official docs 2025, Cresta voice latency post 2025.
    FAST_MODELS = {
        "llama-3.1-8b-instant",
        "openai/gpt-oss-20b",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    }
    SLOW_MODELS = {
        "openai/gpt-oss-120b",
        "moonshotai/kimi-k2-instruct-0905",
    }
    PREVIEW_MODELS = {
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "qwen/qwen3-32b",
        "moonshotai/kimi-k2-instruct-0905",
    }

    latency_warnings: list[str] = []

    if config.llm_model in SLOW_MODELS:
        latency_warnings.append(
            f"'{config.llm_model}' is a large reasoning model. "
            "Expected TTFT: 300–600ms vs ~90ms for llama-3.1-8b-instant. "
            "Recommended for quality use cases, not real-time voice."
        )
    elif config.llm_model not in FAST_MODELS:
        latency_warnings.append(
            f"'{config.llm_model}' has moderate latency (~150–250ms TTFT). "
            "For lowest latency, use llama-3.1-8b-instant (560 t/s on Groq)."
        )

    if config.llm_model in PREVIEW_MODELS:
        latency_warnings.append(
            f"'{config.llm_model}' is a preview model. "
            "Preview models may have higher latency, rate limits, or instability in production."
        )

    if config.llm_max_tokens > 150:
        latency_warnings.append(
            f"llm_max_tokens={config.llm_max_tokens} allows long responses. "
            "Each extra 50 tokens adds ~50–100ms TTS latency per turn. "
            "Voice guideline: keep under 100 tokens (1–2 sentences)."
        )
    elif config.llm_max_tokens > 100:
        latency_warnings.append(
            f"llm_max_tokens={config.llm_max_tokens}. "
            "Voice guideline is 90 tokens (2 sentences). "
            "Higher values may produce longer responses than callers expect."
        )

    # Applies immediately to active voice pipeline selection.
    set_global_config(config)
    return AIProviderConfigWithWarnings(config=config, latency_warnings=latency_warnings)


class LatencyBenchmarkResponse(BaseModel):
    """Response from latency benchmark"""
    llm_first_token_ms: float
    llm_total_ms: float
    tts_first_audio_ms: float
    tts_total_ms: float
    total_pipeline_ms: float


@router.post("/benchmark", response_model=LatencyBenchmarkResponse)
async def run_benchmark(config: AIProviderConfig):
    """
    Run a full pipeline latency benchmark.
    
    Tests LLM and TTS with the specified configuration.
    
    Args:
        config: AIProviderConfig to benchmark
    
    Returns:
        LatencyBenchmarkResponse with detailed latency metrics
    """
    import os as _os

    is_gemini = config.llm_provider == "gemini" or config.llm_model in {m.id for m in GEMINI_MODELS}

    if is_gemini:
        llm_key = os.getenv("GEMINI_API_KEY")
        if not llm_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gemini API key not configured. Set GEMINI_API_KEY in .env."
            )
    else:
        llm_key = os.getenv("GROQ_API_KEY")
        if not llm_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Groq API key not configured"
            )

    voice_id = config.tts_voice_id
    sample_rate = config.tts_sample_rate

    try:
        # Initialize providers
        llm = GeminiLLMProvider() if is_gemini else GroqLLMProvider()
        await llm.initialize({
            "api_key": llm_key,
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens
        })
        
        if config.tts_provider == "google":
            backend_dir = _os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))))
            )
            creds_path = _os.path.join(backend_dir, "config", "google-service-account.json")
            if not _os.path.exists(creds_path):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Google service account file not found",
                )
            _os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
            tts = GoogleTTSStreamingProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
        elif config.tts_provider == "deepgram":
            if not os.getenv("DEEPGRAM_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Deepgram API key not configured",
                )
            tts = DeepgramTTSProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
        elif config.tts_provider == "elevenlabs":
            if not os.getenv("ELEVENLABS_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="ElevenLabs API key not configured",
                )
            tts = ElevenLabsTTSProvider()
            await tts.initialize(
                {
                    "voice_id": voice_id,
                    "model_id": config.tts_model or "eleven_flash_v2_5",
                    "sample_rate": sample_rate,
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported TTS provider '{config.tts_provider}' for benchmark",
            )
        
        # Benchmark LLM
        messages = [Message(role=MessageRole.USER, content="Hello, how are you?")]
        
        llm_start = time.time()
        llm_first_token_time: Optional[float] = None
        llm_response = ""
        
        async for token in llm.stream_chat(
            messages=messages,
            system_prompt="Be brief.",
            model=config.llm_model
        ):
            if llm_first_token_time is None:
                llm_first_token_time = time.time()
            llm_response += token
        
        llm_end = time.time()
        
        llm_first_token_ms = ((llm_first_token_time or llm_end) - llm_start) * 1000
        llm_total_ms = (llm_end - llm_start) * 1000
        
        # Benchmark TTS
        tts_start = time.time()
        tts_first_audio_time: Optional[float] = None
        
        async for chunk in tts.stream_synthesize(
            text=llm_response,
            voice_id=voice_id,
            sample_rate=sample_rate
        ):
            if tts_first_audio_time is None:
                tts_first_audio_time = time.time()
        
        tts_end = time.time()
        
        tts_first_audio_ms = ((tts_first_audio_time or tts_end) - tts_start) * 1000
        tts_total_ms = (tts_end - tts_start) * 1000
        
        # Cleanup
        await llm.cleanup()
        await tts.cleanup()
        
        return LatencyBenchmarkResponse(
            llm_first_token_ms=llm_first_token_ms,
            llm_total_ms=llm_total_ms,
            tts_first_audio_ms=tts_first_audio_ms,
            tts_total_ms=tts_total_ms,
            total_pipeline_ms=llm_total_ms + tts_total_ms
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Benchmark failed: {str(e)}"
        )
