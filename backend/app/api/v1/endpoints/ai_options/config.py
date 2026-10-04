"""Tenant AI-config endpoints.

Endpoints:
  GET /config   - read current config without replacing saved selections
  POST /config  - validate and persist new config; return latency advisories
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.v1.dependencies import get_current_user, get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.domain.models.ai_config import (
    AIProviderConfig,
    CARTESIA_MODELS,
    DEEPGRAM_TTS_MODELS,
    GOOGLE_TTS_MODELS,
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
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant",
        )

    # tenant_ai_configs is under FORCE RLS (Alembic 0038) and the app role has
    # no BYPASSRLS: a bare acquire sees zero rows and its bootstrap INSERT is
    # refused by WITH CHECK (prod 500 on 2026-09-02). Set the tenant GUC.
    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        config = await _fetch_tenant_config(conn, tenant_id)
        if config is None:
            config = AIProviderConfig()
            await _upsert_tenant_config(conn, tenant_id, config)
        elif config.pipeline_mode == "realtime":
            from app.realtime.config import normalize_realtime_settings
            config.realtime_settings = normalize_realtime_settings(config.realtime_settings)
            return config

    # NOTE: this GET handler MUST NOT mutate any process-global state. It used
    # to call set_global_config(config), which meant merely VIEWING the
    # AI-Options page overwrote the model/provider/pipeline that every OTHER
    # tenant's live call read off the shared singleton (cross-tenant model
    # bleed). Per-call config is now sourced per-tenant from tenant_ai_configs
    # via tenant_ai_config_resolver. Existing selections stay intact even if
    # a provider catalog is temporarily incomplete; explicit saves validate them.
    return config


@router.post("/config", response_model=AIProviderConfigWithWarnings)
async def save_config(
    config: AIProviderConfig,
    current_user=Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Save AI provider configuration for the authenticated tenant.

    This configuration supplies this tenant's defaults for voice interactions:
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

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant",
        )

    from app.domain.services.voice_tuning import get_voice_tuning_resolver
    try:
        config.voice_tuning = get_voice_tuning_resolver().validate_user_partial_for_tenant(config.voice_tuning or {}, tenant_id) or None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if config.pipeline_mode not in {"cascaded", "realtime"}:
        raise HTTPException(400, "Invalid voice pipeline")
    if config.pipeline_mode == "realtime":
        from app.realtime.config import validate_realtime
        from app.domain.services.credential_resolver import get_credential_resolver
        try:
            config.realtime_settings = validate_realtime(config.realtime_model, config.realtime_voice, config.realtime_settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        provider = str((config.realtime_settings or {}).get("provider") or "openai").lower()
        if not await get_credential_resolver().resolve(provider, tenant_id=tenant_id):
            raise HTTPException(400, f"{provider} is not configured for Realtime")
        async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
            await _upsert_tenant_config(conn, tenant_id, config)
        return AIProviderConfigWithWarnings(config=config, latency_warnings=[])

    if config.tts_provider not in {"cartesia", "google", "deepgram", "elevenlabs"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TTS provider. Supported providers: cartesia, google, deepgram, elevenlabs.",
        )

    # Save and runtime use the same offered + legacy provider/model contract.
    from app.domain.models.ai_config import validate_traditional_llm_selection
    from app.domain.services.telephony_session_config import resolve_stt_selection
    try:
        resolve_stt_selection(config)
        validate_traditional_llm_selection(config.llm_provider, config.llm_model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Refuse to save a Gemini config if the API key isn't present — caught
    # here gives a clear 503 instead of a confusing pipeline error mid-call.
    if config.llm_provider == "openai":
        from app.domain.services.credential_resolver import get_credential_resolver
        key = await get_credential_resolver().resolve("openai", tenant_id=str(current_user.tenant_id))
        if not key:
            raise HTTPException(status_code=503, detail="OpenAI API key not configured")
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
        from app.domain.services.voice_eligibility import (
            VoiceEligibilityError, require_elevenlabs_voice_eligible,
        )
        valid_tts_models = [m.id for m in await get_elevenlabs_tts_models_for_current_key()]
        elevenlabs_voices = await get_elevenlabs_voices_for_current_key()
        try:
            await require_elevenlabs_voice_eligible(db_client.pool, tenant_id, config.tts_voice_id, voices=elevenlabs_voices)
        except VoiceEligibilityError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        valid_voice_ids = {voice.id for voice in elevenlabs_voices}

    if config.tts_model not in valid_tts_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid TTS model. Must be one of: {valid_tts_models}"
        )
    if config.tts_voice_id not in valid_voice_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected voice is not available for this TTS provider. Reload the catalog or select another voice.",
        )

    if config.tts_provider == "cartesia":
        from app.infrastructure.tts.cartesia import CARTESIA_SAMPLE_RATES
        if config.tts_sample_rate not in CARTESIA_SAMPLE_RATES:
            raise HTTPException(400, f"Cartesia sample_rate must be one of {sorted(CARTESIA_SAMPLE_RATES)}")

    if (
        config.tts_provider == "google"
        and config.tts_model == GoogleTTSModel.CHIRP3_HD.value
        and config.tts_sample_rate != 24000
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google TTS settings use sample rate 24000; phone calls adapt audio to their transport",
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

    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        await _upsert_tenant_config(conn, tenant_id, config)

    # Compute soft latency warnings — advisory only, never blocks saving.
    # Sources: Groq official docs 2025, Cresta voice latency post 2025.
    # Rebuilt 2026-08-17 from measurement on this account rather than from the
    # vendor's published throughput. The old set named llama-3.1-8b-instant,
    # which 404s here, so the "for lowest latency use X" advice pointed at a
    # model nobody could select.
    #
    # Measured, 7,281-token prompt, warm (second identical request):
    #   openai/gpt-oss-120b   102ms TTFT   (prompt caching hit 7168/7281)
    #   openai/gpt-oss-20b    119ms TTFT   (prompt caching hit 7168/7281)
    #   qwen/qwen3.6-27b      672ms TTFT   (no caching on this model)
    FAST_MODELS = {
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    }
    SLOW_MODELS: set[str] = set()
    PREVIEW_MODELS = {
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        # Superseded by the GA "gemini-3.1-flash-lite"; still selectable so
        # already-saved tenants can write their config. Warn on it so nobody
        # newly adopts a preview id that Google can retire.
        "gemini-3.1-flash-lite-preview",
    }

    latency_warnings: list[str] = []

    # Realtime (gpt-realtime-2) does NOT use the cascaded LLM/STT/TTS pipeline,
    # so the cascaded-latency advisories below are irrelevant — return none.
    # (This was surfacing a floating "latency" notification about the cascaded
    # LLM/tokens even when the user had switched to Realtime.)
    if (getattr(config, "pipeline_mode", "cascaded") or "cascaded") == "realtime":
        return AIProviderConfigWithWarnings(config=config, latency_warnings=[])

    if config.llm_model in SLOW_MODELS:
        latency_warnings.append(
            f"'{config.llm_model}' is a large reasoning model. "
            "Expected TTFT: 300–600ms vs ~100ms for openai/gpt-oss-20b. "
            "Recommended for quality use cases, not real-time voice."
        )
    elif config.llm_model not in FAST_MODELS and config.llm_provider != "openai":
        latency_warnings.append(
            f"'{config.llm_model}' has moderate latency (~150–250ms TTFT). "
            "For lowest latency, use openai/gpt-oss-20b on Groq or gpt-oss-120b on Cerebras."
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

    # Persisted per-tenant to tenant_ai_configs above. Live calls pick this up
    # per-tenant via tenant_ai_config_resolver (keyed on the call's tenant_id) —
    # the change lands on the tenant's NEXT call with no restart, and does NOT
    # touch any other tenant's calls. We deliberately do NOT call
    # set_global_config here anymore (that made the last tenant to save "win"
    # the process-global that everyone else's calls read).
    return AIProviderConfigWithWarnings(config=config, latency_warnings=latency_warnings)
