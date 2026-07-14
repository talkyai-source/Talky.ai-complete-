"""Stereo-WAV recording pipeline for telephony calls.

Builds a stereo WAV (caller left channel / agent right channel) from the
media gateway's per-direction PCM buffers, resolves the canonical
``calls`` row by ``external_call_uuid``, inserts the ``recording_s3``
metadata row, and uploads to S3 if configured. Falls back to disk-only
save when the DB context is unavailable.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


def _session_tenant_uuid(voice_session) -> UUID | None:
    """Best-effort authoritative tenant id for a live call, as a ``UUID``.

    Used to tenant-scope the ``external_call_uuid`` -> ``calls.id`` resolution
    so a recording can only ever bind to a call owned by the tenant this
    session was serving. Priority mirrors the rest of the teardown path:

    1. ``voice_session._dialer_tenant_id`` — stamped at answer-time binding
       (``call_transcript_persister.bind_telephony_call``); the canonical
       tenant for a dialer/outbound call.
    2. ``voice_session.config.tenant_id`` — the session config, when resolved.
    3. ``voice_session.call_session.tenant_id`` — the live CallSession.

    Returns ``None`` when no tenant is known or the value isn't a valid UUID
    (e.g. the ``"default"`` sentinel), in which case the caller leaves the
    lookup unscoped rather than risk breaking a legitimate resolution.
    """
    candidates = [getattr(voice_session, "_dialer_tenant_id", None)]
    cfg = getattr(voice_session, "config", None)
    candidates.append(getattr(cfg, "tenant_id", None) if cfg else None)
    cs = getattr(voice_session, "call_session", None)
    candidates.append(getattr(cs, "tenant_id", None) if cs else None)
    for value in candidates:
        if not value:
            continue
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            continue
    return None


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

    # Snapshot the gateway's live buffers into new list objects now, in the
    # same synchronous stretch (no `await` yet) as the calls above. The mix
    # below runs in a worker thread further down, and `get_recording_buffer`
    # / `get_tts_recording_buffer` hand back the gateway session's *live*
    # lists (see telephony_media_gateway.py — no copy is made there). Once
    # we `await` to offload the mix, the event loop is free to run
    # `end_session()` / `clear_recording_buffer()` for this same call
    # concurrently, which `.clear()`s those exact list objects in place —
    # a thread reading them mid-clear would see a truncated/empty buffer or
    # (worst case) race a `.clear()` mutation while iterating. `list(...)`
    # takes a shallow copy in one atomic, non-yielding step; the bytes/tuple
    # elements inside are immutable, so the copies are fully independent and
    # stable for the rest of this call's lifetime — nothing else ever holds
    # a reference to them.
    caller_chunks = list(caller_chunks) if caller_chunks else []
    agent_chunks = list(agent_chunks) if agent_chunks else []

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

    # Mix caller (left) + agent (right) into a stereo WAV.
    #
    # ROOT CAUSE FIX (2026-07-13): mix_stereo_recording does a per-sample
    # Python loop (backend/app/domain/services/recording_service.py, the
    # `for i in range(total_samples): ...` interleave loop) over the WHOLE
    # call's audio. For a multi-minute call at 16kHz stereo that's several
    # million iterations of pure-Python bytearray slicing — tens to
    # hundreds of ms of the single asyncio event loop being 100% CPU-bound,
    # during which EVERY other in-flight call's audio pump, STT/TTS
    # websocket reads, and silence timers starve. Offload it to a worker
    # thread via asyncio.to_thread so the loop is free to keep servicing
    # every other live call while this one call's recording is mixed.
    # `caller_chunks`/`agent_chunks` were already snapshotted into
    # independent list copies above, so the thread never touches state that
    # concurrent teardown (`clear_recording_buffer`, `end_session`) can
    # mutate. A raise here propagates out of `_save_call_recording()` to
    # the caller's `try/except` in `lifecycle._on_call_ended`, which logs
    # it and continues teardown (end_session still runs — see the call
    # site) — identical failure isolation to the previous synchronous call.
    wav_bytes = await asyncio.to_thread(
        mix_stereo_recording,
        caller_chunks=caller_chunks,
        agent_chunks=agent_chunks,
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

    # 2026-07-08: this lookup runs once per call at recording persist (hot
    # path), so it was moved off the blocking postgres_adapter
    # (`db_client.table()` — blocks the event loop on the shared thread pool
    # AND opens an unpooled asyncpg connection per call) onto the pooled
    # async `get_db()` connection. `get_db()` reads the same tenant-isolation
    # contextvars the adapter did, so RLS/tenant behaviour is unchanged.
    try:
        from app.core.db import get_db

        # Object-level authz (2026-07-13): external_call_uuid (the PBX channel
        # id) is NOT unique in `calls` — channel ids get reused across calls,
        # and over time the table accumulates multiple rows sharing one value
        # across DIFFERENT tenants. A bare `LIMIT 1` with no tenant predicate
        # and no ordering could therefore resolve THIS recording onto another
        # tenant's historical call row and persist the audio under the wrong
        # tenant. Scope the lookup to the tenant this live session actually
        # belongs to (authoritative source: `_dialer_tenant_id`, stamped at
        # answer-time binding; falls back to the session config / call-session
        # tenant). When none is known — rare dev/standalone calls that never
        # bound to a dialer row — the predicate is permissive (unchanged
        # behaviour, and there is no session tenant to cross). ORDER BY
        # created_at DESC deterministically picks the freshest matching row.
        expected_tenant = _session_tenant_uuid(voice_session)
        async with get_db() as conn:
            row = await conn.fetchrow(
                "SELECT id, tenant_id, campaign_id FROM calls "
                "WHERE external_call_uuid = $1 "
                "AND ($2::uuid IS NULL OR tenant_id = $2) "
                "ORDER BY created_at DESC LIMIT 1",
                call_id,
                expected_tenant,
            )
        if row:
            internal_call_id = str(row["id"])
            tenant_id = str(row["tenant_id"] or "default")
            campaign_id = str(row["campaign_id"] or "unknown")
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
