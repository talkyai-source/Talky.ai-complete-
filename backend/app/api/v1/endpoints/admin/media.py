"""Cross-tenant Admin APIs for call recordings and reviewer feedback notes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_platform_admin,
)
from app.api.v1.endpoints.recordings import _ranged_file_response
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.call_feedback_service import (
    CallFeedbackService,
    FeedbackAudioStorage,
    FeedbackNotFoundError,
    FeedbackStorageError,
    FeedbackTranscriptionInProgressError,
    StoredFeedbackAudio,
)
from app.domain.services.recording_service import S3Client

from ._serialization import AdminResponseModel

router = APIRouter()
logger = logging.getLogger(__name__)


class AdminRecordingItem(AdminResponseModel):
    id: str
    call_id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_id: str | None = None
    campaign_name: str | None = None
    status: str
    mime_type: str
    storage: str
    duration_seconds: int | None = None
    file_size_bytes: int | None = None
    created_at: str
    updated_at: str | None = None
    playable: bool
    direction: str = "outbound"
    caller_ani: str | None = None
    called_did: str | None = None
    legal_hold: bool = False


class AdminRecordingListResponse(BaseModel):
    items: list[AdminRecordingItem]
    page: int
    page_size: int
    total: int


class AdminFeedbackItem(AdminResponseModel):
    id: str
    call_id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_id: str | None = None
    campaign_name: str | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    created_by_email: str | None = None
    audio_mime_type: str
    audio_size_bytes: int
    duration_seconds: float | None = None
    transcript: str | None = None
    transcript_status: str
    transcript_error: str | None = None
    transcription_attempts: int
    transcript_provider: str | None = None
    created_at: str
    updated_at: str
    retryable: bool
    direction: str = "outbound"
    caller_ani: str | None = None
    called_did: str | None = None
    legal_hold: bool = False


class AdminFeedbackListResponse(BaseModel):
    items: list[AdminFeedbackItem]
    page: int
    page_size: int
    total: int


class AdminMediaDeleteRequest(BaseModel):
    """Operator justification captured before an irreversible media delete."""

    reason: str = Field(min_length=8, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 8 or not any(char.isalpha() for char in normalized):
            raise ValueError("reason must be a meaningful explanation of at least 8 characters")
        return normalized


@dataclass(frozen=True)
class _DeletionClaim:
    intent_id: UUID
    status: str
    snapshot: dict[str, Any]
    cached_response: dict[str, Any] | None = None
    origin_actor_id: str | None = None
    attempt_actor_id: str | None = None

    @property
    def resumed_by_different_actor(self) -> bool:
        return bool(
            self.origin_actor_id
            and self.attempt_actor_id
            and self.origin_actor_id != self.attempt_actor_id
        )


_ACTIVE_COMPLIANCE_HOLD_SQL = """
    EXISTS (
        SELECT 1
        FROM suspension_events se
        WHERE se.suspension_type = 'COMPLIANCE'
          AND se.is_active = TRUE
          AND se.restored_at IS NULL
          AND (se.suspended_until IS NULL OR se.suspended_until > NOW())
          AND (
              (se.target_type = 'tenant'
               AND se.target_id = {tenant_expression})
              OR (
                  se.target_type = 'partner'
                  AND EXISTS (
                      SELECT 1
                      FROM tenants held_tenant
                      WHERE held_tenant.id = {tenant_expression}
                        AND held_tenant.white_label_partner_id = se.target_id
                  )
              )
          )
    )
