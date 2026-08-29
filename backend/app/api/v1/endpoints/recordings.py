"""Tenant-scoped call-recording access and permanent deletion controls."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any, List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
    get_db_client,
    get_db_pool,
)
from app.core.db_utils import acquire_with_tenant
from app.core.security.rbac import (
    ROLE_DEFAULT_PERMISSIONS,
    Permission,
    _warn_unseeded_fallback_once,
    check_permission,
    get_effective_permissions,
    normalize_role,
    rbac_data_is_seeded,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.recording_service import S3Client

router = APIRouter(prefix="/recordings", tags=["recordings"])
logger = logging.getLogger(__name__)


class RecordingListItem(BaseModel):
    id: str
    call_id: str
    phone_number: Optional[str] = None
    created_at: str
    duration_seconds: Optional[int] = None
    file_size_bytes: Optional[int] = None
    status: str = "uploaded"
    legal_hold: bool = False


class RecordingListResponse(BaseModel):
    items: List[RecordingListItem]
    page: int
    page_size: int
    total: int


class RecordingUrlResponse(BaseModel):
    url: str
    expires_in: int
    recording_id: str
    mime_type: str
    retention_days_remaining: Optional[int] = None


class RecordingDeleteRequest(BaseModel):
    """A durable justification for irreversible tenant media deletion."""

    reason: str = Field(min_length=8, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 8 or not any(char.isalpha() for char in normalized):
            raise ValueError("reason must be a meaningful explanation of at least 8 characters")
        return normalized


def _tenant_id(user: CurrentUser) -> str:
    if not user.tenant_id:
        raise HTTPException(status_code=403, detail={"code": "tenant_context_required"})
    return str(user.tenant_id)


def _require_recording_permission(permission: Permission):
    """Resolve live DB grants and fail closed on authorization outages."""

    async def dependency(
        user: CurrentUser = Depends(get_current_user),
        db_pool: asyncpg.Pool = Depends(get_db_pool),
    ) -> CurrentUser:
        tenant_id = _tenant_id(user)
        try:
            permissions = await get_effective_permissions(db_pool, user.id, tenant_id)
            # An empty set is ambiguous: it means either "this user has been
            # denied everything" or "this deployment has no RBAC data at all"
            # (prod: role_permissions and tenant_users are both empty, so every
            # tenant user would lose recording playback and DSAR erasure). Only
            # the second is a reason to fall back, and the fallback is that
            # role's defaults — never wider than a seeded deployment grants.
            # A non-empty set proves the deployment is seeded, so the probe
            # never runs on the healthy path.
            if not permissions and not await rbac_data_is_seeded(db_pool):
                permissions = ROLE_DEFAULT_PERMISSIONS.get(
                    normalize_role(user.role), set()
                )
                _warn_unseeded_fallback_once()
        except Exception as exc:  # noqa: BLE001 - authorization must fail closed
            logger.error(
                "recording_permission_lookup_failed tenant=%s user=%s err_type=%s",
                tenant_id,
                user.id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authorization_unavailable"},
            ) from exc

        user.set_permissions({item.value for item in permissions})
        if not check_permission(permissions, permission):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "required": permission.value},
            )
        return user

    return dependency


async def _safe_audit(audit_logger: AuditLogger, **kwargs: Any) -> None:
    try:
        await audit_logger.log(**kwargs)
    except Exception as exc:  # pragma: no cover - audit delivery is best effort
        logger.warning("tenant recording audit failed: %s", type(exc).__name__)


@router.get("/", response_model=RecordingListResponse)
async def list_recordings(
    call_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(_require_recording_permission(Permission.RECORDINGS_READ)),
    db_client: Client = Depends(get_db_client),
):
    """List recordings for the current tenant, optionally filtered by call."""
    tenant_id = _tenant_id(current_user)
    offset = (page - 1) * page_size

    # Only list playable recordings — 'failed'/'deleted'/'uploading' rows would
    # render a row + play button that 404s on click.
    conditions = ["r.tenant_id = $1", "r.status = 'uploaded'"]
    params: list = [tenant_id]
    idx = 2

    if call_id:
        conditions.append(f"r.call_id = ${idx}")
        params.append(call_id)
        idx += 1

    where = " AND ".join(conditions)

    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT r.id, r.call_id, r.created_at,
                   r.duration_seconds, r.file_size_bytes, r.status,
                   c.phone_number,
                   EXISTS (
                       SELECT 1
                       FROM suspension_events se
                       WHERE se.suspension_type = 'COMPLIANCE'
                         AND se.is_active = TRUE
                         AND se.restored_at IS NULL
                         AND (se.suspended_until IS NULL OR se.suspended_until > NOW())
                         AND (
                             (se.target_type = 'tenant' AND se.target_id = r.tenant_id)
                             OR (
                                 se.target_type = 'partner'
                                 AND EXISTS (
                                     SELECT 1 FROM tenants held_tenant
                                     WHERE held_tenant.id = r.tenant_id
                                       AND held_tenant.white_label_partner_id = se.target_id
                                 )
                             )
                         )
                   ) AS legal_hold
            FROM recordings_s3 r
            LEFT JOIN calls c ON c.id = r.call_id
            WHERE {where}
            ORDER BY r.created_at DESC
            LIMIT ${idx} OFFSET ${idx+1}
            """,
            *params,
            page_size,
            offset,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM recordings_s3 r WHERE {where}",
            *params,
        )

    items = [
        RecordingListItem(
            id=str(r["id"]),
            call_id=str(r["call_id"]),
            phone_number=r["phone_number"],
            created_at=r["created_at"].isoformat(),
            duration_seconds=r["duration_seconds"],
            file_size_bytes=r["file_size_bytes"],
            status=r["status"],
            legal_hold=bool(r["legal_hold"]),
        )
        for r in rows
    ]
    return RecordingListResponse(items=items, page=page, page_size=page_size, total=total or 0)


