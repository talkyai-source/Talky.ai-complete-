"""Cheap, bounded authentication probes over fixed provider read endpoints.

A successful check proves access to this endpoint at this time, not credit,
generation permissions or call readiness. No response payload is persisted.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json

import httpx


PROBE_TIMEOUT_SECONDS = 5.0
_MAX_BYTES = 1024 * 1024
_ENDPOINTS = {
    "groq": ("https://api.groq.com/openai/v1/models", "Authorization", "Bearer ", "data"),
    "cerebras": ("https://api.cerebras.ai/v1/models", "Authorization", "Bearer ", "data"),
    "openai": ("https://api.openai.com/v1/models", "Authorization", "Bearer ", "data"),
    "deepseek": ("https://api.deepseek.com/models", "Authorization", "Bearer ", "data"),
    "xai": ("https://api.x.ai/v1/models", "Authorization", "Bearer ", "data"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models", "x-goog-api-key", "", "models"),
    "anthropic": ("https://api.anthropic.com/v1/models", "x-api-key", "", "data"),
    "deepgram": ("https://api.deepgram.com/v1/models", "Authorization", "Token ", "stt"),
    "cartesia": ("https://api.cartesia.ai/models", "Authorization", "Bearer ", "data"),
    # ElevenLabs models are public (optional key); require account identity.
    "elevenlabs": ("https://api.elevenlabs.io/v1/user", "xi-api-key", "", "user_id"),
    # No general STT models endpoint: read one metadata entry, never transcribe.
    "assemblyai": ("https://api.assemblyai.com/v2/transcript", "Authorization", "", "transcripts"),
}
SUPPORTED_PROVIDERS = frozenset(_ENDPOINTS)


class ProviderProbeUnavailable(RuntimeError):
    """The provider could not establish a result; existing observations remain."""


@dataclass(frozen=True)
class ProviderKeyCheck:
    status: str
    checked_at: datetime


async def probe_provider_key(provider: str, api_key: str, *, client=None) -> ProviderKeyCheck:
    if provider not in _ENDPOINTS:
        raise ValueError("Provider key validation is not supported for this provider")
    if not isinstance(api_key, str) or not 8 <= len(api_key) <= 8192 or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise ValueError("API key must contain only printable non-space characters")
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned_client:
            return await probe_provider_key(provider, api_key, client=owned_client)
    url, header, prefix, field = _ENDPOINTS[provider]
    headers = {header: prefix + api_key}
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "cartesia":
        headers["Cartesia-Version"] = "2026-08-14"
    params = {"limit": 1} if provider == "assemblyai" else None
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            async with client.stream("GET", url, headers=headers, params=params,
                                     timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as response:
                if response.status_code in (401, 403):
                    return ProviderKeyCheck("invalid", datetime.now(timezone.utc))
                if response.status_code != 200:
                    raise ProviderProbeUnavailable("provider_unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > _MAX_BYTES:
                        raise ProviderProbeUnavailable("provider_response_unavailable")
                    body.extend(chunk)
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ProviderProbeUnavailable("provider_response_unavailable")
                value = data.get(field)
                valid_shape = isinstance(value, list)
                if provider == "elevenlabs":
                    valid_shape = isinstance(value, str) and 0 < len(value) <= 256
                if not valid_shape:
                    raise ProviderProbeUnavailable("provider_response_unavailable")
                return ProviderKeyCheck("valid", datetime.now(timezone.utc))
    except ProviderProbeUnavailable:
        raise
    except Exception:
        # HTTP error text, echoed keys and response bodies never leave this seam.
        # CancelledError inherits BaseException and is deliberately not caught.
        raise ProviderProbeUnavailable("provider_unavailable") from None
