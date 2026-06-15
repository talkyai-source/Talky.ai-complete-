"""Voice-cloning endpoints (ElevenLabs Instant Voice Cloning).

  POST   /ai-options/voices/clone          create a clone from an audio sample
  GET    /ai-options/voices/cloned         list this tenant's clones
  DELETE /ai-options/voices/cloned/{id}    delete a clone (frees an EL slot)

A clone lives in the shared EL account; ownership/scoping is handled by
``voice_clone_service`` + the cloned_voices table. The catalog
(``providers.list_voices``) hides other tenants' clones, so once created a
clone shows up in the user's normal voice list and is selectable per
campaign exactly like any ElevenLabs voice.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.v1.dependencies import get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.domain.services import voice_clone_service as vcs
from app.infrastructure.tts.elevenlabs_catalog import (
    elevenlabs_enabled,
    invalidate_elevenlabs_voices_cache,
)
from app.infrastructure.tts.elevenlabs_clone import (
    ElevenLabsCloneError,
    clone_voice_instant,
    delete_elevenlabs_voice,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Options"])

# Guardrails on the uploaded sample.
_MAX_SAMPLE_BYTES = 12 * 1024 * 1024  # 12 MB — plenty for a 1–2 min clip
_ALLOWED_AUDIO_PREFIXES = ("audio/",)
_ALLOWED_FALLBACK_EXTS = (".mp3", ".wav", ".m4a", ".webm", ".ogg", ".flac")


def _tenant_or_403(current_user) -> str:
    tid = getattr(current_user, "tenant_id", None)
    if not tid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context")
    return str(tid)


@router.get("/voices/cloned")
async def list_cloned_voices(
    current_user=Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """List the voice clones owned by the current tenant."""
    tenant_id = _tenant_or_403(current_user)
    items = await vcs.list_for_tenant(db_client.pool, tenant_id)
    return {"items": items, "max_per_tenant": vcs.MAX_PER_TENANT, "used": len(items)}


@router.post("/voices/clone", status_code=status.HTTP_201_CREATED)
async def clone_voice(
    name: str = Form(..., min_length=1, max_length=80),
    consent: bool = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Create an Instant Voice Clone from an uploaded/recorded audio sample.

    Requires an explicit consent attestation (ElevenLabs ToS: you must have
    the right to clone the voice). Enforces a per-tenant clone cap before
    spending an EL voice slot.
    """
    if not elevenlabs_enabled():
        raise HTTPException(status_code=503, detail="Voice cloning is not configured on this server.")
    if not consent:
        raise HTTPException(
            status_code=400,
            detail="You must confirm you have the right to clone this voice.",
        )
    tenant_id = _tenant_or_403(current_user)

    # Per-tenant cap — checked BEFORE we spend a shared EL slot.
    used = await vcs.count_for_tenant(db_client.pool, tenant_id)
    if used >= vcs.MAX_PER_TENANT:
        raise HTTPException(
            status_code=409,
            detail=f"Clone limit reached ({vcs.MAX_PER_TENANT}). Delete a voice to add another.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The audio sample is empty.")
    if len(content) > _MAX_SAMPLE_BYTES:
        raise HTTPException(status_code=400, detail="Audio sample too large (max 12 MB).")
    ctype = (file.content_type or "").lower()
    fname = (file.filename or "sample").lower()
    if not (ctype.startswith(_ALLOWED_AUDIO_PREFIXES) or fname.endswith(_ALLOWED_FALLBACK_EXTS)):
        raise HTTPException(status_code=400, detail="Please upload an audio file (mp3, wav, m4a, webm…).")

    try:
        voice_id = await clone_voice_instant(
            name=name.strip(),
            samples=[(file.filename or "sample.mp3", content, ctype or "audio/mpeg")],
        )
    except ElevenLabsCloneError as exc:
        # Surface EL's message (e.g. "voice limit reached") as a 4xx.
        raise HTTPException(status_code=502, detail=str(exc))

    row = await vcs.record_clone(
        db_client.pool,
        tenant_id=tenant_id,
        voice_id=voice_id,
        name=name.strip(),
        created_by=getattr(current_user, "email", None) or getattr(current_user, "user_id", None),
    )
    invalidate_elevenlabs_voices_cache()
    logger.info("voice_clone_created tenant=%s voice_id=%s", tenant_id, voice_id)
    return row


@router.delete("/voices/cloned/{clone_id}")
async def delete_cloned_voice(
    clone_id: str,
    current_user=Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Delete a tenant's clone: remove it from ElevenLabs (frees a slot) then
    drop the ownership row. Tenant-scoped — you can only delete your own."""
    tenant_id = _tenant_or_403(current_user)
    row = await vcs.get_owned(db_client.pool, tenant_id, clone_id)
    if not row:
        raise HTTPException(status_code=404, detail="Voice not found")

    # Best-effort EL delete (a 404 there is fine — already gone). If EL is
    # unreachable we still drop our row so the user isn't stuck.
    try:
        await delete_elevenlabs_voice(row["voice_id"])
    except ElevenLabsCloneError as exc:
        logger.warning("voice_clone delete EL failed voice_id=%s: %s", row["voice_id"], exc)

    await vcs.delete_owned(db_client.pool, tenant_id, clone_id)
    invalidate_elevenlabs_voices_cache()
    return {"deleted": True, "id": clone_id}
