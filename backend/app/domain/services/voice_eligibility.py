"""Authorize voices in the shared ElevenLabs account using existing ownership."""
from __future__ import annotations

from app.domain.services import voice_clone_service


class VoiceEligibilityError(ValueError):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


async def _ownership_ids(pool, tenant_id: str | None) -> tuple[set[str], set[str]]:
    if not tenant_id:
        raise VoiceEligibilityError("tenant_required", "A verified tenant is required for voice selection", 403)
    if pool is None:
        raise VoiceEligibilityError("ownership_unavailable", "Voice ownership is temporarily unavailable", 503)
    try:
        owned = await voice_clone_service.owned_voice_ids(pool, str(tenant_id))
        all_clones = await voice_clone_service.all_platform_voice_ids(pool)
    except Exception as exc:
        # Never substitute the shared account's unfiltered catalog on DB failure.
        raise VoiceEligibilityError("ownership_unavailable", "Voice ownership is temporarily unavailable", 503) from exc
    return owned, all_clones


async def require_elevenlabs_voice_eligible(pool, tenant_id: str | None, voice_id: str, *, voices=None) -> None:
    """Require ownership or positive public metadata for an ElevenLabs voice."""
    owned, all_clones = await _ownership_ids(pool, tenant_id)
    if voice_id in owned:
        return
    if voice_id in all_clones:
        raise VoiceEligibilityError("voice_not_available", "Voice is not available for this tenant", 403)
    if voices is None:
        from app.infrastructure.tts.elevenlabs_catalog import get_elevenlabs_voices_for_current_key
        try:
            voices = await get_elevenlabs_voices_for_current_key()
        except Exception as exc:
            raise VoiceEligibilityError("catalog_unavailable", "Voice eligibility is temporarily unavailable", 503) from exc
    if any(v.id == voice_id and v.provider == "elevenlabs" and getattr(v, "provider_is_public", False) is True for v in voices):
        return
    # Provider-created but unrecorded private clones must never become stock
    # merely because a create/delete operation left an incomplete DB record.
    raise VoiceEligibilityError("voice_not_available", "Voice is not available for this tenant", 403)


async def filter_tenant_voices(pool, tenant_id: str | None, voices: list) -> list:
    if not any(voice.provider == "elevenlabs" for voice in voices):
        return voices
    owned, all_clones = await _ownership_ids(pool, tenant_id)
    return [v for v in voices if v.provider != "elevenlabs" or v.id in owned or (
        v.id not in all_clones and getattr(v, "provider_is_public", False) is True
    )]
