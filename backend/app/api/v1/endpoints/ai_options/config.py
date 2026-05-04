"""Tenant AI-config endpoints.

Endpoints:
  GET /config   - read current config; auto-correct stale voice IDs
  POST /config  - validate and persist new config; return latency advisories
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.v1.dependencies import get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.domain.models.ai_config import (
    AIProviderConfig,
    CARTESIA_MODELS,
    DEEPGRAM_TTS_MODELS,
    GEMINI_MODELS,
    GOOGLE_TTS_MODELS,
    GROQ_MODELS,
)
from app.infrastructure.tts.elevenlabs_catalog import (
    get_elevenlabs_tts_models_for_current_key,
    get_elevenlabs_voices_for_current_key,
)

from ._catalog import (
    _english_google_voices,
    _get_deepgram_voices_for_current_key,
    _get_live_cartesia_voices,
)
from ._shared import _fetch_tenant_config, _upsert_tenant_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Options"])


class AIProviderConfigWithWarnings(BaseModel):
    """AIProviderConfig plus soft latency warnings returned by save_config."""
    config: AIProviderConfig
    latency_warnings: list[str] = []


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
    from app.domain.models.ai_config import DeepgramTTSModel, GoogleTTSModel
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
