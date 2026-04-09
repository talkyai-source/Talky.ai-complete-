"""
ElevenLabs catalog helpers.

This module centralizes live model/voice discovery and preview sample caching
so the AI Options endpoints can stay focused on request/response handling.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

from app.domain.models.ai_config import ELEVENLABS_TTS_MODELS, ModelInfo, VoiceInfo

logger = logging.getLogger(__name__)

_ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io"
_ELEVENLABS_MODELS_URL = f"{_ELEVENLABS_API_BASE_URL}/v1/models"
_ELEVENLABS_VOICES_URL = f"{_ELEVENLABS_API_BASE_URL}/v2/voices"
_ELEVENLABS_VOICE_URL_TEMPLATE = f"{_ELEVENLABS_API_BASE_URL}/v1/voices/{{voice_id}}"
_ELEVENLABS_TIMEOUT = aiohttp.ClientTimeout(total=15)
_ELEVENLABS_CACHE_TTL_SECONDS = 300.0
_ELEVENLABS_VOICE_PAGE_SIZE = 100
_ELEVENLABS_PREVIEW_CACHE_DIR = Path("/tmp/talky-elevenlabs-preview-cache")

_elevenlabs_models_cache: Optional[list[ModelInfo]] = None
_elevenlabs_models_cache_expires_at: float = 0.0
_elevenlabs_models_cache_lock = asyncio.Lock()

_elevenlabs_voices_cache: Optional[list[VoiceInfo]] = None
_elevenlabs_voices_cache_expires_at: float = 0.0
_elevenlabs_voices_cache_lock = asyncio.Lock()

_elevenlabs_preview_url_cache: dict[str, str] = {}
_elevenlabs_preview_download_locks: dict[str, asyncio.Lock] = {}


def elevenlabs_api_key() -> Optional[str]:
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    return api_key or None


def elevenlabs_enabled() -> bool:
    return bool(elevenlabs_api_key())


def elevenlabs_preview_proxy_url(voice_id: str) -> str:
    return f"/api/v1/ai-options/voices/{voice_id}/sample"


def _elevenlabs_headers() -> dict[str, str]:
    api_key = elevenlabs_api_key()
    if not api_key:
        raise RuntimeError("ElevenLabs API key not configured")
    return {"xi-api-key": api_key}


def _safe_voice_filename(voice_id: str, suffix: str = ".mp3") -> str:
    sanitized = "".join(ch for ch in voice_id if ch.isalnum() or ch in {"-", "_"})
    return f"{sanitized or 'voice'}{suffix}"


def _voice_accent_color(gender: Optional[str]) -> str:
    normalized = (gender or "").strip().lower()
    if normalized == "female":
        return "#db2777"
    if normalized == "male":
        return "#2563eb"
    return "#64748b"


def _normalize_language_name(language: Optional[str], locale: Optional[str]) -> str:
    if language:
        return language
    if locale:
        return locale
    return "Unknown"


def _normalize_gender(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    normalized = raw_value.strip().lower()
    if normalized in {"female", "male"}:
        return normalized
    return raw_value


def _pick_preview_url(payload: dict[str, Any]) -> Optional[str]:
    top_level = payload.get("preview_url")
    if isinstance(top_level, str) and top_level.strip():
        return top_level

    verified_languages = payload.get("verified_languages")
    if isinstance(verified_languages, list):
        for item in verified_languages:
            if not isinstance(item, dict):
                continue
            candidate = item.get("preview_url")
            if isinstance(candidate, str) and candidate.strip():
                return candidate

    samples = payload.get("samples")
    if isinstance(samples, list):
        for item in samples:
            if not isinstance(item, dict):
                continue
            for key in ("preview_url", "audio_file_url", "audioFileUrl", "download_url"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate

    return None


def _normalize_elevenlabs_voice(payload: dict[str, Any]) -> Optional[VoiceInfo]:
    voice_id = payload.get("voice_id") or payload.get("voiceId") or payload.get("id")
    if not isinstance(voice_id, str) or not voice_id.strip():
        return None

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        name = voice_id

    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    verified_languages = payload.get("verified_languages")
    verified_language = verified_languages[0] if isinstance(verified_languages, list) and verified_languages else {}
    if not isinstance(verified_language, dict):
        verified_language = {}

    description = payload.get("description") or labels.get("description")
    if not isinstance(description, str) or not description.strip():
        category = payload.get("category")
        if isinstance(category, str) and category.strip():
            description = f"ElevenLabs {category} voice."
        else:
            description = "ElevenLabs voice."

    gender = _normalize_gender(labels.get("gender"))
    accent = labels.get("accent") or verified_language.get("accent")
    accent_value = accent if isinstance(accent, str) and accent.strip() else "Global"

    language_name = _normalize_language_name(
        verified_language.get("language") if isinstance(verified_language.get("language"), str) else None,
        verified_language.get("locale") if isinstance(verified_language.get("locale"), str) else None,
    )

    tags: list[str] = ["elevenlabs"]
    for candidate in (
        payload.get("category"),
        labels.get("use_case"),
        labels.get("age"),
        labels.get("accent"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            tags.append(candidate.strip().lower().replace(" ", "-"))

    preview_url = _pick_preview_url(payload)
    if preview_url:
        _elevenlabs_preview_url_cache[voice_id] = preview_url

    return VoiceInfo(
        id=voice_id,
        name=name,
        language=language_name,
        description=description,
        gender=gender,
        accent=accent_value,
        accent_color=_voice_accent_color(gender),
        preview_text="Hello, I am your AI voice assistant. How can I help you today?",
        provider="elevenlabs",
        tags=sorted(set(tags)),
        preview_url=elevenlabs_preview_proxy_url(voice_id) if preview_url else None,
    )


async def _fetch_json(url: str, *, params: Optional[dict[str, Any]] = None) -> Any:
    async with aiohttp.ClientSession(timeout=_ELEVENLABS_TIMEOUT) as session:
        async with session.get(url, headers=_elevenlabs_headers(), params=params) as response:
            if response.status == 401:
                raise RuntimeError("ElevenLabs API returned 401 Unauthorized")
            if response.status != 200:
                message = await response.text()
                raise RuntimeError(
                    f"ElevenLabs request failed with status {response.status}: {message[:240]}"
                )
            return await response.json()


async def get_elevenlabs_tts_models_for_current_key() -> list[ModelInfo]:
    global _elevenlabs_models_cache, _elevenlabs_models_cache_expires_at
    now = time.time()
    if _elevenlabs_models_cache is not None and now < _elevenlabs_models_cache_expires_at:
        return list(_elevenlabs_models_cache)

    async with _elevenlabs_models_cache_lock:
        now = time.time()
        if _elevenlabs_models_cache is not None and now < _elevenlabs_models_cache_expires_at:
            return list(_elevenlabs_models_cache)

        if not elevenlabs_enabled():
            _elevenlabs_models_cache = list(ELEVENLABS_TTS_MODELS)
            _elevenlabs_models_cache_expires_at = now + _ELEVENLABS_CACHE_TTL_SECONDS
            return list(_elevenlabs_models_cache)

        try:
            payload = await _fetch_json(_ELEVENLABS_MODELS_URL)
            models: list[ModelInfo] = []
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    if item.get("can_do_text_to_speech") is False:
                        continue
                    model_id = item.get("model_id")
                    name = item.get("name")
                    if not isinstance(model_id, str) or not model_id.strip():
                        continue
                    if not isinstance(name, str) or not name.strip():
                        name = model_id
                    description = item.get("description")
                    if not isinstance(description, str) or not description.strip():
                        description = "ElevenLabs text-to-speech model."
                    models.append(
                        ModelInfo(
                            id=model_id,
                            name=name,
                            description=description,
                            context_window=item.get("maximum_text_length_per_request"),
                            provider="elevenlabs",
                        )
                    )
            if models:
                _elevenlabs_models_cache = models
            else:
                _elevenlabs_models_cache = list(ELEVENLABS_TTS_MODELS)
        except Exception as exc:
            logger.warning("ElevenLabs models lookup failed: %s", exc)
            _elevenlabs_models_cache = list(ELEVENLABS_TTS_MODELS)

        _elevenlabs_models_cache_expires_at = time.time() + _ELEVENLABS_CACHE_TTL_SECONDS
        return list(_elevenlabs_models_cache)


async def get_elevenlabs_voices_for_current_key() -> list[VoiceInfo]:
    global _elevenlabs_voices_cache, _elevenlabs_voices_cache_expires_at
    now = time.time()
    if _elevenlabs_voices_cache is not None and now < _elevenlabs_voices_cache_expires_at:
        return list(_elevenlabs_voices_cache)

    async with _elevenlabs_voices_cache_lock:
        now = time.time()
        if _elevenlabs_voices_cache is not None and now < _elevenlabs_voices_cache_expires_at:
            return list(_elevenlabs_voices_cache)

        if not elevenlabs_enabled():
            _elevenlabs_voices_cache = []
            _elevenlabs_voices_cache_expires_at = now + _ELEVENLABS_CACHE_TTL_SECONDS
            return []

        try:
            voices: list[VoiceInfo] = []
            next_page_token: Optional[str] = None

            while True:
                params: dict[str, Any] = {"page_size": _ELEVENLABS_VOICE_PAGE_SIZE}
                if next_page_token:
                    params["next_page_token"] = next_page_token
                payload = await _fetch_json(_ELEVENLABS_VOICES_URL, params=params)
                items = payload.get("voices") if isinstance(payload, dict) else payload
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        normalized = _normalize_elevenlabs_voice(item)
                        if normalized is not None:
                            voices.append(normalized)

                if not isinstance(payload, dict):
                    break
                next_page_token = payload.get("next_page_token") or payload.get("nextPageToken")
                has_more = payload.get("has_more")
                if not next_page_token or has_more is False:
                    break

            deduped = {voice.id: voice for voice in voices}
            _elevenlabs_voices_cache = list(deduped.values())
        except Exception as exc:
            logger.warning("ElevenLabs voices lookup failed: %s", exc)
            _elevenlabs_voices_cache = []

        _elevenlabs_voices_cache_expires_at = time.time() + _ELEVENLABS_CACHE_TTL_SECONDS
        return list(_elevenlabs_voices_cache)


async def get_elevenlabs_voice_by_id(voice_id: str) -> Optional[VoiceInfo]:
    for voice in await get_elevenlabs_voices_for_current_key():
        if voice.id == voice_id:
            return voice

    if not elevenlabs_enabled():
        return None

    try:
        payload = await _fetch_json(_ELEVENLABS_VOICE_URL_TEMPLATE.format(voice_id=voice_id))
        if isinstance(payload, dict):
            normalized = _normalize_elevenlabs_voice(payload)
            if normalized is not None:
                return normalized
    except Exception as exc:
        logger.warning("ElevenLabs voice lookup failed for %s: %s", voice_id, exc)
    return None


async def ensure_elevenlabs_preview_cached(voice_id: str) -> Optional[Path]:
    if not elevenlabs_enabled():
        return None

    preview_url = _elevenlabs_preview_url_cache.get(voice_id)
    if not preview_url:
        voice = await get_elevenlabs_voice_by_id(voice_id)
        if voice is None:
            return None
        preview_url = _elevenlabs_preview_url_cache.get(voice_id)
        if not preview_url:
            return None

    _ELEVENLABS_PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target_path = _ELEVENLABS_PREVIEW_CACHE_DIR / _safe_voice_filename(voice_id)
    if target_path.exists() and target_path.stat().st_size > 0:
        return target_path

    lock = _elevenlabs_preview_download_locks.setdefault(voice_id, asyncio.Lock())
    async with lock:
        if target_path.exists() and target_path.stat().st_size > 0:
            return target_path

        async with aiohttp.ClientSession(timeout=_ELEVENLABS_TIMEOUT) as session:
            async with session.get(preview_url) as response:
                if response.status != 200:
                    logger.warning(
                        "ElevenLabs preview download failed for %s with status %s",
                        voice_id,
                        response.status,
                    )
                    return None
                content = await response.read()

        if not content:
            return None

        temp_path = target_path.with_suffix(".tmp")
        temp_path.write_bytes(content)
        temp_path.replace(target_path)
        return target_path
