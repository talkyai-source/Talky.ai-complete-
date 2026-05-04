"""Read-only catalog endpoints — provider list, voice list, voice sample.

Endpoints:
  GET /providers                          - LLM/STT/TTS providers + models
  GET /voices                             - merged TTS voices across providers
  GET /voices/{voice_id}/sample           - cached ElevenLabs preview MP3
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.domain.models.ai_config import (
    CARTESIA_MODELS,
    DEEPGRAM_MODELS,
    DEEPGRAM_TTS_MODELS,
    ELEVENLABS_TTS_MODELS,
    GEMINI_MODELS,
    GOOGLE_TTS_MODELS,
    GROQ_MODELS,
    ProviderListResponse,
)
from app.infrastructure.tts.elevenlabs_catalog import (
    elevenlabs_enabled,
    ensure_elevenlabs_preview_cached,
    get_elevenlabs_last_error,
    get_elevenlabs_tts_models_for_current_key,
)

from ._catalog import _find_elevenlabs_voice, _get_all_tts_voices

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Options"])


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
