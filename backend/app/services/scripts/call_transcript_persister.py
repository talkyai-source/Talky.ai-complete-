"""Bind a telephony VoiceSession to the dialer's calls row and persist the
transcript on hangup.

Why this exists
---------------
voice_orchestrator.create_voice_session() mints a fresh UUID and stores it on
`voice_session.call_id` AND `voice_session.call_session.call_id`. That UUID
is used as the key for:
  - TranscriptService's in-memory buffer
  - STT pre_connect()/stream_transcribe() connection map (deepgram_flux
    keeps `self._pre_connections[call_id] = ws`)
  - TTS connect_for_call() pools
  - Media gateway's session registry

For outbound campaign calls the *dialer worker* has already inserted the
real `calls` row with a different UUID and keyed to the PBX channel via
`external_call_uuid`. TranscriptService.flush_to_database() does
`UPDATE calls WHERE id = voice_session.call_id` which matches zero rows,
so the campaign's calls row never receives the transcript.

Naive fix (swap voice_session.call_id to the dialer's calls.id) breaks the
STT/TTS/media-gateway connection maps that were opened during ringing-phase
warmup using the original id. So instead this module uses a
non-destructive binding: the dialer's calls.id is stored on the session as
`_dialer_call_id`, and the final persist path reads the in-memory transcript
buffer (still keyed on the original id) and writes via direct SQL to the
dialer row.

Public API
----------
  bind_telephony_call(voice_session, pbx_channel_id, db_client)
      Look up calls row by external_call_uuid; stash the dialer ids onto
      voice_session as private attributes. Returns CallBinding or None.

  save_call_transcript_on_hangup(voice_session, transcript_service, db_pool)
      Final persist: reads the buffer, writes to calls + transcripts tables,
      clears the buffer. Idempotent; safe to call multiple times.

Both functions swallow their own errors (log and continue) so a transient
DB hiccup cannot tear down an otherwise-healthy telephony call.
"""
from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallBinding:
    """Result of a successful dialer-row lookup."""

    internal_call_id: str
    tenant_id: Optional[str]
    campaign_id: Optional[str]


async def _maybe_await(value):
    """Supabase-style clients sometimes return an awaitable from .execute()
    and sometimes the response directly; _save_call_recording's lookup path
    in telephony_bridge.py uses the sync form, so we support both."""
    if inspect.isawaitable(value):
        return await value
    return value


async def bind_telephony_call(
    *,
    voice_session,
    pbx_channel_id: str,
    db_client,
) -> Optional[CallBinding]:
    """Resolve `calls.id` for a PBX channel and stash it on voice_session.

    On success, sets these attributes on voice_session:
      - _dialer_call_id: str  (calls.id UUID as string)
      - _dialer_tenant_id: Optional[str]
      - _dialer_campaign_id: Optional[str]

    Does NOT modify voice_session.call_id or voice_session.call_session.call_id
    so STT/TTS/media-gateway connection maps continue to work.

    Returns:
        CallBinding if the dialer row exists.
        None if no row matches (non-campaign / test call) or lookup fails.
        Never raises.
    """
    try:
        query = (
            db_client.table("calls")
            .select("id, tenant_id, campaign_id")
            .eq("external_call_uuid", pbx_channel_id)
            .limit(1)
            .execute()
        )
        response = await _maybe_await(query)
    except Exception as exc:
        logger.warning(
            "bind_telephony_call lookup failed pbx=%s err=%s",
            pbx_channel_id[:12] if pbx_channel_id else "?",
            exc,
        )
        return None

    data = getattr(response, "data", None)
    if not data:
        logger.debug(
            "bind_telephony_call no dialer row for pbx=%s "
            "(non-campaign test call?)",
            pbx_channel_id[:12] if pbx_channel_id else "?",
        )
        return None

    row = data[0] if isinstance(data, list) else data
    internal_call_id_raw = row.get("id")
    if not internal_call_id_raw:
        return None
    internal_call_id = str(internal_call_id_raw)
    tenant_id_raw = row.get("tenant_id")
    campaign_id_raw = row.get("campaign_id")

    tenant_id = str(tenant_id_raw) if tenant_id_raw else None
    campaign_id = str(campaign_id_raw) if campaign_id_raw else None

    voice_session._dialer_call_id = internal_call_id
    voice_session._dialer_tenant_id = tenant_id
    voice_session._dialer_campaign_id = campaign_id

    logger.info(
        "bind_telephony_call voice_session=%s -> calls.id=%s pbx=%s",
        str(getattr(voice_session, "call_id", ""))[:8],
        internal_call_id[:8],
        pbx_channel_id[:12] if pbx_channel_id else "?",
    )
    return CallBinding(
        internal_call_id=internal_call_id,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
    )


