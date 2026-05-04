"""Voice catalog — predicates, lookups, and live-fetched provider lists.

Owns module-level cache state for Deepgram and Cartesia voice IDs. All
voice-availability questions ("is this a Google voice?", "what English
Aura-2 voices does the current key see?", "merged TTS voice list") are
answered by this module.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import List, Optional, Set

import aiohttp

from app.domain.models.ai_config import (
    CARTESIA_VOICES,
    DEEPGRAM_AURA2_VOICES,
    GOOGLE_CHIRP3_VOICES,
    VoiceInfo,
)
from app.infrastructure.tts.elevenlabs_catalog import (
    elevenlabs_enabled,
    get_elevenlabs_voice_by_id,
    get_elevenlabs_voices_for_current_key,
)

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


# --- predicates / sync lookups ----------------------------------------

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


# --- live fetchers (cached) -------------------------------------------

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
