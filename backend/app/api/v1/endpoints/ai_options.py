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
    DEEPGRAM_MODELS,
    GOOGLE_TTS_MODELS,
    DEEPGRAM_TTS_MODELS,
    ELEVENLABS_TTS_MODELS,
    GOOGLE_CHIRP3_VOICES,
    DEEPGRAM_AURA2_VOICES,
)
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider
from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider
from app.infrastructure.tts.elevenlabs_catalog import (
    ensure_elevenlabs_preview_cached,
    get_elevenlabs_tts_models_for_current_key,
    get_elevenlabs_voice_by_id,
    get_elevenlabs_voices_for_current_key,
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


def _is_google_voice(voice_id: str) -> bool:
    return _find_google_voice(voice_id) is not None


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


async def _find_elevenlabs_voice(voice_id: str) -> Optional[VoiceInfo]:
    if not elevenlabs_enabled():
        return None
    return await get_elevenlabs_voice_by_id(voice_id)


async def _get_all_tts_voices() -> List[VoiceInfo]:
    deepgram_voices = await _get_deepgram_voices_for_current_key()
    elevenlabs_voices = await get_elevenlabs_voices_for_current_key()
    return [*_english_google_voices(), *deepgram_voices, *elevenlabs_voices]


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
    tts_providers = ["google", "deepgram"]
    tts_models = [
        *(model.model_dump() for model in GOOGLE_TTS_MODELS),
        *(model.model_dump() for model in DEEPGRAM_TTS_MODELS),
    ]
    if elevenlabs_enabled():
        tts_providers.append("elevenlabs")
        tts_models.extend(model.model_dump() for model in (elevenlabs_models or ELEVENLABS_TTS_MODELS))

    return ProviderListResponse(
        llm={
            "providers": ["groq"],
            "models": [model.model_dump() for model in GROQ_MODELS]
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


@router.get("/voices", response_model=List[VoiceInfo])
async def list_voices():
    """
    Get all available TTS voices (curated list for voice agents).
    
    Returns curated voices optimized for voice AI agents.
    These voices are pre-selected for clarity, naturalness, and
    suitability for business calls.
    
    Includes:
    - Google Chirp 3 HD voices
    - Deepgram Aura-2 voices
    
    Returns:
        List of VoiceInfo with voice details and preview info
    """
    return await _get_all_tts_voices()


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
    - Google Chirp3-HD voices
    - Deepgram Aura-2 voices
    
    Args:
        request: VoicePreviewRequest with voice_id and optional text
    
    Returns:
        VoicePreviewResponse with base64 audio data
    """
    try:
        tts = None
        deepgram_voices = await _get_deepgram_voices_for_current_key()
        deepgram_voice_map = {voice.id: voice for voice in deepgram_voices}
        deepgram_static_voice_map = {
            voice.id: voice
            for voice in _english_deepgram_static_voices()
        }
        elevenlabs_voice = await _find_elevenlabs_voice(request.voice_id)

        voice_info = (
            _find_google_voice(request.voice_id)
            or deepgram_voice_map.get(request.voice_id)
            or deepgram_static_voice_map.get(request.voice_id)
            or elevenlabs_voice
        )
        voice_name = voice_info.name if voice_info else "Unknown Voice"
        voice_id = request.voice_id
        sample_rate = 24000

        if _is_google_voice(voice_id):
            backend_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            )
            creds_path = os.path.join(backend_dir, "config", "google-service-account.json")
            if not os.path.exists(creds_path):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Google service account file not found at: {creds_path}",
                )
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
            tts = GoogleTTSStreamingProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
            output_is_linear16 = False
        elif voice_id in deepgram_voice_map or voice_id in deepgram_static_voice_map:
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
            # Deepgram returns linear16 PCM; convert to float32 for frontend preview playback.
            combined_audio = _linear16_to_float32le_bytes(combined_audio)
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
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Groq API key not configured"
        )
    
    try:
        llm = GroqLLMProvider()
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
        deepgram_voices = await _get_deepgram_voices_for_current_key()
        deepgram_voice_ids = {voice.id for voice in deepgram_voices}
        deepgram_static_voice_ids = {voice.id for voice in _english_deepgram_static_voices()}
        elevenlabs_voice = await _find_elevenlabs_voice(voice_id)

        if _is_google_voice(voice_id):
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


@router.post("/config", response_model=AIProviderConfig)
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
        Saved AIProviderConfig
    """
    from app.domain.models.ai_config import GoogleTTSModel, DeepgramTTSModel
    from app.domain.services.global_ai_config import set_global_config

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant",
        )

    if config.tts_provider not in {"google", "deepgram", "elevenlabs"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TTS provider. Supported providers: google, deepgram, elevenlabs.",
        )
    
    # Validate LLM model
    valid_llm_models = [m.id for m in GROQ_MODELS]
    if config.llm_model not in valid_llm_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid LLM model. Must be one of: {valid_llm_models}"
        )
    
    if config.tts_provider == "google":
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

    # Applies immediately to active voice pipeline selection.
    set_global_config(config)
    return config


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
    
    groq_key = os.getenv("GROQ_API_KEY")
    
    if not groq_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Groq API key not configured"
        )
    
    voice_id = config.tts_voice_id
    sample_rate = config.tts_sample_rate

    try:
        # Initialize providers
        llm = GroqLLMProvider()
        await llm.initialize({
            "api_key": groq_key,
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