"""


def _date_bounds(
    from_date: date | None,
    to_date: date | None,
) -> tuple[datetime | None, datetime | None]:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="from_date must not be after to_date")
    start = (
        datetime.combine(from_date, time.min, tzinfo=timezone.utc)
        if from_date
        else None
    )
    end = (
        datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if to_date
        else None
    )
    return start, end


def _recording_item(row: Any) -> AdminRecordingItem:
    data = dict(row)
    return AdminRecordingItem(
        id=data["id"],
        call_id=data["call_id"],
        tenant_id=data["tenant_id"],
        tenant_name=data.get("tenant_name") or "Unknown",
        phone_number=data.get("phone_number") or "",
        campaign_id=data.get("campaign_id"),
        campaign_name=data.get("campaign_name"),
        status=data.get("status") or "unknown",
        mime_type=data.get("mime_type") or "audio/wav",
        storage="local" if data.get("s3_bucket") == "local" else "object",
        duration_seconds=data.get("duration_seconds"),
        file_size_bytes=data.get("file_size_bytes"),
        created_at=data["created_at"],
        updated_at=data.get("updated_at"),
        playable=data.get("status") == "uploaded",
        direction=data.get("direction") or "outbound",
        caller_ani=(
            None
            if data.get("caller_ani_private")
            else data.get("caller_ani") or data.get("phone_number")
        ),
        called_did=data.get("called_did"),
        legal_hold=bool(data.get("legal_hold")),
    )


def _feedback_item(row: Any) -> AdminFeedbackItem:
    data = dict(row)
    status = str(data.get("transcript_status") or "pending")
    return AdminFeedbackItem(
        id=data["id"],
        call_id=data["call_id"],
        tenant_id=data["tenant_id"],
        tenant_name=data.get("tenant_name") or "Unknown",
        phone_number=data.get("phone_number") or "",
        campaign_id=data.get("campaign_id"),
        campaign_name=data.get("campaign_name"),
        created_by=data.get("created_by"),
        created_by_name=data.get("created_by_name"),
        created_by_email=data.get("created_by_email"),
        audio_mime_type=data.get("audio_mime_type") or "application/octet-stream",
        audio_size_bytes=int(data.get("audio_size_bytes") or 0),
        duration_seconds=(
            float(data["duration_seconds"])
            if data.get("duration_seconds") is not None
            else None
        ),
        transcript=data.get("transcript"),
        transcript_status=status,
        transcript_error=data.get("transcript_error"),
        transcription_attempts=int(data.get("transcription_attempts") or 0),
        transcript_provider=data.get("transcript_provider"),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        retryable=status == "failed",
        direction=data.get("direction") or "outbound",
        caller_ani=(
            None
            if data.get("caller_ani_private")
            else data.get("caller_ani") or data.get("phone_number")
        ),
        called_did=data.get("called_did"),
        legal_hold=bool(data.get("legal_hold")),
    )


async def _safe_audit(audit_logger: AuditLogger, **kwargs) -> None:
    try:
        await audit_logger.log(**kwargs)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("admin media audit failed: %s", exc)


@router.get("/recordings", response_model=AdminRecordingListResponse)
async def list_admin_recordings(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=200),
    status: str | None = Query(None, max_length=32),
    direction: str | None = Query(None, pattern="^(inbound|outbound)$"),
    tenant_id: UUID | None = None,
    call_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
) -> AdminRecordingListResponse:
    """List recording metadata across tenants with operational filters."""
    conditions: list[str] = []
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        conditions.append(clause.format(index=len(params)))

    if search and search.strip():
        add(
            "(c.phone_number ILIKE ${index} OR COALESCE(c.called_did, '') ILIKE ${index} "
            "OR COALESCE(c.caller_ani, '') ILIKE ${index} OR r.call_id::text ILIKE ${index} "
            "OR COALESCE(t.business_name, '') ILIKE ${index} "
            "OR COALESCE(cp.name, '') ILIKE ${index})",
            f"%{search.strip()}%",
        )
    if status:
        add("r.status = ${index}", status)
    if direction:
        add("c.direction = ${index}", direction)
    if tenant_id:
        add("r.tenant_id = ${index}", tenant_id)
    if call_id:
        add("r.call_id = ${index}", call_id)
    start, end = _date_bounds(from_date, to_date)
    if start:
        add("r.created_at >= ${index}", start)
    if end:
        add("r.created_at < ${index}", end)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    joins = """
        FROM recordings_s3 r
        JOIN calls c ON c.id = r.call_id
        LEFT JOIN tenants t ON t.id = r.tenant_id
        LEFT JOIN campaigns cp ON cp.id = r.campaign_id
    """
    offset = (page - 1) * page_size
    async with acquire_with_tenant(db_client.pool, None) as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) {joins} {where}",
            *params,
        )
        rows = await conn.fetch(
            f"""
            SELECT r.id, r.call_id, r.tenant_id, r.campaign_id,
                   r.s3_bucket, r.status, r.mime_type, r.duration_seconds,
                   r.file_size_bytes, r.created_at, r.updated_at,
                   c.phone_number, c.direction, c.caller_ani, c.caller_ani_private,
                   c.called_did, t.business_name AS tenant_name,
                   cp.name AS campaign_name,
                   {_ACTIVE_COMPLIANCE_HOLD_SQL.format(tenant_expression='r.tenant_id')}
                       AS legal_hold
            {joins}
            {where}
            ORDER BY r.created_at DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            page_size,
            offset,
        )
    return AdminRecordingListResponse(
        items=[_recording_item(row) for row in rows],
        page=page,
        page_size=page_size,
        total=int(total or 0),
    )


_FEEDBACK_JOINS = """
    FROM call_feedback f
    JOIN calls c ON c.id = f.call_id
    LEFT JOIN tenants t ON t.id = f.tenant_id
    LEFT JOIN campaigns cp ON cp.id = c.campaign_id
    LEFT JOIN user_profiles up ON up.id = f.created_by
"""

_FEEDBACK_SELECT = f"""
    SELECT f.id, f.call_id, f.tenant_id, c.campaign_id,
           f.created_by, f.audio_mime_type, f.audio_size_bytes,
           f.duration_seconds, f.transcript, f.transcript_status,
           f.transcript_error, f.transcription_attempts,
           f.transcript_provider, f.created_at, f.updated_at,
           c.phone_number, c.direction, c.caller_ani, c.caller_ani_private,
           c.called_did, t.business_name AS tenant_name,
           cp.name AS campaign_name, up.name AS created_by_name,
           up.email AS created_by_email,
           {_ACTIVE_COMPLIANCE_HOLD_SQL.format(tenant_expression='f.tenant_id')}
               AS legal_hold
"""