@router.get("/{recording_id}/url", response_model=RecordingUrlResponse)
async def get_recording_url(
    recording_id: UUID,
    current_user: CurrentUser = Depends(
        _require_recording_permission(Permission.RECORDINGS_DOWNLOAD)
    ),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Return a presigned S3 URL for direct audio download.
    URL is valid for 1 hour (S3_PRESIGNED_URL_EXPIRY).
    """
    tenant_id = _tenant_id(current_user)
    row = await _recording_storage_row(db_client, recording_id, tenant_id)
    _assert_recording_playable(row)
    if row["s3_bucket"] == "local":
        url = f"/api/v1/recordings/{recording_id}/download"
    else:
        s3 = S3Client()
        if not s3.is_available():
            raise HTTPException(
                status_code=503,
                detail={"code": "recording_storage_unavailable"},
            )
        try:
            url = await asyncio.to_thread(
                s3.presigned_url,
                str(row["s3_key"]),
                f"recording-{recording_id}.wav",
                str(row["s3_bucket"]),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "recording_storage_unavailable"},
            ) from exc

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.RECORD_EXPORTED,
        actor_id=current_user.id,
        actor_type="user",
        tenant_id=tenant_id,
        resource_type="recording",
        resource_id=recording_id,
        action="recording_download_url_created",
        description="Tenant user requested recording download access",
        metadata={"call_id": str(row["call_id"]), "storage": str(row["s3_bucket"])},
    )

    return RecordingUrlResponse(
        url=url,
        expires_in=int(__import__("os").getenv("S3_PRESIGNED_URL_EXPIRY", "3600")),
        recording_id=str(recording_id),
        mime_type="audio/wav",
    )


def _ranged_file_response(
    filepath: str,
    request: Request,
    media_type: str = "audio/wav",
    filename: Optional[str] = None,
    disposition: str = "inline",
) -> Response:
    """Serve a local file with HTTP Range support so the audio player can seek.

    Starlette 0.35's FileResponse ignores the Range header, so dragging the
    progress bar did nothing for local-disk recordings. This honors a single
    `bytes=start-end` range with a 206 + Content-Range, and advertises
    Accept-Ranges on the full 200 response too.
    """
    file_size = os.path.getsize(filepath)
    headers = {"Accept-Ranges": "bytes"}
    if filename:
        safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
        headers["Content-Disposition"] = f'{disposition}; filename="{safe_filename}"'

    range_header = request.headers.get("range")
    start, end, status_code = 0, file_size - 1, 200
    if range_header and range_header.strip().startswith("bytes="):
        try:
            rng = range_header.split("=", 1)[1].split(",")[0].strip()
            s, _, e = rng.partition("-")
            start = int(s) if s.strip() else 0
            end = int(e) if e.strip() else file_size - 1
            if start > end or start >= file_size:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
                )
            end = min(end, file_size - 1)
            status_code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        except (ValueError, IndexError):
            start, end, status_code = 0, file_size - 1, 200

    length = end - start + 1
    headers["Content-Length"] = str(length)

    def _iter():
        remaining = length
        with open(filepath, "rb") as f:
            f.seek(start)
            while remaining > 0:
                data = f.read(min(64 * 1024, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        _iter(), status_code=status_code, media_type=media_type, headers=headers
    )


def _validated_recording_path(raw_path: str) -> str:
    """Resolve a local object key only inside the configured recording root."""

    # abspath/commonpath alone is vulnerable to an in-root symlink or Windows
    # junction whose target is outside the storage directory. Resolve both the
    # configured root and candidate through the filesystem before comparing.
    root = os.path.realpath(
        os.path.abspath(os.getenv("LOCAL_RECORDINGS_DIR", "./recordings"))
    )
    path = os.path.realpath(os.path.abspath(raw_path))
    try:
        inside = os.path.commonpath((root, path)) == root
    except ValueError:
        inside = False
    if not inside:
        logger.error("recording_path_outside_root")
        raise HTTPException(status_code=500, detail={"code": "recording_storage_invalid"})
    return path


async def _recording_storage_row(
    db_client: Client,
    recording_id: UUID,
    tenant_id: str,
):
    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        return await conn.fetchrow(
            """
            SELECT id, call_id, tenant_id, s3_key, s3_bucket, status, mime_type
            FROM recordings_s3
            WHERE id = $1 AND tenant_id = $2
            """,
            recording_id,
            UUID(tenant_id),
        )


def _assert_recording_playable(row: Any) -> None:
    if not row:
        raise HTTPException(status_code=404, detail={"code": "recording_not_found"})
    if row["status"] != "uploaded":
        raise HTTPException(
            status_code=409,
            detail={"code": "recording_not_playable", "status": str(row["status"])},
        )


@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(_require_recording_permission(Permission.RECORDINGS_READ)),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Stream a recording.
    - Local recordings (bucket='local'): served from disk with HTTP Range support.
    - S3 recordings (status='uploaded'): 302 redirect to presigned S3 URL
      (S3 honors Range natively, so seeking works there too).
    """
    tenant_id = _tenant_id(current_user)
    row = await _recording_storage_row(db_client, recording_id, tenant_id)
    _assert_recording_playable(row)

    if row["s3_bucket"] == "local":
        filepath = _validated_recording_path(str(row["s3_key"]))
        if not os.path.isfile(filepath):
            raise HTTPException(status_code=404, detail={"code": "recording_file_missing"})
        await _safe_audit(
            audit_logger,
            event_type=AuditEvent.RECORD_VIEWED,
            actor_id=current_user.id,
            actor_type="user",
            tenant_id=tenant_id,
            resource_type="recording",
            resource_id=recording_id,
            action="recording_played",
            description="Tenant user played a call recording",
            metadata={"call_id": str(row["call_id"]), "storage": "local"},
        )
        return _ranged_file_response(
            filepath,
            request,
            media_type=str(row["mime_type"] or "audio/wav"),
            filename=f"recording-{recording_id}.wav",
        )

    # S3 path: generate a short-lived URL from the already tenant-scoped row.
    s3 = S3Client()
    if not s3.is_available():
        raise HTTPException(
            status_code=503,
            detail={"code": "recording_storage_unavailable"},
        )
    try:
        url = await asyncio.to_thread(
            s3.presigned_url,
            str(row["s3_key"]),
            None,
            str(row["s3_bucket"]),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "recording_storage_unavailable"},
        ) from exc

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.RECORD_VIEWED,
        actor_id=current_user.id,
        actor_type="user",
        tenant_id=tenant_id,
        resource_type="recording",
        resource_id=recording_id,
        action="recording_played",
        description="Tenant user played a call recording",
        metadata={"call_id": str(row["call_id"]), "storage": "object"},
    )

    return RedirectResponse(
        url=url,
        status_code=302,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{recording_id}/download")
async def download_recording(
    recording_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(
        _require_recording_permission(Permission.RECORDINGS_DOWNLOAD)
    ),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Download recording bytes through a separately revocable permission."""

    tenant_id = _tenant_id(current_user)
    row = await _recording_storage_row(db_client, recording_id, tenant_id)
    _assert_recording_playable(row)
    filename = f"recording-{recording_id}.wav"

    if row["s3_bucket"] == "local":
        filepath = _validated_recording_path(str(row["s3_key"]))
        if not os.path.isfile(filepath):
            raise HTTPException(status_code=404, detail={"code": "recording_file_missing"})
        response = _ranged_file_response(
            filepath,
            request,
            media_type=str(row["mime_type"] or "audio/wav"),
            filename=filename,
            disposition="attachment",
        )
    else:
        s3 = S3Client()
        if not s3.is_available():
            raise HTTPException(
                status_code=503,
                detail={"code": "recording_storage_unavailable"},
            )
        try:
            url = await asyncio.to_thread(
                s3.presigned_url,
                str(row["s3_key"]),
                filename,
                str(row["s3_bucket"]),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "recording_storage_unavailable"},
            ) from exc
        response = RedirectResponse(
            url=url,
            status_code=302,
            headers={"Cache-Control": "private, no-store"},
        )

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.RECORD_EXPORTED,
        actor_id=current_user.id,
        actor_type="user",
        tenant_id=tenant_id,
        resource_type="recording",
        resource_id=recording_id,
        action="recording_downloaded",
        description="Tenant user downloaded a call recording",
        metadata={"call_id": str(row["call_id"]), "storage": str(row["s3_bucket"])},
    )
    return response


@router.delete("/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: UUID,
    payload: RecordingDeleteRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
    current_user: CurrentUser = Depends(
        _require_recording_permission(Permission.RECORDINGS_DELETE)
    ),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Permanently erase bytes only after a durable, retryable intent commits."""

    # Reuse the same proven two-system deletion state machine as the platform
    # admin endpoint.  This import is intentionally local because admin.media
    # imports the shared ranged-response helper from this module.
    from app.api.v1.endpoints.admin.media import (
        _claim_media_deletion,
        _complete_recording_deletion,
        _execute_serialized_media_deletion,
    )

    tenant_id = UUID(_tenant_id(current_user))
    try:
        claim = await _claim_media_deletion(
            db_client,
            resource_type="recording",
            resource_id=recording_id,
            actor_id=current_user.id,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            expected_tenant_id=tenant_id,
        )
    except HTTPException as exc:
        if exc.status_code == 423:
            raise HTTPException(
                status_code=423,
                detail={"code": "recording_legal_hold"},
            ) from exc
        if exc.status_code == 409:
            raise HTTPException(
                status_code=409,
                detail={"code": "idempotency_conflict"},
            ) from exc
        raise

    if claim is None or claim.status == "completed":
        return Response(status_code=204)

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

        try:
            storage_error = await _execute_serialized_media_deletion(
                db_client,
                claim,
                erase_recording_object,
            )
        except HTTPException as exc:
            if exc.status_code == 423:
                raise HTTPException(
                    status_code=423,
                    detail={"code": "recording_legal_hold"},
                ) from exc
            if exc.status_code == 409:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "recording_delete_in_progress"},
                ) from exc
            raise

        if storage_error is not None:
            raise HTTPException(
                status_code=503,
                detail={"code": "recording_delete_storage_failed"},
            ) from storage_error

    response_body = {
        "detail": "Recording permanently deleted",
        "deletion_intent_id": str(claim.intent_id),
    }
    try:
        await _complete_recording_deletion(
            db_client,
            claim=claim,
            recording_id=recording_id,
            response_body=response_body,
            expected_tenant_id=tenant_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "recording_delete_metadata_pending"},
        ) from exc

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.CONFIG_CHANGED,
        actor_id=current_user.id,
        actor_type="user",
        tenant_id=tenant_id,
        resource_type="recording",
        resource_id=recording_id,
        action="recording_deleted_by_tenant",
        description="Authorized tenant user permanently deleted a call recording",
        metadata={
            "call_id": str(row["call_id"]),
            "deletion_intent_id": str(claim.intent_id),
            "reason": payload.reason,
            "origin_actor_id": claim.origin_actor_id,
            "resumed_by_different_actor": claim.resumed_by_different_actor,
        },
    )
    return Response(status_code=204)
