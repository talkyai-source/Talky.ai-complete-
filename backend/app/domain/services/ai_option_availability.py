"""Which AI options can work on this server (D5-1).

One place answers "does this provider have what it needs here?" for the
AI-Options catalogue and for the save route. The reason shown to a customer
is one fixed sentence: it never names a setting, a key or a provider's text.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

UNAVAILABLE_REASON = "This option is not switched on for this account yet."

# Providers whose key is read from the platform environment (the same
# resolve_sync_env_only the catalogue already used for DeepSeek). OpenAI and
# AssemblyAI keep their tenant-aware resolution in the catalogue endpoint.
_ENV_KEYED = ("groq", "cerebras", "gemini", "deepseek", "deepgram", "cartesia")
# Providers that also accept a comma-separated pool (groq.py, deepgram.py and
# cartesia.py read <NAME>_API_KEYS with parse_keys_csv). DeepSeek's pool is
# already part of resolve_sync_env_only.
_POOL_VARS = {"groq": "GROQ_API_KEYS", "deepgram": "DEEPGRAM_API_KEYS", "cartesia": "CARTESIA_API_KEYS"}
_GOOGLE_TTS_SETTINGS = ("GOOGLE_TTS_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS")


def google_tts_available() -> bool:
    return any(os.getenv(name, "").strip() for name in _GOOGLE_TTS_SETTINGS)


def pool_available(provider: str) -> bool:
    """True when the provider's key pool variable holds at least one key."""
    from app.infrastructure.providers.key_pool import parse_keys_csv

    name = _POOL_VARS.get(provider)
    return bool(name and parse_keys_csv(os.getenv(name)))


def provider_key_available(provider: str) -> bool:
    """Singular platform key resolves, or the provider's pool holds a key."""
    from app.domain.services.credential_resolver import resolve_sync_env_only

    return bool(resolve_sync_env_only(provider)) or pool_available(provider)


def platform_availability() -> dict[str, bool]:
    """Availability of every provider that is keyed from the platform environment.

    Each provider is resolved once per call (the DeepSeek key pool rotates on
    every read).
    """
    available = {name: provider_key_available(name) for name in _ENV_KEYED}
    available["google"] = google_tts_available()
    return available


def option_status(available: bool) -> dict[str, Any]:
    return {"available": bool(available), "unavailable_reason": None if available else UNAVAILABLE_REASON}


def with_status(item: Mapping[str, Any], available: Optional[Mapping[str, bool]], default: bool = True) -> dict[str, Any]:
    """Copy ``item`` (a model/voice dict) and add availability from its provider."""
    provider = str(item.get("provider") or "")
    ok = bool(available.get(provider, default)) if available is not None else default
    return {**item, **option_status(ok)}


def unavailable_detail(cause: str) -> dict[str, str]:
    """HTTP detail for a refused unavailable option; the real cause is logged only."""
    logger.warning("AI option refused, not available on this server: %s", cause)
    return {"code": "ai_option_unavailable", "message": UNAVAILABLE_REASON}