@router.get("/feedback", response_model=AdminFeedbackListResponse)
async def list_admin_feedback(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=200),
    transcript_status: str | None = Query(None, max_length=16),
    direction: str | None = Query(None, pattern="^(inbound|outbound)$"),
    tenant_id: UUID | None = None,
    call_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
) -> AdminFeedbackListResponse:
    """List reviewer voice notes and transcription state across tenants."""
    conditions: list[str] = []
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        conditions.append(clause.format(index=len(params)))

    if search and search.strip():
        add(
            "(c.phone_number ILIKE ${index} OR COALESCE(c.called_did, '') ILIKE ${index} "
            "OR COALESCE(c.caller_ani, '') ILIKE ${index} OR f.call_id::text ILIKE ${index} "
            "OR COALESCE(t.business_name, '') ILIKE ${index} "
            "OR COALESCE(up.email, '') ILIKE ${index})",
            f"%{search.strip()}%",
        )
    if transcript_status:
        add("f.transcript_status = ${index}", transcript_status)
    if direction:
        add("c.direction = ${index}", direction)
    if tenant_id:
        add("f.tenant_id = ${index}", tenant_id)
    if call_id:
        add("f.call_id = ${index}", call_id)
    start, end = _date_bounds(from_date, to_date)
    if start:
        add("f.created_at >= ${index}", start)
    if end:
        add("f.created_at < ${index}", end)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * page_size
    async with acquire_with_tenant(db_client.pool, None) as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) {_FEEDBACK_JOINS} {where}",
            *params,
        )
        rows = await conn.fetch(
            f"""
            {_FEEDBACK_SELECT}
            {_FEEDBACK_JOINS}
            {where}
            ORDER BY f.created_at DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            page_size,
            offset,
        )
    return AdminFeedbackListResponse(
        items=[_feedback_item(row) for row in rows],
        page=page,
        page_size=page_size,
        total=int(total or 0),
    )


async def _recording_storage_row(db_client: Client, recording_id: UUID):
    async with acquire_with_tenant(db_client.pool, None) as conn:
        return await conn.fetchrow(
            """
            SELECT id, call_id, tenant_id, s3_bucket, s3_key, status, mime_type
            FROM recordings_s3
            WHERE id = $1
            """,
            recording_id,
        )


async def _feedback_storage_row(db_client: Client, feedback_id: UUID):
    async with acquire_with_tenant(db_client.pool, None) as conn:
        return await conn.fetchrow(
            """
            SELECT f.*, c.phone_number, c.campaign_id, c.direction,
                   c.caller_ani, c.caller_ani_private, c.called_did,
                   t.business_name AS tenant_name,
                   cp.name AS campaign_name,
                   up.name AS created_by_name,
                   up.email AS created_by_email,
                   {_ACTIVE_COMPLIANCE_HOLD_SQL.format(tenant_expression='f.tenant_id')}
                       AS legal_hold
            FROM call_feedback f
            JOIN calls c ON c.id = f.call_id
            LEFT JOIN tenants t ON t.id = f.tenant_id
            LEFT JOIN campaigns cp ON cp.id = c.campaign_id
            LEFT JOIN user_profiles up ON up.id = f.created_by
            WHERE f.id = $1
            """,
            feedback_id,
        )


def _validated_recording_path(raw_path: str) -> str:
    root = os.path.realpath(
        os.path.abspath(os.getenv("LOCAL_RECORDINGS_DIR", "./recordings"))
    )
    path = os.path.realpath(os.path.abspath(raw_path))
    try:
        inside = os.path.commonpath((root, path)) == root
    except ValueError:
        inside = False
    if not inside:
        raise HTTPException(status_code=500, detail="Recording path is outside storage root")
    return path


@router.get("/recordings/{recording_id}/audio")
async def get_admin_recording_audio(
    recording_id: UUID,
    request: Request,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Stream local audio or redirect to a short-lived object-store URL."""
    row = await _recording_storage_row(db_client, recording_id)
    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")
    if row["status"] != "uploaded":
        raise HTTPException(status_code=409, detail=f"Recording is {row['status']}")

    if row["s3_bucket"] == "local":
        path = _validated_recording_path(str(row["s3_key"]))
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Recording file is missing")
        await _safe_audit(
            audit_logger,
            event_type=AuditEvent.RECORD_EXPORTED,
            actor_id=admin_user.id,
            actor_type="user",
            tenant_id=row["tenant_id"],
            resource_type="recording",
            resource_id=recording_id,
            action="recording_audio_opened",
            description="Platform admin opened a call recording",
            metadata={"call_id": str(row["call_id"]), "storage": "local"},
        )
        return _ranged_file_response(
            path,
            request,
            media_type=str(row["mime_type"] or "audio/wav"),
            filename=f"call-{row['call_id']}.wav",
        )

    s3 = S3Client()
    if not s3.is_available():
        raise HTTPException(status_code=503, detail="Recording object storage is unavailable")
    try:
        url = await asyncio.to_thread(
            s3.presigned_url,
            str(row["s3_key"]),
            None,
            str(row["s3_bucket"]),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not open recording") from exc
    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.RECORD_EXPORTED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=row["tenant_id"],
        resource_type="recording",
        resource_id=recording_id,
        action="recording_audio_opened",
        description="Platform admin opened a call recording",
        metadata={"call_id": str(row["call_id"]), "storage": "object"},
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/feedback/{feedback_id}/audio")
async def get_admin_feedback_audio(
    feedback_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Serve a reviewer note without requiring a tenant context."""
    row = await _feedback_storage_row(db_client, feedback_id)
    if not row:
        raise HTTPException(status_code=404, detail="Feedback note not found")
    storage = FeedbackAudioStorage()
    try:
        url = storage.presigned_url(row)
        if url:
            await _safe_audit(
                audit_logger,
                event_type=AuditEvent.RECORD_EXPORTED,
                actor_id=admin_user.id,
                actor_type="user",
                tenant_id=row["tenant_id"],
                resource_type="call_feedback",
                resource_id=feedback_id,
                action="feedback_audio_opened",
                description="Platform admin opened reviewer feedback audio",
                metadata={"call_id": str(row["call_id"]), "storage": "object"},
            )
            return RedirectResponse(url, status_code=302)
        data = await storage.read(row)
    except FeedbackStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.RECORD_EXPORTED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=row["tenant_id"],
        resource_type="call_feedback",
        resource_id=feedback_id,
        action="feedback_audio_opened",
        description="Platform admin opened reviewer feedback audio",
        metadata={"call_id": str(row["call_id"]), "storage": "local"},
    )
    return Response(
        content=data,
        media_type=str(row["audio_mime_type"]),
        headers={
            "Content-Length": str(len(data)),
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": "inline",
        },
    )


@router.post("/feedback/{feedback_id}/transcription/retry", response_model=AdminFeedbackItem)
async def retry_admin_feedback_transcription(
    feedback_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> AdminFeedbackItem:
    row = await _feedback_storage_row(db_client, feedback_id)
    if not row:
        raise HTTPException(status_code=404, detail="Feedback note not found")

    service = CallFeedbackService(db_client.pool)
    try:
        await service.retry_transcription(
            tenant_id=str(row["tenant_id"]),
            call_id=str(row["call_id"]),
        )
    except FeedbackTranscriptionInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FeedbackNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FeedbackStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    refreshed = await _feedback_storage_row(db_client, feedback_id)
    if not refreshed:
        raise HTTPException(status_code=404, detail="Feedback note not found")
    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.CONFIG_CHANGED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=row["tenant_id"],
        resource_type="call_feedback",
        resource_id=feedback_id,
        action="feedback_transcription_retried",
        description="Platform admin retried feedback transcription",
        metadata={"call_id": str(row["call_id"])},
    )
    return _feedback_item(refreshed)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        with suppress(json.JSONDecodeError):
            decoded = json.loads(value)
            if isinstance(decoded, dict):
                return decoded
    return {}


async def _has_active_compliance_hold(conn: Any, tenant_id: Any) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT " + _ACTIVE_COMPLIANCE_HOLD_SQL.format(tenant_expression="$1"),
            tenant_id,
        )
    )


def _claim_from_row(row: Any, attempt_actor_id: UUID | None = None) -> _DeletionClaim:
    data = dict(row)
    response = _json_object(data.get("response_body")) or None
    origin_actor = data.get("actor_id")
    return _DeletionClaim(
        intent_id=UUID(str(data["id"])),
        status=str(data["status"]),
        snapshot=_json_object(data.get("resource_snapshot")),
        cached_response=response,
        origin_actor_id=str(origin_actor) if origin_actor else None,
        attempt_actor_id=str(attempt_actor_id) if attempt_actor_id else None,
    )


def _validate_bound_delete_request(
    row: Any,
    *,
    resource_type: str,
    resource_id: UUID,
    reason: str,
    expected_tenant_id: UUID | None,
    reason_field: str,
) -> None:
    if (
        str(row["resource_type"]) != resource_type
        or UUID(str(row["resource_id"])) != resource_id
        or str(row[reason_field]) != reason
        or (
            expected_tenant_id is not None
            and UUID(str(row["tenant_id"])) != expected_tenant_id
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different delete request",
        )


async def _bind_media_deletion_request_key(
    conn: Any,
    *,
    intent_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
    reason: str,
) -> None:
    """Append one immutable actor/key → intent binding, or verify its replay."""

    binding = await conn.fetchrow(
        """
        INSERT INTO admin_media_deletion_request_keys (
            intent_id, actor_id, idempotency_key, request_reason
        ) VALUES ($1, $2, $3, $4)
        ON CONFLICT (actor_id, idempotency_key) DO NOTHING
        RETURNING intent_id, request_reason
        """,
        intent_id,
        actor_id,
        idempotency_key,
        reason,
    )
    if binding is None:
        binding = await conn.fetchrow(
            """
            SELECT intent_id, request_reason
            FROM admin_media_deletion_request_keys
            WHERE actor_id = $1 AND idempotency_key = $2
            """,
            actor_id,
            idempotency_key,
        )
    if (
        binding is None
        or UUID(str(binding["intent_id"])) != intent_id
        or str(binding["request_reason"]) != reason
    ):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different delete request",
        )


async def _resume_media_deletion_intent(
    conn: Any,
    row: Any,
    actor_id: UUID,
) -> _DeletionClaim:
    if str(row["status"]) == "completed":
        return _claim_from_row(row, actor_id)
    if await _has_active_compliance_hold(conn, row["tenant_id"]):
        raise HTTPException(
            status_code=423,
            detail="Media deletion is blocked by an active compliance/legal hold",
        )
    resumed_status = (
        "intent_committed" if str(row["status"]) == "failed" else str(row["status"])
    )
    resumed = await conn.fetchrow(
        """
        UPDATE admin_media_deletion_intents
           SET status = $2,
               attempt_count = attempt_count + 1,
               attempt_actor_ids = array_append(attempt_actor_ids, $3),
               last_error = NULL,
               updated_at = NOW()
         WHERE id = $1
        RETURNING *
        """,
        row["id"],
        resumed_status,
        actor_id,
    )
    return _claim_from_row(resumed, actor_id)


async def _claim_media_deletion(
    db_client: Client,
    *,
    resource_type: str,
    resource_id: UUID,
    actor_id: str,
    idempotency_key: str,
    reason: str,
    expected_tenant_id: UUID | None = None,
) -> _DeletionClaim | None:
    """Commit the durable deletion intent before callers may touch storage."""

    actor_uuid = UUID(str(actor_id))
    key = idempotency_key.strip()
    if len(key) < 8 or len(key) > 255:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be 8-255 characters")

    async with acquire_with_tenant(db_client.pool, None) as conn:
        key_locked = await conn.fetchval(
            """
            SELECT pg_try_advisory_xact_lock(
                hashtextextended(
                    'talky:media-delete-key:' || $1::text || ':' || $2::text,
                    0
                )
            )
            """,
            actor_uuid,
            key,
        )
        if not key_locked:
            raise HTTPException(
                status_code=409,
                detail="Another request is using this Idempotency-Key; retry shortly",
            )
        bound_request = await conn.fetchrow(
            """
            SELECT i.*, k.request_reason AS bound_request_reason
            FROM admin_media_deletion_request_keys k
            JOIN admin_media_deletion_intents i ON i.id = k.intent_id
            WHERE k.actor_id = $1 AND k.idempotency_key = $2
            FOR UPDATE OF i
            """,
            actor_uuid,
            key,
        )
        if bound_request:
            _validate_bound_delete_request(
                bound_request,
                resource_type=resource_type,
                resource_id=resource_id,
                reason=reason,
                expected_tenant_id=expected_tenant_id,
                reason_field="bound_request_reason",
            )
            return await _resume_media_deletion_intent(
                conn,
                bound_request,
                actor_uuid,
            )

        # Rolling-deploy fallback: a pre-0027 replica can create the origin
        # intent but cannot create its alias row. Lazily bind that known-safe
        # actor/key pair before continuing.
        existing = await conn.fetchrow(
            """
            SELECT *
            FROM admin_media_deletion_intents
            WHERE actor_id = $1 AND idempotency_key = $2
            FOR UPDATE
            """,
            actor_uuid,
            key,
        )
        if existing:
            _validate_bound_delete_request(
                existing,
                resource_type=resource_type,
                resource_id=resource_id,
                reason=reason,
                expected_tenant_id=expected_tenant_id,
                reason_field="reason",
            )
            await _bind_media_deletion_request_key(
                conn,
                intent_id=UUID(str(existing["id"])),
                actor_id=actor_uuid,
                idempotency_key=key,
                reason=reason,
            )
            return await _resume_media_deletion_intent(conn, existing, actor_uuid)

        if resource_type == "recording":
            if expected_tenant_id is None:
                resource = await conn.fetchrow(
                    """
                SELECT id, call_id, tenant_id, s3_bucket, s3_key, status, mime_type
                FROM recordings_s3
                WHERE id = $1
                FOR UPDATE
                    """,
                    resource_id,
                )
            else:
                resource = await conn.fetchrow(
                    """
                SELECT id, call_id, tenant_id, s3_bucket, s3_key, status, mime_type
                FROM recordings_s3
                WHERE id = $1 AND tenant_id = $2
                FOR UPDATE
                    """,
                    resource_id,
                    expected_tenant_id,
                )
        else:
            if expected_tenant_id is None:
                resource = await conn.fetchrow(
                    """
                SELECT id, call_id, tenant_id, audio_storage_provider,
                       audio_bucket, audio_key, audio_mime_type
                FROM call_feedback
                WHERE id = $1
                FOR UPDATE
                    """,
                    resource_id,
                )
            else:
                resource = await conn.fetchrow(
                    """
                SELECT id, call_id, tenant_id, audio_storage_provider,
                       audio_bucket, audio_key, audio_mime_type
                FROM call_feedback
                WHERE id = $1 AND tenant_id = $2
                FOR UPDATE
                    """,
                    resource_id,
                    expected_tenant_id,
                )

        # A concurrent request can finish while this transaction waits for the
        # resource lock.  The durable intent remains queryable even after the
        # media row has gone, so a retry still receives its completed response.
        if expected_tenant_id is None:
            resource_intent = await conn.fetchrow(
                """
            SELECT *
            FROM admin_media_deletion_intents
            WHERE resource_type = $1 AND resource_id = $2
            FOR UPDATE
                """,
                resource_type,
                resource_id,
            )
        else:
            resource_intent = await conn.fetchrow(
                """
            SELECT *
            FROM admin_media_deletion_intents
            WHERE resource_type = $1 AND resource_id = $2 AND tenant_id = $3
            FOR UPDATE
                """,
                resource_type,
                resource_id,
                expected_tenant_id,
            )
        if resource_intent:
            await _bind_media_deletion_request_key(
                conn,
                intent_id=UUID(str(resource_intent["id"])),
                actor_id=actor_uuid,
                idempotency_key=key,
                reason=reason,
            )
            return await _resume_media_deletion_intent(
                conn,
                resource_intent,
                actor_uuid,
            )

        if not resource:
            return None
        if await _has_active_compliance_hold(conn, resource["tenant_id"]):
            raise HTTPException(
                status_code=423,
                detail="Media deletion is blocked by an active compliance/legal hold",
            )

        snapshot = dict(resource)
        intent = await conn.fetchrow(
            """
            INSERT INTO admin_media_deletion_intents (
                actor_id, tenant_id, call_id, resource_type, resource_id,
                idempotency_key, reason, resource_snapshot, attempt_actor_ids
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, ARRAY[$1]::uuid[])
            RETURNING *
            """,
            actor_uuid,
            resource["tenant_id"],
            resource["call_id"],
            resource_type,
            resource_id,
            key,
            reason,
            snapshot,
        )
        await _bind_media_deletion_request_key(
            conn,
            intent_id=UUID(str(intent["id"])),
            actor_id=actor_uuid,
            idempotency_key=key,
            reason=reason,
        )
        return _claim_from_row(intent, actor_uuid)


async def _mark_deletion_stage(
    db_client: Client,
    intent_id: UUID,
    status: str,
    *,
    error: str | None = None,
) -> None:
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await _mark_deletion_stage_on_connection(
            conn,
            intent_id,
            status,
            error=error,
        )


async def _mark_deletion_stage_on_connection(
    conn: Any,
    intent_id: UUID,
    status: str,
    *,
    error: str | None = None,
) -> None:
    result = await conn.execute(
        """
            UPDATE admin_media_deletion_intents
               SET status = $2,
                   last_error = $3,
                   object_deleted_at = CASE
                       WHEN $2 = 'object_deleted' THEN COALESCE(object_deleted_at, NOW())
                       ELSE object_deleted_at
                   END,
                   updated_at = NOW()
             WHERE id = $1 AND status <> 'completed'
        """,
        intent_id,
        status,
        error[:1000] if error else None,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=409, detail="Deletion intent is no longer active")


async def _ensure_deletion_not_held(db_client: Client, claim: _DeletionClaim) -> None:
    """Re-read the hold immediately before the irreversible storage call."""

    tenant_id = UUID(str(claim.snapshot["tenant_id"]))
    async with acquire_with_tenant(db_client.pool, None) as conn:
        if await _has_active_compliance_hold(conn, tenant_id):
            raise HTTPException(
                status_code=423,
                detail="Media deletion is blocked by an active compliance/legal hold",
            )


@asynccontextmanager
async def _serialized_media_deletion(db_client: Client, claim: _DeletionClaim):
    """Serialize hold creation with the irreversible storage operation.

    Alembic revision 0025 makes every tenant/partner COMPLIANCE-hold write take
    the same tenant advisory transaction lock. Keeping this transaction open
    across the storage call means either the hold commits first and blocks the
    delete, or this deletion commits its object-deleted stage first. There is
    no check-then-delete gap in which protected media can disappear.
    """

    tenant_id = UUID(str(claim.snapshot["tenant_id"]))
    async with acquire_with_tenant(db_client.pool, None) as conn:
        # Never wait on the advisory lock while occupying a pooled database
        # connection. A tenant can retry the same durable intent; allowing a
        # burst of losing requests to queue here would let one slow object-
        # store operation exhaust the pool for every tenant.
        locked = await conn.fetchval(
            """
            SELECT pg_try_advisory_xact_lock(
                hashtextextended('talky:media-hold:' || $1::text, 0)
            )
            """,
            tenant_id,
        )
        if not locked:
            raise HTTPException(
                status_code=409,
                detail="Another media deletion is already in progress; retry this idempotent request",
            )
        if await _has_active_compliance_hold(conn, tenant_id):
            raise HTTPException(
                status_code=423,
                detail="Media deletion is blocked by an active compliance/legal hold",
            )

        snapshot = claim.snapshot
        if snapshot.get("s3_key") is not None:
            resource_type = "recording"
            bucket = str(snapshot.get("s3_bucket") or "")
            key = str(snapshot["s3_key"])
        elif snapshot.get("audio_key") is not None:
            resource_type = "call_feedback"
            bucket = str(snapshot.get("audio_bucket") or "")
            key = str(snapshot["audio_key"])
        else:
            resource_type = ""
            bucket = ""
            key = ""

        resource_id = snapshot.get("id")
        if resource_type and bucket and key and resource_id:
            object_locked = await conn.fetchval(
                """
                SELECT pg_try_advisory_xact_lock(
                    hashtextextended(
                        'talky:media-object:' || $1::text || ':' || $2::text,
                        0
                    )
                )
                """,
                bucket,
                key,
            )
            if not object_locked:
                raise HTTPException(
                    status_code=409,
                    detail="Another deletion of this media object is already in progress; retry this idempotent request",
                )

            # Per-table unique constraints prevent ordinary duplicate writers,
            # while this cross-table check also protects legacy/manual imports.
            # Keep it under the object advisory lock through physical deletion.
            shared_reference = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM recordings_s3
                    WHERE s3_bucket = $1 AND s3_key = $2
                      AND ($3::text <> 'recording' OR id <> $4::uuid)
                    UNION ALL
                    SELECT 1
                    FROM call_feedback
                    WHERE audio_bucket = $1 AND audio_key = $2
                      AND ($3::text <> 'call_feedback' OR id <> $4::uuid)
                )
                """,
                bucket,
                key,
                resource_type,
                UUID(str(resource_id)),
            )
            if shared_reference:
                raise HTTPException(
                    status_code=409,
                    detail="Media deletion is blocked because another record references the same object",
                )
        yield conn


async def _execute_serialized_media_deletion(
    db_client: Client,
    claim: _DeletionClaim,
    delete_operation: Callable[[], Awaitable[None]],
) -> Exception | None:
    """Finish erasure and its durable stage before propagating cancellation.

    ``asyncio.to_thread`` cannot stop its worker when the HTTP request task is
    cancelled. Run the complete lock/delete/stage/commit sequence in an owned,
    shielded task and keep awaiting it after cancellation. This guarantees the
    advisory lock is not released while a background thread can still erase
    bytes. The caller sees cancellation only after the intent says either
    ``object_deleted`` or ``failed`` durably.
    """

    async def finish_under_lock() -> Exception | None:
        storage_error: Exception | None = None
        async with _serialized_media_deletion(db_client, claim) as deletion_conn:
            try:
                await delete_operation()
            except Exception as exc:
                storage_error = exc
                await _mark_deletion_stage_on_connection(
                    deletion_conn,
                    claim.intent_id,
                    "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                await _mark_deletion_stage_on_connection(
                    deletion_conn,
                    claim.intent_id,
                    "object_deleted",
                )
        return storage_error

    task = asyncio.create_task(finish_under_lock())
    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True

    task_error: BaseException | None = None
    result: Exception | None = None
    try:
        result = task.result()
    except BaseException as exc:  # re-raised after cancellation bookkeeping
        task_error = exc

    if cancellation_requested:
        if task_error is not None:
            logger.error(
                "media_deletion_finalize_failed_after_request_cancel intent=%s error=%s",
                claim.intent_id,
                type(task_error).__name__,
                exc_info=task_error,
            )
        raise asyncio.CancelledError
    if task_error is not None:
        raise task_error
    return result


async def _complete_recording_deletion(
    db_client: Client,
    *,
    claim: _DeletionClaim,
    recording_id: UUID,
    response_body: dict[str, Any],
    expected_tenant_id: UUID | None = None,
) -> None:
    call_id = UUID(str(claim.snapshot["call_id"]))
    async with acquire_with_tenant(db_client.pool, None) as conn:
        if expected_tenant_id is None:
            await conn.execute("DELETE FROM recordings_s3 WHERE id = $1", recording_id)
        else:
            await conn.execute(
                "DELETE FROM recordings_s3 WHERE id = $1 AND tenant_id = $2",
                recording_id,
                expected_tenant_id,
            )
        replacement = await conn.fetchval(
            """
            SELECT id FROM recordings_s3
            WHERE call_id = $1
              AND ($2::uuid IS NULL OR tenant_id = $2)
              AND status = 'uploaded'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            call_id,
            expected_tenant_id,
        )
        if expected_tenant_id is None:
            await conn.execute(
                """
            UPDATE calls
               SET recording_url = $2, updated_at = NOW()
             WHERE id = $1
                """,
                call_id,
                f"/api/v1/recordings/{replacement}/stream" if replacement else None,
            )
        else:
            await conn.execute(
                """
            UPDATE calls
               SET recording_url = $3, updated_at = NOW()
             WHERE id = $1 AND tenant_id = $2
                """,
                call_id,
                expected_tenant_id,
                f"/api/v1/recordings/{replacement}/stream" if replacement else None,
            )
        await conn.execute(
            """
            UPDATE admin_media_deletion_intents
               SET status = 'completed', response_body = $2::jsonb,
                   completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
             WHERE id = $1
            """,
            claim.intent_id,
            response_body,
        )


async def _complete_feedback_deletion(
    db_client: Client,
    *,
    claim: _DeletionClaim,
    feedback_id: UUID,
    response_body: dict[str, Any],
) -> None:
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await conn.execute("DELETE FROM call_feedback WHERE id = $1", feedback_id)
        await conn.execute(
            """
            UPDATE admin_media_deletion_intents
               SET status = 'completed', response_body = $2::jsonb,
                   completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
             WHERE id = $1
            """,
            claim.intent_id,
            response_body,
        )


@router.delete("/recordings/{recording_id}")
async def delete_admin_recording(
    recording_id: UUID,
    payload: AdminMediaDeleteRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Permanently remove recording bytes and metadata after a durable intent."""
    claim = await _claim_media_deletion(
        db_client,
        resource_type="recording",
        resource_id=recording_id,
        actor_id=admin_user.id,
        idempotency_key=idempotency_key,
        reason=payload.reason,
    )
    if not claim:
        return {"detail": "Recording already deleted"}
    if claim.status == "completed":
        return claim.cached_response or {
            "detail": "Recording permanently deleted",
            "deletion_intent_id": str(claim.intent_id),
        }

    row = claim.snapshot
    if claim.status != "object_deleted":
        async def erase_recording_object() -> None:
            if not row.get("s3_key"):
                return
            if row.get("s3_bucket") == "local":
                path = _validated_recording_path(str(row["s3_key"]))
                with suppress(FileNotFoundError):
                    await asyncio.to_thread(os.remove, path)
                return
            s3 = S3Client()
            if not s3.is_available():
                raise RuntimeError("recording object storage is unavailable")
            await asyncio.to_thread(
                s3.delete_permanently,
                str(row["s3_key"]),
                str(row["s3_bucket"]),
            )

        storage_error = await _execute_serialized_media_deletion(
            db_client,
            claim,
            erase_recording_object,
        )
        if storage_error is not None:
            raise HTTPException(
                status_code=503,
                detail="Could not delete recording audio; the durable deletion intent and metadata were preserved",
            ) from storage_error
    response_body = {
        "detail": "Recording permanently deleted",
        "deletion_intent_id": str(claim.intent_id),
    }
    await _complete_recording_deletion(
        db_client,
        claim=claim,
        recording_id=recording_id,
        response_body=response_body,
    )
    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.CONFIG_CHANGED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=row["tenant_id"],
        resource_type="recording",
        resource_id=recording_id,
        action="recording_deleted_by_admin",
        description="Platform admin permanently deleted a call recording",
        metadata={
            "call_id": str(row["call_id"]),
            "deletion_intent_id": str(claim.intent_id),
            "reason": payload.reason,
            "origin_actor_id": claim.origin_actor_id,
            "resumed_by_different_actor": claim.resumed_by_different_actor,
        },
    )
    return response_body


@router.delete("/feedback/{feedback_id}")
async def delete_admin_feedback(
    feedback_id: UUID,
    payload: AdminMediaDeleteRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Permanently remove feedback audio/transcript after a durable intent."""
    claim = await _claim_media_deletion(
        db_client,
        resource_type="call_feedback",
        resource_id=feedback_id,
        actor_id=admin_user.id,
        idempotency_key=idempotency_key,
        reason=payload.reason,
    )
    if not claim:
        return {"detail": "Feedback note already deleted"}
    if claim.status == "completed":
        return claim.cached_response or {
            "detail": "Feedback note permanently deleted",
            "deletion_intent_id": str(claim.intent_id),
        }

    row = claim.snapshot
    if claim.status != "object_deleted":
        storage = FeedbackAudioStorage()
        stored = StoredFeedbackAudio(
            provider=str(row["audio_storage_provider"]),
            bucket=str(row["audio_bucket"]),
            key=str(row["audio_key"]),
        )
        async def erase_feedback_object() -> None:
            await storage.delete(stored)

        storage_error = await _execute_serialized_media_deletion(
            db_client,
            claim,
            erase_feedback_object,
        )
        if storage_error is not None:
            raise HTTPException(
                status_code=503,
                detail="Could not delete feedback audio; the durable deletion intent and metadata were preserved",
            ) from storage_error

    response_body = {
        "detail": "Feedback note permanently deleted",
        "deletion_intent_id": str(claim.intent_id),
    }
    await _complete_feedback_deletion(
        db_client,
        claim=claim,
        feedback_id=feedback_id,
        response_body=response_body,
    )
    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.CONFIG_CHANGED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=row["tenant_id"],
        resource_type="call_feedback",
        resource_id=feedback_id,
        action="feedback_deleted_by_admin",
        description="Platform admin permanently deleted a feedback note",
        metadata={
            "call_id": str(row["call_id"]),
            "deletion_intent_id": str(claim.intent_id),
            "reason": payload.reason,
            "origin_actor_id": claim.origin_actor_id,
            "resumed_by_different_actor": claim.resumed_by_different_actor,
        },
    )
    return response_body
