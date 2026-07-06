"""Stereo-WAV recording pipeline for telephony calls.

Builds a stereo WAV (caller left channel / agent right channel) from the
media gateway's per-direction PCM buffers, resolves the canonical
``calls`` row by ``external_call_uuid``, inserts the ``recording_s3``
metadata row, and uploads to S3 if configured. Falls back to disk-only
save when the DB context is unavailable.
"""
from __future__ import annotations

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


async def _save_call_recording(voice_session, call_id: str) -> None:
    """
    Extract the recording buffer from the media gateway, convert to WAV,
    and persist to local storage + DB.

    Must be called BEFORE end_session() destroys the gateway session.

    Parameters
    ----------
    voice_session : VoiceSession
        The active voice session (still holds providers / gateway).
    call_id : str
        The PBX channel_id (key in _telephony_sessions).  This is NOT the
        same as calls.id — we must look up the internal UUID from
        calls.external_call_uuid.
    """
    from app.domain.services.recording_service import RecordingService, RecordingBuffer, mix_stereo_recording
    from app.core.container import get_container

    gateway = voice_session.media_gateway
    if not gateway:
        return

    caller_chunks = gateway.get_recording_buffer(voice_session.call_id)
    agent_chunks = getattr(gateway, "get_tts_recording_buffer", lambda _: None)(voice_session.call_id)

    if not caller_chunks and not agent_chunks:
        logger.debug(f"No recording data for {call_id[:12]}")
        return

    # Resolve sample rate from the live session so this stays correct after
    # the 8kHz -> 16kHz telephony migration. The recording buffers are written
    # at the gateway's INTERNAL rate (gateway._sample_rate), so prefer that as
    # the source of truth — it is authoritative for both cascaded and realtime.
    # Realtime forces the gateway to 8 kHz while config.gateway_sample_rate
    # stays at its (24 kHz) default; using the config value there would encode
    # the WAV at the wrong rate and play it back badly speed-shifted. Fall back
    # to config only when the gateway doesn't expose its rate.
    rec_sample_rate = 16000
    try:
        gw_rate = getattr(gateway, "_sample_rate", None)
        if gw_rate:
            rec_sample_rate = int(gw_rate)
        else:
            cfg = getattr(voice_session, "config", None)
            if cfg is not None:
                rec_sample_rate = int(
                    getattr(cfg, "gateway_sample_rate", None)
                    or getattr(cfg, "stt_sample_rate", None)
                    or 16000
                )
    except Exception:
        rec_sample_rate = 16000

    # Mix caller (left) + agent (right) into a stereo WAV
    wav_bytes = mix_stereo_recording(
        caller_chunks=caller_chunks or [],
        agent_chunks=agent_chunks or [],
        sample_rate=rec_sample_rate,
    )

    # Calculate duration from caller side (continuous timeline reference)
    caller_bytes = sum(len(c) for c in (caller_chunks or []))
    agent_bytes = sum(len(chunk) for _, chunk in (agent_chunks or []))
    bytes_per_sec = rec_sample_rate * 2  # 16-bit mono (per channel)
    duration = caller_bytes / bytes_per_sec if bytes_per_sec else 0.0

    if duration < 0.5:
        logger.debug(f"Recording too short ({duration:.1f}s) for {call_id[:12]}, skipping")
        return

    logger.info(
        f"Saving stereo recording for {call_id[:12]}: {duration:.1f}s, "
        f"caller={caller_bytes}B, agent={agent_bytes}B, wav={len(wav_bytes)}B"
    )

    # Build a RecordingBuffer to carry the pre-mixed WAV through the save pipeline
    buf = RecordingBuffer(
        call_id=call_id,
        sample_rate=rec_sample_rate,
        channels=2,        # stereo
        bit_depth=16,
    )
    buf._wav_bytes_override = wav_bytes  # pre-mixed WAV, skip re-encoding
    buf.total_bytes = len(wav_bytes)

    container = get_container()
    if not container.is_initialized:
        logger.warning("Cannot save recording: container not initialized")
        return

    db_client = container.db_client
    recording_svc = RecordingService(db_client.pool)  # pool, not Client wrapper

    # --- Resolve the internal calls.id from the PBX channel_id ----------
    # The dialer worker stores the PBX channel_id as external_call_uuid.
    internal_call_id = None
    tenant_id = "default"
    campaign_id = "unknown"

    try:
        result = (
            db_client.table("calls")
            .select("id, tenant_id, campaign_id")
            .eq("external_call_uuid", call_id)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0] if isinstance(result.data, list) else result.data
            internal_call_id = str(row.get("id"))
            tenant_id = str(row.get("tenant_id") or "default")
            campaign_id = str(row.get("campaign_id") or "unknown")
            logger.info(
                f"Resolved PBX channel {call_id[:12]} → calls.id={internal_call_id}, "
                f"tenant={tenant_id[:8]}, campaign={campaign_id[:8]}"
            )
    except Exception as lookup_err:
        logger.warning(f"Failed to look up calls record for {call_id[:12]}: {lookup_err}")

    # If we couldn't resolve the internal_call_id, this is typically a
    # dev / standalone test call that bypassed the dialer worker (which
    # is what normally inserts the calls row).  Insert a stub calls row
    # using voice_session.call_id (a real UUID) so the recording can be
    # FK-linked and shows up in the recordings UI.  Tenant + campaign
    # come from the live session config when available.
    if not internal_call_id:
        try:
            cfg = getattr(voice_session, "config", None)
            session_tenant = (
                getattr(cfg, "tenant_id", None) if cfg else None
            )
            session_campaign = (
                getattr(cfg, "campaign_id", None) if cfg else None
            )
            # Fallback 1: pull tenant_id from the live CallSession.
            if not session_tenant:
                cs = getattr(voice_session, "call_session", None)
                session_tenant = getattr(cs, "tenant_id", None) if cs else None
            # Fallback 2: look up the campaign row to get its tenant_id.
            # This is the path that handles "campaign_id was passed in URL
            # but voice_session.config didn't get tenant resolved".
            if (
                not session_tenant
                and session_campaign
                and session_campaign != "telephony"
            ):
                try:
                    async with db_client.pool.acquire() as _conn:
                        async with _conn.transaction():
                            await _conn.execute("SET LOCAL app.bypass_rls = 'true'")
                            row = await _conn.fetchrow(
                                "SELECT tenant_id FROM campaigns WHERE id = $1",
                                UUID(session_campaign),
                            )
                            if row and row["tenant_id"]:
                                session_tenant = str(row["tenant_id"])
                except Exception as _ten_lookup_exc:
                    logger.debug(
                        "campaign_tenant_lookup_failed err=%s", _ten_lookup_exc
                    )
            # Last resort: refuse to insert without a tenant — the row
            # would be invisible to the UI anyway under RLS.
            if not session_tenant:
                raise RuntimeError(
                    "no tenant_id available for stub calls row "
                    "(call has no campaign and no session tenant)"
                )
            voice_uuid = str(voice_session.call_id)
            # Best-effort phone number — falls back to channel name.
            phone_number = (
                getattr(voice_session, "_destination", None)
                or getattr(cfg, "lead_id", None)
                or call_id
            )
            async with db_client.pool.acquire() as conn:
                async with conn.transaction():
                    # Set RLS context so the INSERT is allowed under the
                    # row-level security policy on `calls`.  Without this
                    # the connection inherits whatever tenant the previous
                    # request set (often the system tenant) and the
                    # insert is rejected with InsufficientPrivilegeError.
                    if session_tenant:
                        await conn.execute(
                            f"SET LOCAL app.current_tenant_id = '{UUID(session_tenant)}'"
                        )
                    await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                    await conn.execute(
                        """
                        INSERT INTO calls (
                            id, tenant_id, campaign_id, phone_number,
                            external_call_uuid, status, created_at, updated_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                        ON CONFLICT (id) DO NOTHING
                        """,
                        UUID(voice_uuid),
                        UUID(session_tenant) if session_tenant else None,
                        UUID(session_campaign) if session_campaign and session_campaign != "telephony" else None,
                        str(phone_number)[:64],
                        call_id,
                        "completed",
                    )
            internal_call_id = voice_uuid
            tenant_id = session_tenant or "default"
            campaign_id = session_campaign or "unknown"
            logger.info(
                "stub_calls_row_inserted call=%s tenant=%s campaign=%s "
                "— enables recording UI listing for standalone test call",
                voice_uuid[:12],
                str(tenant_id)[:8],
                str(campaign_id)[:8] if campaign_id else "none",
            )
        except Exception as stub_err:
            logger.warning(
                "stub_calls_row_insert_failed call=%s err=%s — "
                "falling back to disk-only save",
                call_id[:12], stub_err,
            )
            # Disk-only fallback path (no DB row, won't show in UI but
            # WAV is recoverable from ./recordings/<voice_uuid>.wav).
            try:
                storage_path = await recording_svc._save_local(
                    call_id=str(voice_session.call_id),
                    buffer=buf,
                    tenant_id=str(tenant_id) if tenant_id and tenant_id != "default" else "unknown",
                    campaign_id=str(campaign_id) if campaign_id and campaign_id != "unknown" else "unknown",
                )
                if storage_path:
                    logger.info(f"WAV saved to disk: {storage_path}")
            except Exception as save_err:
                logger.warning(f"WAV save to disk failed: {save_err}")
            gateway.clear_recording_buffer(voice_session.call_id)
            return

    # --- Full save: file + DB record + call update ----------------------
    recording_id = await recording_svc.save_and_link(
        call_id=internal_call_id,
        buffer=buf,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
    )

    if recording_id:
        logger.info(f"Recording saved: {recording_id} for call {internal_call_id}")
    else:
        logger.warning(f"Recording save_and_link returned None for {call_id[:12]}")

    # Free memory
    gateway.clear_recording_buffer(voice_session.call_id)
