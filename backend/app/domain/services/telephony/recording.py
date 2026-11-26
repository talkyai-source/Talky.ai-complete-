"""Stereo-WAV recording pipeline for telephony calls.

Builds a stereo WAV (caller left channel / agent right channel) from the
media gateway's per-direction PCM buffers, resolves the canonical
``calls`` row by ``external_call_uuid``, inserts the ``recording_s3``
metadata row, and uploads to S3 or configured local storage. Missing tenant,
call binding or policy evidence suppresses retention.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


def _is_true_inbound_session(voice_session) -> bool:
    """Identify a carrier inbound session without trusting one optional field."""

    if bool(getattr(voice_session, "_inbound_admission", None)):
        return True
    direction = getattr(voice_session, "_call_direction", None)
    if direction is None:
        config = getattr(voice_session, "config", None)
        direction = getattr(config, "direction", None) if config is not None else None
    direction = getattr(direction, "value", direction)
    return str(direction or "").strip().lower() == "inbound"


def _discard_recording(voice_session) -> None:
    """Close retention and destroy both live buffers synchronously."""

    gateway = getattr(voice_session, "media_gateway", None)
    if gateway is None:
        return
    call_id = str(getattr(voice_session, "call_id", "") or "")
    set_gate = getattr(gateway, "set_recording_enabled", None)
    if callable(set_gate):
        set_gate(call_id, False)
    else:
        clear = getattr(gateway, "clear_recording_buffer", None)
        if callable(clear):
            clear(call_id)
    voice_session._recording_allowed = False


async def _inbound_recording_persist_allowed(voice_session, db_pool) -> Optional[bool]:
    """Live emergency switch at persistence time: True, False, or None.

    None = the switch could not be read; the caller keeps the audio safe.
    """

    if not _is_true_inbound_session(voice_session):
        return True
    if getattr(voice_session, "_recording_allowed", False) is not True:
        return False
    if db_pool is None:
        return None
    from app.domain.services.telephony.modes.caller_first import (
        _live_inbound_recording_state,
    )

    return await _live_inbound_recording_state(db_pool)


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
    (e.g. the ``"default"`` sentinel), in which case retention is refused.
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




# --- Durable spool: capture, process, retry loop ---------------------------

_PROCESSING: set = set()
_awaiting_finalizer = None

_LOOP_FIRST_DELAY_S = 5.0
_LOOP_MAX_PER_PASS = 4
_ORPHAN_AGE_S = 120.0
_AWAITING_FINALIZER_AGE_S = 120.0


def set_awaiting_finalizer(fn) -> None:
    """Register ``async fn(entry)`` that joins a transfer part; None unregisters."""
    global _awaiting_finalizer
    _awaiting_finalizer = fn


def _spool_key_for(voice_session, call_id: str) -> str:
    import re
    import uuid

    from app.domain.services.recording_spool import _KEY_RE

    session_id = str(voice_session.call_id)
    if _KEY_RE.match(session_id):
        return session_id
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", str(call_id))[:60] or "call"
    return f"{cleaned}-{uuid.uuid4().hex[:8]}"


def _build_spool_entry(
    voice_session, call_id: str, *, sample_rate: int, duration: float,
    awaiting_transfer: bool, transfer_recording_name: Optional[str],
):
    from app.domain.services.recording_policy_service import (
        _CALLEE_COUNTRY, get_callee_country, get_disclosure_state,
        notice_proof_required,
    )
    from app.domain.services.recording_spool import SpoolEntry, utc_now_iso
    from app.domain.services.telephony.modes.agent_first import _disclosure_call_ids

    cfg = getattr(voice_session, "config", None)
    tenant = _session_tenant_uuid(voice_session)
    campaign = getattr(cfg, "campaign_id", None) if cfg else None
    phone_hint = (
        getattr(voice_session, "_destination", None)
        or getattr(cfg, "lead_id", None)
        or call_id
    )
    # What the notice ledger says right now travels with the audio. A fault
    # while reading it must not cost the recording: it is then kept and marked
    # "notice not confirmed" like any call the ledger knows nothing about.
    try:
        ids = [*_disclosure_call_ids(voice_session), call_id]
        country = get_callee_country(*ids)
        country_known = country is not None or any(
            str(i) in _CALLEE_COUNTRY for i in ids if i
        )
        disclosure_state = get_disclosure_state(*ids)
    except Exception:
        country, country_known, disclosure_state = None, False, None
    inbound = _is_true_inbound_session(voice_session)
    return SpoolEntry(
        key=_spool_key_for(voice_session, call_id),
        pbx_call_id=str(call_id),
        session_call_id=str(voice_session.call_id),
        tenant_id=str(tenant) if tenant is not None else None,
        campaign_id=str(campaign) if campaign else None,
        phone_hint=str(phone_hint)[:64] if phone_hint else None,
        sample_rate=int(sample_rate),
        duration_seconds=float(duration),
        disclosure_state=disclosure_state,
        callee_country=country,
        callee_country_known=bool(country_known),
        inbound=inbound,
        strict_notice=bool(inbound and notice_proof_required()),
        awaiting="transfer_part" if awaiting_transfer else None,
        transfer_recording_name=transfer_recording_name,
        transfer_part=None,
        created_at=utc_now_iso(),
    )


async def _save_call_recording(
    voice_session,
    call_id: str,
    *,
    awaiting_transfer: bool = False,
    transfer_recording_name: Optional[str] = None,
) -> str:
    """Share the recording acquisition budget through lookup, stub and save.

    Returns a short status: ``captured``, ``recovered_checkpoint``, ``refused``
    or ``none:<reason>``.
    """
    from app.domain.services.recording_service import _with_recording_pool_budget

    try:
        return await _with_recording_pool_budget(_save_call_recording_with_budget)(
            voice_session,
            call_id,
            awaiting_transfer=awaiting_transfer,
            transfer_recording_name=transfer_recording_name,
        )
    finally:
        # The teardown's save is over (saved, retry or error): the checkpoint
        # is no longer shielded. Never raises into the teardown.
        try:
            from app.domain.services.recording_checkpoint import checkpoint_teardown_finished

            checkpoint_teardown_finished(voice_session.call_id)
        except Exception:
            pass


async def _save_call_recording_with_budget(
    voice_session,
    call_id: str,
    *,
    awaiting_transfer: bool = False,
    transfer_recording_name: Optional[str] = None,
) -> str:
    """
    Capture: mix the gateway buffers and write them to the durable spool.
    Process: ``process_spool_entry`` saves from the spool (or memory).

    Must be called BEFORE end_session() destroys the gateway session.

    ``call_id`` is the PBX channel_id (key in _telephony_sessions), NOT
    calls.id; the internal UUID is resolved from calls.external_call_uuid.
    """
    from app.domain.services.recording_service import mix_stereo_recording
    from app.domain.services.recording_spool import write_entry
    from app.core.container import get_container

    gateway = getattr(voice_session, "media_gateway", None)

    container = get_container()
    db_pool = (
        getattr(container, "db_pool", None)
        if getattr(container, "is_initialized", False)
        else None
    )
    def _checkpoint_done() -> None:
        # The live on-disk copy is no longer needed (audio is in the pending
        # folder, or deliberately not kept). Never raises into the save path.
        try:
            from app.domain.services.recording_checkpoint import checkpoint_done

            checkpoint_done(str(voice_session.call_id))
        except Exception:
            pass

    async def _not_captured(reason: str) -> str:
        # Nothing could be mixed from memory. Keep any on-disk checkpoint copy
        # (move it to the pending folder) and say so; delete it only when it
        # is safely pending or there was none.
        from app.domain.services.recording_checkpoint import (
            recover_session_checkpoint,
        )

        cp = await recover_session_checkpoint(voice_session.call_id)
        if cp == "failed":
            log = logger.error
        elif reason in ("empty", "too_short") and cp == "none":
            # ~70 ordinary calls a day end like this (unanswered legs with a
            # session and no audio): not an alarm.
            log = logger.info
        else:
            log = logger.warning
        log(
            "recording_not_captured call=%s session=%s reason=%s checkpoint=%s",
            str(call_id)[:12], str(voice_session.call_id)[:12], reason, cp,
        )
        if cp != "failed":
            _checkpoint_done()
        return "recovered_checkpoint" if cp == "recovered" else f"none:{reason}"

    if getattr(voice_session, "_recording_refused_by_policy", False) is True:
        # The account chose not to record this call: its buffers were closed at
        # the notice step. Nothing is kept, and that is a decision, not a fault.
        _checkpoint_done()
        return "refused"

    if not gateway:
        return await _not_captured("no_gateway")

    if (await _inbound_recording_persist_allowed(voice_session, db_pool)) is False:
        _discard_recording(voice_session)
        _checkpoint_done()
        logger.info(
            "Inbound recording discarded before persistence: live retention "
            "permission is absent call=%s",
            call_id[:12],
        )
        return "refused"

    caller_chunks = gateway.get_recording_buffer(voice_session.call_id)
    agent_chunks = getattr(gateway, "get_tts_recording_buffer", lambda _: None)(voice_session.call_id)

    if not caller_chunks and not agent_chunks:
        return await _not_captured(
            "gateway_session_gone"
            if caller_chunks is None else "empty"
        )

    # Snapshot the gateway's live buffers into new list objects now, in the
    # same synchronous stretch (no `await` yet) as the calls above. The mix
    # below runs in a worker thread, and `get_recording_buffer` /
    # `get_tts_recording_buffer` hand back the gateway session's *live* lists
    # (no copy is made there). After the first `await`, `end_session()` /
    # `clear_recording_buffer()` may `.clear()` those exact lists in place; a
    # thread reading them mid-clear would see a truncated buffer. `list(...)`
    # is an atomic shallow copy; the elements are immutable so the copies stay
    # stable for the rest of this call.
    caller_chunks = list(caller_chunks) if caller_chunks else []
    agent_chunks = list(agent_chunks) if agent_chunks else []

    # Sample rate: the buffers are written at the gateway's INTERNAL rate
    # (gateway._sample_rate), authoritative for cascaded and realtime (realtime
    # forces 8 kHz while config.gateway_sample_rate keeps its 24 kHz default).
    # Fall back to config only when the gateway doesn't expose its rate.
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

    # mix_stereo_recording is CPU-bound per-sample Python; offload it so the
    # event loop keeps servicing every other live call (2026-07-13).
    wav_bytes = await asyncio.to_thread(
        mix_stereo_recording,
        caller_chunks=caller_chunks,
        agent_chunks=agent_chunks,
        sample_rate=rec_sample_rate,
    )

    caller_bytes = sum(len(c) for c in (caller_chunks or []))
    agent_bytes = sum(len(chunk) for _, chunk in (agent_chunks or []))
    bytes_per_sec = rec_sample_rate * 2  # 16-bit mono (per channel)
    duration = caller_bytes / bytes_per_sec if bytes_per_sec else 0.0

    if duration < 0.5:
        return await _not_captured("too_short")

    logger.info(
        f"Saving stereo recording for {call_id[:12]}: {duration:.1f}s, "
        f"caller={caller_bytes}B, agent={agent_bytes}B, wav={len(wav_bytes)}B"
    )

    # Write to the durable spool BEFORE anything else can fail. A broken disk
    # must not lose a recording that could still be saved directly from memory.
    entry = _build_spool_entry(
        voice_session, call_id, sample_rate=rec_sample_rate, duration=duration,
        awaiting_transfer=awaiting_transfer,
        transfer_recording_name=transfer_recording_name,
    )
    spooled = False
    try:
        await asyncio.to_thread(write_entry, entry, wav_bytes)
        spooled = True
        _checkpoint_done()
    except Exception as exc:
        logger.error(
            "recording_spool_write_failed call=%s step=%s err=%s", call_id[:12], getattr(exc, "spool_step", "other"), type(exc).__name__,
        )
    if spooled:
        # The audio is on disk now; free the in-memory copy.
        gateway.clear_recording_buffer(voice_session.call_id)
        if awaiting_transfer:
            logger.info("recording_spool_written call=%s awaiting=transfer_part", call_id[:12])
            return "captured"
    elif awaiting_transfer:
        # Nothing on disk to join the transfer part onto later, and the buffers
        # are about to be cleared: save the AI part now and say what is missing.
        entry.awaiting = None
        entry.transfer_part = "missing"

    if spooled:
        outcome = await process_spool_entry(entry, wav_bytes=wav_bytes, voice_session=voice_session)
    else:
        # No durable copy exists: an unavailable "already saved" check must not
        # stop this save (the memory copy is destroyed right after).
        outcome = await process_spool_entry(
            entry, wav_bytes=wav_bytes, voice_session=voice_session,
            proceed_if_check_unavailable=True,
        )
    if not spooled and outcome != "retry":
        _checkpoint_done()
        # Free memory (today's timing when the spool write failed).
        gateway.clear_recording_buffer(voice_session.call_id)
    if outcome == "refused":
        return "refused"
    if spooled or outcome == "saved":
        return "captured"
    # Not on disk and not saved: the audio is only in memory, and the caller is
    # about to end the session. Say so; never report it as captured.
    return "none:spool_write_failed"


async def _mark_retry(entry, reason: str) -> str:
    """Count a failed attempt on the entry and keep it on disk. Never raises."""
    from app.domain.services.recording_spool import update_entry, utc_now_iso

    try:
        entry.attempts += 1
        entry.last_error = reason
        entry.last_attempt_at = utc_now_iso()
        await asyncio.to_thread(update_entry, entry)
    except Exception as exc:
        logger.error("recording_spool_write_failed call=%s op=update err=%s",
                     str(entry.pbx_call_id)[:12], type(exc).__name__)
    logger.warning(
        "recording_spool_retry call=%s reason=%s attempts=%d",
        str(entry.pbx_call_id)[:12], reason, entry.attempts,
    )
    if reason == "saved_check_unavailable" and entry.attempts >= 3:
        # Only WARNINGs until the 30-day expiry deletes the audio otherwise.
        logger.error(
            "recording_saved_check_still_unavailable call=%s attempts=%d",
            str(entry.pbx_call_id)[:12], entry.attempts,
        )
    return "retry"


async def _finish(entry, status: str) -> str:
    """Remove the pending copy after a final outcome."""
    from app.domain.services.recording_spool import remove_entry

    try:
        await asyncio.to_thread(remove_entry, entry.key)
    except Exception as exc:
        logger.error("recording_spool_write_failed call=%s op=remove err=%s",
                     str(entry.pbx_call_id)[:12], type(exc).__name__)
    return status


async def process_spool_entry(
    entry, *, wav_bytes=None, voice_session=None, proceed_if_check_unavailable: bool = False,
) -> str:
    """Save one pending recording. Returns "saved", "refused" or "retry"; never raises."""
    if entry.key in _PROCESSING:
        return "retry"
    _PROCESSING.add(entry.key)
    try:
        if proceed_if_check_unavailable:
            return await _process_locked(
                entry, wav_bytes, voice_session, proceed_if_check_unavailable=True,
            )
        return await _process_locked(entry, wav_bytes, voice_session)
    except Exception as exc:
        return await _mark_retry(entry, type(exc).__name__)
    finally:
        _PROCESSING.discard(entry.key)


async def _process_locked(
    entry, wav_bytes, voice_session, *, proceed_if_check_unavailable: bool = False,
) -> str:
    from app.domain.services import recording_service as _rs
    from app.domain.services.recording_policy_service import (
        _CALLEE_COUNTRY, get_disclosure_state,
        record_callee_country, record_disclosure_state,
    )
    from app.domain.services.recording_spool import read_wav
    from app.core.container import get_container

    pbx_id = entry.pbx_call_id

    if wav_bytes is None:
        wav_bytes = await asyncio.to_thread(read_wav, entry.key)
        if wav_bytes is None:
            logger.info("recording_spool_refused call=%s reason=%s", pbx_id[:12], "audio_missing")
            return await _finish(entry, "refused")

    container = get_container()
    if not getattr(container, "is_initialized", False):
        return await _mark_retry(entry, "container_unavailable")
    db_client = getattr(container, "db_client", None)
    pool = getattr(db_client, "pool", None)
    if pool is None:
        return await _mark_retry(entry, "container_unavailable")
    db_pool = getattr(container, "db_pool", None)

    try:
        expected_tenant = UUID(str(entry.tenant_id)) if entry.tenant_id else None
    except (ValueError, AttributeError, TypeError):
        expected_tenant = None
    if expected_tenant is None:
        # Not discarded any more: stays pending so a person can be alerted.
        return await _mark_retry(entry, "tenant_unavailable")

    # --- Resolve the internal calls.id from the PBX channel_id ----------
    internal_call_id = None
    tenant_id = "default"
    campaign_id = "unknown"
    try:
        # Object-level authz: external_call_uuid is NOT unique across tenants
        # (channel ids get reused), so the lookup is scoped to the tenant the
        # session belonged to.
        async with _rs._acquire_recording_connection(
            pool, pbx_id, str(expected_tenant),
        ) as conn:
            row = await conn.fetchrow(
                "SELECT id, tenant_id, campaign_id FROM calls "
                "WHERE external_call_uuid = $1 "
                "AND tenant_id = $2 "
                "ORDER BY created_at DESC LIMIT 1",
                pbx_id,
                expected_tenant,
            )
        if row:
            internal_call_id = str(row["id"])
            tenant_id = str(row["tenant_id"] or "default")
            campaign_id = str(row["campaign_id"] or "unknown")
            logger.info(
                f"Resolved PBX channel {pbx_id[:12]} → calls.id={internal_call_id}, "
                f"tenant={tenant_id[:8]}, campaign={campaign_id[:8]}"
            )
    except Exception as lookup_err:
        logger.warning(f"Failed to look up calls record for {pbx_id[:12]}: {lookup_err}")
        return await _mark_retry(entry, "call_lookup_failed")

    # No calls row: a dev / standalone call that bypassed the dialer worker.
    # Insert a stub row keyed on the voice-session UUID so the recording can be
    # FK-linked and listed.
    if not internal_call_id and entry.inbound:
        # An admitted incoming call always has its calls row from admission.
        # A stub here could only be a second, wrong row (and the database
        # refuses it), so keep the recording pending and retry instead.
        return await _mark_retry(entry, "inbound_call_row_missing")
    if not internal_call_id:
        try:
            session_tenant = str(expected_tenant)
            if voice_session is not None:
                cfg = getattr(voice_session, "config", None)
                configured_tenant = getattr(cfg, "tenant_id", None) if cfg else None
                if configured_tenant and UUID(str(configured_tenant)) != expected_tenant:
                    raise RuntimeError("conflicting recording tenant context")
            session_campaign = entry.campaign_id
            voice_uuid = str(entry.session_call_id)
            phone_number = entry.phone_hint or pbx_id
            from app.core.db_utils import acquire_with_tenant

            async with _rs._acquire_recording_connection(
                pool, pbx_id, session_tenant,
                acquire_context=lambda: acquire_with_tenant(pool, session_tenant),
            ) as conn:
                campaign_uuid = (
                    UUID(str(session_campaign))
                    if session_campaign and session_campaign != "telephony" else None
                )
                if campaign_uuid is not None:
                    campaign = await conn.fetchrow(
                        "SELECT id FROM campaigns WHERE id = $1 AND tenant_id = $2",
                        campaign_uuid, expected_tenant,
                    )
                    if not campaign:
                        raise RuntimeError("recording campaign ownership unavailable")
                await conn.execute(
                    """
                    INSERT INTO calls (
                        id, tenant_id, campaign_id, phone_number,
                        external_call_uuid, status, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                    ON CONFLICT (id) DO NOTHING
                    """,
                    UUID(voice_uuid),
                    expected_tenant,
                    campaign_uuid,
                    str(phone_number)[:64],
                    pbx_id,
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
        except Exception as exc:
            logger.warning(
                "stub_calls_row_insert_failed call=%s err=%s — recording kept pending",
                pbx_id[:12], type(exc).__name__,
            )
            return await _mark_retry(entry, "stub_insert_failed")

    # A call can end between cluster heartbeats. Re-read the live inbound
    # switch immediately before the first irreversible storage operation.
    if voice_session is not None:
        allowed = await _inbound_recording_persist_allowed(voice_session, db_pool)
    elif entry.inbound:
        from app.domain.services.telephony.modes.caller_first import (
            _live_inbound_recording_state,
        )

        allowed = None if db_pool is None else await _live_inbound_recording_state(db_pool)
    else:
        allowed = True
    if allowed is None:
        # Switch unreadable: keep the audio for a later attempt, never refuse.
        return await _mark_retry(entry, "inbound_switch_unreadable")
    if not allowed:
        if voice_session is not None:
            _discard_recording(voice_session)
        logger.info(
            "Inbound recording discarded at final persistence gate call=%s", pbx_id[:12],
        )
        logger.info("recording_spool_refused call=%s reason=%s", pbx_id[:12], "inbound_gate_closed")
        return await _finish(entry, "refused")

    # Put the notice ledger back after a restart, only where it is empty.
    aliases = (internal_call_id, entry.session_call_id, pbx_id)
    if get_disclosure_state(*aliases) is None and entry.disclosure_state is not None:
        record_disclosure_state(entry.disclosure_state, *aliases)
    if entry.callee_country_known and not any(str(a) in _CALLEE_COUNTRY for a in aliases if a):
        record_callee_country(entry.callee_country, *aliases)

    buf = _rs.RecordingBuffer(
        call_id=pbx_id,
        sample_rate=entry.sample_rate,
        channels=2,        # stereo
        bit_depth=16,
    )
    buf._wav_bytes_override = wav_bytes  # pre-mixed WAV, skip re-encoding
    buf.total_bytes = len(wav_bytes)

    outcome = await _rs.RecordingService(pool).save_and_link_outcome(
        call_id=internal_call_id,
        buffer=buf,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        strict_notice=entry.strict_notice,
        transfer_part=entry.transfer_part,
        # One "failed" row tells the story; a retry every few minutes must not
        # add another each time.
        mark_failure=entry.attempts == 0,
        **({"proceed_if_check_unavailable": True} if proceed_if_check_unavailable else {}),
    )
    status = getattr(outcome, "status", None)
    if status == _rs.SAVE_SAVED:
        logger.info(
            "recording_spool_saved call=%s recording=%s attempts=%d",
            pbx_id[:12], outcome.recording_id, entry.attempts,
        )
        logger.info(f"Recording saved: {outcome.recording_id} for call {internal_call_id}")
        return await _finish(entry, "saved")
    if status == _rs.SAVE_REFUSED:
        logger.info("recording_spool_refused call=%s reason=%s", pbx_id[:12], outcome.reason)
        return await _finish(entry, "refused")
    return await _mark_retry(entry, getattr(outcome, "reason", "") or "not_saved")


def _age_seconds(iso: Optional[str], now) -> Optional[float]:
    from app.domain.services.recording_spool import parse_iso

    parsed = parse_iso(iso)
    return None if parsed is None else (now - parsed).total_seconds()


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    import os

    try:
        return max(minimum, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


_TEMP_SWEEP_EVERY_S = 600.0
_TEMP_SWEEP_LAST: Optional[float] = None


def _monotonic() -> float:
    """Monotonic seconds; tests replace this to move the clock."""
    return time.monotonic()


def _set_temp_sweep_last(value: float) -> None:
    global _TEMP_SWEEP_LAST
    _TEMP_SWEEP_LAST = value


def _loop_may_run(loop_name: str) -> bool:
    from app.domain.services.recording_checkpoint import loop_may_run

    return loop_may_run(loop_name)


async def _spool_pass() -> int:
    """One retry pass; returns the number of entries processed."""
    from datetime import datetime, timezone

    from app.domain.services.recording_service import _with_recording_pool_budget
    from app.domain.services.recording_spool import (
        has_audio, list_entries, read_entry, remove_entry, update_entry,
    )

    if not _loop_may_run("recording_spool_loop"):
        return 0
    entries = await asyncio.to_thread(list_entries)
    now = datetime.now(timezone.utc)
    awaiting_max = _env_float("RECORDING_AWAITING_MAX_AGE_S", 4500.0)
    expire_after = _env_float("RECORDING_PENDING_MAX_AGE_DAYS", 30.0, minimum=1.0) * 86400.0
    processed = 0
    for entry in entries:
        if processed >= _LOOP_MAX_PER_PASS:
            break
        if entry.key in _PROCESSING:
            continue
        # The listing is a moment old. Another path may have saved and
        # removed this entry since, or changed it: read it again.
        key = entry.key
        entry = await asyncio.to_thread(read_entry, key)
        if entry is None or not await asyncio.to_thread(has_audio, key):
            continue
        age = _age_seconds(entry.created_at, now)
        if age is None:
            age = 0.0
        if age > expire_after:
            # The health check has alerted on this entry every minute since
            # its first quarter of an hour. Audio must not outlive that for
            # ever outside the stored recordings (an account that was
            # deleted, a call row that is gone): it is removed, loudly.
            logger.error(
                "recording_spool_expired call=%s age_days=%.1f attempts=%d last_error=%s",
                str(entry.pbx_call_id)[:12], age / 86400.0, entry.attempts, entry.last_error,
            )
            await asyncio.to_thread(remove_entry, key)
            continue
        if entry.awaiting:
            if _awaiting_finalizer is not None:
                if age > _AWAITING_FINALIZER_AGE_S:
                    try:
                        await _awaiting_finalizer(entry)
                    except Exception as exc:
                        # One entry must not stop the pass for the others.
                        logger.error(
                            "recording_spool_loop_error err=%s", type(exc).__name__,
                        )
                continue
            if age <= awaiting_max:
                continue
            entry.awaiting = None
            entry.transfer_part = "missing"
            await asyncio.to_thread(update_entry, entry)
        elif entry.attempts == 0:
            if age <= _ORPHAN_AGE_S:
                continue
        else:
            since = _age_seconds(entry.last_attempt_at, now)
            wait = min(60 * 2 ** (entry.attempts - 1), 900)
            if since is not None and since < wait:
                continue
        await _with_recording_pool_budget(process_spool_entry)(entry)
        processed += 1
    return processed


async def recording_spool_loop(stop_event: asyncio.Event) -> None:
    """Retry pending recordings until ``stop_event`` is set.

    An entry that cannot be saved stays for RECORDING_PENDING_MAX_AGE_DAYS
    (default 30) and is then removed with an error line.
    """
    logger.info("recording_spool_loop_started")
    interval = _env_float("RECORDING_SPOOL_RETRY_INTERVAL_S", 60.0, minimum=10.0)
    delay = _LOOP_FIRST_DELAY_S
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass
            delay = interval
            if not _loop_may_run("recording_spool_loop"):
                continue  # not the call owner right now: do nothing this round
            try:
                await _spool_pass()
            except Exception as exc:
                logger.error("recording_spool_loop_error err=%s", type(exc).__name__)
            try:
                # Transfer parts nobody could join are removed after a week
                # (the function itself runs at most once in six hours).
                from app.domain.services.telephony.transfer_recording import (
                    purge_old_transfer_parts,
                )

                await purge_old_transfer_parts()
            except Exception as exc:
                logger.error("recording_spool_loop_error err=%s", type(exc).__name__)
            try:
                from app.domain.services.recording_spool import sweep_leftovers

                await asyncio.to_thread(sweep_leftovers)  # old stray .json / .tmp files
            except Exception as exc:
                logger.error("recording_spool_loop_error err=%s", type(exc).__name__)
            try:
                # Stored-recording temp files a crash left (at most every 10 min).
                if _TEMP_SWEEP_LAST is None or _monotonic() - _TEMP_SWEEP_LAST >= _TEMP_SWEEP_EVERY_S:
                    _set_temp_sweep_last(_monotonic())
                    from app.domain.services.recording_service import sweep_stale_temp_recordings

                    await asyncio.to_thread(sweep_stale_temp_recordings)
            except Exception as exc:
                logger.error("recording_spool_loop_error err=%s", type(exc).__name__)
    finally:
        logger.info("recording_spool_loop_stopped")
