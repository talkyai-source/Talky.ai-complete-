"""Read-only catalog endpoints — provider list, voice list, voice sample.

Endpoints:
  GET /providers                          - LLM/STT/TTS providers + models
  GET /voices                             - merged TTS voices across providers
  GET /voices/{voice_id}/sample           - cached ElevenLabs preview MP3
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.v1.dependencies import get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.domain.services.voice_eligibility import (
    VoiceEligibilityError, filter_tenant_voices, require_elevenlabs_voice_eligible,
)

from app.domain.models.ai_config import (
    CARTESIA_MODELS,
    DEEPGRAM_MODELS,
    DEEPGRAM_TTS_MODELS,
    ELEVENLABS_TTS_MODELS,
    CEREBRAS_MODELS,
    GEMINI_MODELS,
    GOOGLE_TTS_MODELS,
    GROQ_MODELS,
    OPENAI_MODELS,
    ProviderListResponse,
    STT_ENGINES,
)
from app.infrastructure.tts.elevenlabs_catalog import (
    elevenlabs_enabled,
    ensure_elevenlabs_preview_cached,
    get_elevenlabs_last_error,
    get_elevenlabs_tts_models_for_current_key,
)

from app.realtime.catalog import REALTIME_MODEL, REALTIME_NOISE_REDUCTION, REALTIME_TURN_DETECTION, REALTIME_VOICES

from ._catalog import _find_elevenlabs_voice, _get_all_tts_voices

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Options"])


@router.get("/providers", response_model=ProviderListResponse)
async def list_providers(current_user=Depends(get_current_user)):
    """
    Get all available AI providers and their models.

    Returns:
        ProviderListResponse with LLM, STT, and TTS options
    """
    from app.realtime.credentials import resolve_openai_key
    realtime_available = bool(await resolve_openai_key(getattr(current_user, "tenant_id", None)))
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

    # The catalog drives the traditional picker; native Realtime stays separate.
    llm_providers: list[str] = ["groq"]
    llm_models = [model.model_dump() for model in GROQ_MODELS]
    if realtime_available:
        llm_providers.append("openai")
        llm_models.extend(model.model_dump() for model in OPENAI_MODELS)
    if os.getenv("CEREBRAS_API_KEY"):
        llm_providers.append("cerebras")
        llm_models.extend(model.model_dump() for model in CEREBRAS_MODELS)
    # 2026-10-01 (owner decision): Google is offered again, with exactly ONE
    # model -- Gemini 3.8 Flash. The older Gemini ids stay accepted on save
    # (a tenant may have one stored) but are never shown.
    if os.getenv("GEMINI_API_KEY"):
        gemini_offered = [m for m in GEMINI_MODELS if m.id == "gemini-3.8-flash"]
        if gemini_offered:
            llm_providers.append("gemini")
            llm_models.extend(model.model_dump() for model in gemini_offered)
    return ProviderListResponse(
        llm={
            "providers": llm_providers,
            "models": llm_models,
        },
        stt={
            "providers": ["deepgram"],
            "models": [model.model_dump() for model in DEEPGRAM_MODELS],
            # Selectable speech ENGINES (the Flux-vs-Nova-3 turn-taking choice).
            "engines": [engine.model_dump() for engine in STT_ENGINES],
        },
        tts={
            "providers": tts_providers,
            "models": tts_models,
        },
        # Realtime (speech-to-speech) pipeline mode — static catalog the
        # frontend renders as a selectable card. Exposed only when the OpenAI
        # key is configured, mirroring how Gemini is gated above (avoids a save
        # that 503s at call time because no key can be resolved).
        realtime=(
            {
                "available": realtime_available,
                "unavailable_reason": None if realtime_available else "OpenAI is not configured",
                "model": REALTIME_MODEL,
                "voices": [dict(v) for v in REALTIME_VOICES],
                "turn_detection": list(REALTIME_TURN_DETECTION),
                "noise_reduction": list(REALTIME_NOISE_REDUCTION),
            }
        ),
    )


@router.get("/voices")
async def list_voices(
    current_user=Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Get all available TTS voices.  Returns a JSON object with a `voices` list
    and an optional `elevenlabs_error` string when the ElevenLabs API key is
    invalid or the API returned an error.

    Voice clones live in one shared ElevenLabs account, so we filter the list
    to this tenant: shared/library voices for everyone, but a cloned voice
    only for the tenant that created it (other tenants' clones are hidden).
    """
    voices = await _get_all_tts_voices()

    try:
        voices = await filter_tenant_voices(db_client.pool, getattr(current_user, "tenant_id", None), voices)
    except VoiceEligibilityError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    el_error = get_elevenlabs_last_error() if elevenlabs_enabled() else None
    response: dict = {"voices": [v.model_dump() for v in voices]}
    if el_error:
        response["elevenlabs_error"] = el_error
    return response


@router.get("/voices/{voice_id}/sample")
async def get_voice_sample(voice_id: str, current_user=Depends(get_current_user), db_client: Client = Depends(get_db_client)):
    """
    Serve a cached ElevenLabs preview sample without generating fresh TTS.
    """
    try:
        await require_elevenlabs_voice_eligible(db_client.pool, getattr(current_user, "tenant_id", None), voice_id)
    except VoiceEligibilityError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
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
        headers={"cache-control": "private, no-store"},
    )
