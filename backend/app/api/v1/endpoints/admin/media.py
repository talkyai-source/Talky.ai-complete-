"""Cross-tenant Admin APIs for call recordings and reviewer feedback notes."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

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


class AdminFeedbackListResponse(BaseModel):
    items: list[AdminFeedbackItem]
    page: int
    page_size: int
    total: int


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
            "(c.phone_number ILIKE ${index} OR r.call_id::text ILIKE ${index} "
            "OR COALESCE(t.business_name, '') ILIKE ${index} "
            "OR COALESCE(cp.name, '') ILIKE ${index})",
            f"%{search.strip()}%",
        )
    if status:
        add("r.status = ${index}", status)
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
                   c.phone_number, t.business_name AS tenant_name,
                   cp.name AS campaign_name
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

_FEEDBACK_SELECT = """
    SELECT f.id, f.call_id, f.tenant_id, c.campaign_id,
           f.created_by, f.audio_mime_type, f.audio_size_bytes,
           f.duration_seconds, f.transcript, f.transcript_status,
           f.transcript_error, f.transcription_attempts,
           f.transcript_provider, f.created_at, f.updated_at,
           c.phone_number, t.business_name AS tenant_name,
           cp.name AS campaign_name, up.name AS created_by_name,
           up.email AS created_by_email
"""


@router.get("/feedback", response_model=AdminFeedbackListResponse)
async def list_admin_feedback(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=200),
    transcript_status: str | None = Query(None, max_length=16),
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
            "(c.phone_number ILIKE ${index} OR f.call_id::text ILIKE ${index} "
            "OR COALESCE(t.business_name, '') ILIKE ${index} "
            "OR COALESCE(up.email, '') ILIKE ${index})",
            f"%{search.strip()}%",
        )
    if transcript_status:
        add("f.transcript_status = ${index}", transcript_status)
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
            SELECT f.*, c.phone_number, c.campaign_id,
                   t.business_name AS tenant_name,
                   cp.name AS campaign_name,
                   up.name AS created_by_name,
                   up.email AS created_by_email
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
    root = os.path.abspath(os.getenv("LOCAL_RECORDINGS_DIR", "./recordings"))
    path = os.path.abspath(raw_path)
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
        url = await asyncio.to_thread(s3.presigned_url, str(row["s3_key"]))
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


@router.delete("/recordings/{recording_id}")
async def delete_admin_recording(
    recording_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Permanently remove recording bytes and metadata (audited)."""
    row = await _recording_storage_row(db_client, recording_id)
    if not row:
        return {"detail": "Recording already deleted"}

    if row["status"] == "uploaded" and row["s3_key"]:
        if row["s3_bucket"] == "local":
            path = _validated_recording_path(str(row["s3_key"]))
            try:
                with suppress(FileNotFoundError):
                    await asyncio.to_thread(os.remove, path)
            except OSError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Could not delete local recording; metadata was preserved",
                ) from exc
        else:
            s3 = S3Client()
            if not s3.is_available():
                raise HTTPException(
                    status_code=503,
                    detail="Recording object storage is unavailable; nothing was deleted",
                )
            try:
                await asyncio.to_thread(s3.delete, str(row["s3_key"]))
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Could not delete recording object; metadata was preserved",
                ) from exc

    async with acquire_with_tenant(db_client.pool, None) as conn:
        await conn.execute("DELETE FROM recordings_s3 WHERE id = $1", recording_id)
        replacement = await conn.fetchval(
            """
            SELECT id FROM recordings_s3
            WHERE call_id = $1 AND status = 'uploaded'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            row["call_id"],
        )
        await conn.execute(
            """
            UPDATE calls
               SET recording_url = $2, updated_at = NOW()
             WHERE id = $1
            """,
            row["call_id"],
            f"/api/v1/recordings/{replacement}/stream" if replacement else None,
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
        metadata={"call_id": str(row["call_id"])},
    )
    return {"detail": "Recording permanently deleted"}


@router.delete("/feedback/{feedback_id}")
async def delete_admin_feedback(
    feedback_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Permanently remove a feedback note, its transcript, and its audio."""
    row = await _feedback_storage_row(db_client, feedback_id)
    if not row:
        return {"detail": "Feedback note already deleted"}

    storage = FeedbackAudioStorage()
    stored = StoredFeedbackAudio(
        provider=str(row["audio_storage_provider"]),
        bucket=str(row["audio_bucket"]),
        key=str(row["audio_key"]),
    )
    try:
        await storage.delete(stored)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not delete feedback audio; metadata was preserved",
        ) from exc

    async with acquire_with_tenant(db_client.pool, None) as conn:
        await conn.execute("DELETE FROM call_feedback WHERE id = $1", feedback_id)

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
        metadata={"call_id": str(row["call_id"])},
    )
    return {"detail": "Feedback note permanently deleted"}
