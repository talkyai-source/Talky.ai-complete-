"""
Recordings Endpoints — S3-backed storage

Updated to use RecordingService with S3 presigned URLs.
- List recordings: queries recordings_s3 table
- Stream/URL: returns a presigned S3 GET URL (audio served directly from S3)
- Plan-based retention access control is preserved
"""
import os

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter, verify_tenant_access
from app.domain.models.retention_config import (
    get_retention_config_for_plan,
    is_recording_accessible,
)
from app.domain.services.recording_service import RecordingService, make_recording_service

router = APIRouter(prefix="/recordings", tags=["recordings"])


class RecordingListItem(BaseModel):
    id: str
    call_id: str
    phone_number: Optional[str] = None
    created_at: str
    duration_seconds: Optional[int] = None
    file_size_bytes: Optional[int] = None
    status: str = "uploaded"


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


@router.get("/", response_model=RecordingListResponse)
async def list_recordings(
    call_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """List recordings for the current tenant, optionally filtered by call."""
    tenant_id = str(current_user.tenant_id)
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

    async with db_client.pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT r.id, r.call_id, r.created_at,
                   r.duration_seconds, r.file_size_bytes, r.status,
                   c.phone_number
            FROM recordings_s3 r
            LEFT JOIN calls c ON c.id = r.call_id
            WHERE {where}
            ORDER BY r.created_at DESC
            LIMIT ${idx} OFFSET ${idx+1}
            """,
            *params, page_size, offset,
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
        )
        for r in rows
    ]
    return RecordingListResponse(items=items, page=page, page_size=page_size, total=total or 0)


@router.get("/{recording_id}/url", response_model=RecordingUrlResponse)
async def get_recording_url(
    recording_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Return a presigned S3 URL for direct audio download.
    URL is valid for 1 hour (S3_PRESIGNED_URL_EXPIRY).
    """
    tenant_id = str(current_user.tenant_id)
    service = make_recording_service(db_client.pool)

    url = await service.get_presigned_url(recording_id, tenant_id)
    if not url:
        raise HTTPException(status_code=404, detail="Recording not found")

    return RecordingUrlResponse(
        url=url,
        expires_in=int(__import__("os").getenv("S3_PRESIGNED_URL_EXPIRY", "3600")),
        recording_id=recording_id,
        mime_type="audio/wav",
    )


@router.delete("/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Delete a recording (GDPR Article 17 DSAR — right to erasure).

    Wipes both the S3 object AND the `recordings_s3` metadata row.
    Scoped by tenant_id so a tenant can only purge their own data.

    Idempotent: re-calling on an already-deleted recording returns 204
    so front-end retries / client replays are safe.
    """
    from fastapi import Response
    import uuid

    tenant_id = str(current_user.tenant_id)

    # Fetch the S3 key before deleting the metadata row so the object
    # removal runs against the correct key. If the row is already gone
    # we treat this as idempotent success.
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, s3_key, s3_bucket, status
            FROM recordings_s3
            WHERE id = $1 AND tenant_id = $2
            """,
            uuid.UUID(recording_id),
            uuid.UUID(tenant_id),
        )

    if not row:
        return Response(status_code=204)

    service = make_recording_service(db_client.pool)
    try:
        # Only call S3 delete for actual S3 objects — local/dev
        # recordings have s3_bucket='local' and a filesystem path.
        if row["s3_bucket"] != "local" and row["s3_key"]:
            service._s3.delete(row["s3_key"])
        elif row["s3_bucket"] == "local" and row["s3_key"]:
            try:
                os.remove(row["s3_key"])
            except FileNotFoundError:
                pass  # already gone — fine
    except Exception as exc:
        # Don't hide a real failure — GDPR compliance needs proof the
        # data was actually removed. Bubble up so callers get a 5xx and
        # can retry / escalate.
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete underlying recording object: {exc}",
        ) from exc

    async with db_client.pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM recordings_s3 WHERE id = $1 AND tenant_id = $2",
            uuid.UUID(recording_id),
            uuid.UUID(tenant_id),
        )

    return Response(status_code=204)


def _ranged_file_response(
    filepath: str,
    request: Request,
    media_type: str = "audio/wav",
    filename: Optional[str] = None,
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
        headers["Content-Disposition"] = f'inline; filename="{filename}"'

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


@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Stream a recording.
    - Local recordings (bucket='local'): served from disk with HTTP Range support.
    - S3 recordings (status='uploaded'): 302 redirect to presigned S3 URL
      (S3 honors Range natively, so seeking works there too).
    """
    tenant_id = str(current_user.tenant_id)

    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, s3_key, s3_bucket, status
            FROM recordings_s3
            WHERE id = $1 AND tenant_id = $2
            """,
            __import__("uuid").UUID(recording_id),
            __import__("uuid").UUID(tenant_id),
        )

    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")

    if row["s3_bucket"] == "local":
        filepath = row["s3_key"]
        if not os.path.isfile(filepath):
            raise HTTPException(status_code=404, detail="Recording file not found on disk")
        return _ranged_file_response(
            filepath, request, media_type="audio/wav", filename=f"{recording_id}.wav"
        )

    # S3 path: generate presigned URL and redirect
    service = make_recording_service(db_client.pool)
    url = await service.get_presigned_url(recording_id, tenant_id)
    if not url:
        raise HTTPException(status_code=404, detail="Recording not found or expired")

    return RedirectResponse(url=url, status_code=302)
