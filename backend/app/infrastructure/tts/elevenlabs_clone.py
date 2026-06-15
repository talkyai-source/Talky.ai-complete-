"""ElevenLabs Instant Voice Cloning client.

Thin wrapper over the two EL endpoints the voice-clone feature needs:

  * ``POST /v1/voices/add``      — create a clone from one or more audio
    samples, returns a ``voice_id`` usable anywhere a normal EL voice is.
  * ``DELETE /v1/voices/{id}``   — remove a clone (frees an account slot).

Cloned voices live in the single shared EL account (one ELEVENLABS_API_KEY)
— ownership/tenant-scoping is handled above this layer by
``voice_clone_service`` + the ``cloned_voices`` table. This module is pure
EL I/O and stays unaware of tenants.

Errors are surfaced as ``ElevenLabsCloneError`` with the EL message so the
endpoint can return something actionable (e.g. "voice limit reached").
"""
from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from app.infrastructure.tts.elevenlabs_catalog import (
    _ELEVENLABS_API_BASE_URL,
    elevenlabs_api_key,
)

logger = logging.getLogger(__name__)

_ADD_VOICE_URL = f"{_ELEVENLABS_API_BASE_URL}/v1/voices/add"
_VOICE_URL = f"{_ELEVENLABS_API_BASE_URL}/v1/voices/{{voice_id}}"
# Cloning uploads audio + trains, so allow longer than the catalog's 15s.
_CLONE_TIMEOUT = aiohttp.ClientTimeout(total=120)


class ElevenLabsCloneError(RuntimeError):
    """Raised when EL rejects a clone/delete. ``message`` is user-safe-ish."""

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _require_key() -> str:
    key = elevenlabs_api_key()
    if not key:
        raise ElevenLabsCloneError("ElevenLabs is not configured on this server.")
    return key


async def clone_voice_instant(
    *,
    name: str,
    samples: list[tuple[str, bytes, str]],
    description: Optional[str] = None,
) -> str:
    """Create an Instant Voice Clone. Returns the new EL ``voice_id``.

    ``samples`` is a list of ``(filename, content_bytes, content_type)``.
    EL accepts one or more; a single ~1-minute clean sample is enough.
    """
    if not samples:
        raise ElevenLabsCloneError("At least one audio sample is required.")
    key = _require_key()

    form = aiohttp.FormData()
    form.add_field("name", name)
    if description:
        form.add_field("description", description)
    for filename, content, content_type in samples:
        form.add_field(
            "files", content, filename=filename, content_type=content_type or "audio/mpeg",
        )

    try:
        async with aiohttp.ClientSession(timeout=_CLONE_TIMEOUT) as session:
            async with session.post(
                _ADD_VOICE_URL, headers={"xi-api-key": key}, data=form,
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise ElevenLabsCloneError(
                        _extract_error(body) or f"ElevenLabs returned {resp.status}.",
                        status=resp.status,
                    )
                voice_id = (body or {}).get("voice_id") or (body or {}).get("voiceId")
                if not voice_id:
                    raise ElevenLabsCloneError("ElevenLabs did not return a voice id.")
                logger.info("elevenlabs_clone_created voice_id=%s name=%s", voice_id, name)
                return str(voice_id)
    except ElevenLabsCloneError:
        raise
    except aiohttp.ClientError as exc:
        raise ElevenLabsCloneError(f"Could not reach ElevenLabs: {exc}") from exc


async def delete_elevenlabs_voice(voice_id: str) -> None:
    """Delete a clone from the EL account. Idempotent: a 404 is treated as
    already-gone (we still want to drop our DB row)."""
    key = _require_key()
    try:
        async with aiohttp.ClientSession(timeout=_CLONE_TIMEOUT) as session:
            async with session.delete(
                _VOICE_URL.format(voice_id=voice_id), headers={"xi-api-key": key},
            ) as resp:
                if resp.status == 404:
                    logger.info("elevenlabs_delete_voice already gone voice_id=%s", voice_id)
                    return
                if resp.status >= 400:
                    body = await resp.json(content_type=None)
                    raise ElevenLabsCloneError(
                        _extract_error(body) or f"ElevenLabs returned {resp.status}.",
                        status=resp.status,
                    )
                logger.info("elevenlabs_delete_voice ok voice_id=%s", voice_id)
    except ElevenLabsCloneError:
        raise
    except aiohttp.ClientError as exc:
        raise ElevenLabsCloneError(f"Could not reach ElevenLabs: {exc}") from exc


def _extract_error(body) -> Optional[str]:
    """Pull a human-readable message out of an EL error envelope."""
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, dict):
        msg = detail.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    msg = body.get("message")
    return msg.strip() if isinstance(msg, str) and msg.strip() else None