async def save_call_transcript_on_hangup(
    *,
    voice_session,
    transcript_service,
    db_pool,
) -> None:
    """Final persist. Reads the buffer (keyed by voice_session.call_id) and
    writes to calls + transcripts rows keyed by voice_session._dialer_call_id.

    Order of operations:
      1. Read text/json/metrics from the in-memory buffer.
      2. If no dialer binding or empty buffer, just clear and return.
      3. UPDATE calls SET transcript/transcript_json/updated_at.
      4. INSERT into transcripts (ON CONFLICT DO NOTHING).
      5. Always clear the buffer.

    Never raises. Logs on every failure path.
    """
    session_call_id = getattr(voice_session, "call_id", None)
    if not session_call_id:
        logger.debug("save_call_transcript_on_hangup no session_call_id; skipping")
        return

    dialer_call_id = getattr(voice_session, "_dialer_call_id", None)
    tenant_id_str = getattr(voice_session, "_dialer_tenant_id", None)

    # 1. Read buffer
    try:
        turns_json = transcript_service.get_transcript_json(session_call_id) or []
        text = transcript_service.get_transcript_text(session_call_id) or ""
        metrics = transcript_service.get_metrics(session_call_id) or {}
    except Exception as exc:
        logger.warning(
            "save_call_transcript_on_hangup buffer read failed session=%s err=%s",
            session_call_id[:8], exc,
        )
        return

    # 2. Early outs
    if not turns_json:
        logger.debug(
            "save_call_transcript_on_hangup no turns in buffer for %s",
            session_call_id[:8],
        )
        _safe_clear(transcript_service, session_call_id)
        return

    if not dialer_call_id:
        logger.info(
            "save_call_transcript_on_hangup no dialer binding for %s; "
            "transcript will not be persisted (non-campaign call)",
            session_call_id[:8],
        )
        _safe_clear(transcript_service, session_call_id)
        return

    if db_pool is None:
        logger.warning("save_call_transcript_on_hangup db_pool is None; skipping DB write")
        _safe_clear(transcript_service, session_call_id)
        return

    # 3 & 4. Persist.
    try:
        dialer_uuid = UUID(dialer_call_id)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "save_call_transcript_on_hangup invalid dialer_call_id=%s err=%s",
            dialer_call_id, exc,
        )
        _safe_clear(transcript_service, session_call_id)
        return

    tenant_uuid: Optional[UUID]
    try:
        tenant_uuid = UUID(tenant_id_str) if tenant_id_str else None
    except (ValueError, TypeError):
        tenant_uuid = None

    turns_jsonb = json.dumps(turns_json)
    word_count = int(metrics.get("word_count", 0))
    turn_count = int(metrics.get("turn_count", 0))
    user_words = int(metrics.get("user_word_count", 0))
    assistant_words = int(metrics.get("assistant_word_count", 0))

    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE calls
                SET transcript = $1,
                    transcript_json = $2::jsonb,
                    updated_at = NOW()
                WHERE id = $3
                """,
                text,
                turns_jsonb,
                dialer_uuid,
            )
            await conn.execute(
                """
                INSERT INTO transcripts (
                    call_id, tenant_id, turns, full_text,
                    word_count, turn_count,
                    user_word_count, assistant_word_count
                ) VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)
                ON CONFLICT DO NOTHING
                """,
                dialer_uuid,
                tenant_uuid,
                turns_jsonb,
                text,
                word_count,
                turn_count,
                user_words,
                assistant_words,
            )
        logger.info(
            "save_call_transcript_on_hangup persisted calls.id=%s turns=%d words=%d",
            dialer_call_id[:8], turn_count, word_count,
        )
    except Exception as exc:
        logger.warning(
            "save_call_transcript_on_hangup DB write failed calls.id=%s err=%s",
            dialer_call_id[:8], exc,
        )
    finally:
        _safe_clear(transcript_service, session_call_id)


def _safe_clear(transcript_service, session_call_id: str) -> None:
    """Clear the in-memory transcript buffer, swallowing any error."""
    try:
        transcript_service.clear_buffer(session_call_id)
    except Exception:
        pass
