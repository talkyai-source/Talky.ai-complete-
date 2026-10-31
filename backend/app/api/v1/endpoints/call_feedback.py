"""Call feedback audio-note endpoints (durable-first synchronous STT)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.rbac import Permission, require_permission
from app.domain.services.call_feedback_service import (
    CallFeedbackService,
    CallNotFoundError,
    FeedbackAlreadyExistsError,
    FeedbackNotFoundError,
    FeedbackStorageError,
    FeedbackTranscriptionInProgressError,
    feedback_max_audio_bytes,
    normalized_mime_type,
    supported_feedback_mime_types,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["call-feedback"])


class CallFeedbackResponse(BaseModel):
    id: str
    call_id: str
    audio_url: str
    audio_mime_type: str
    audio_size_bytes: int
    duration_seconds: float | None = None
    transcript: str | None = None
    transcript_status: Literal["pending", "done", "failed"]
    transcript_error: str | None = None
    transcription_attempts: int
    retryable: bool
    created_at: datetime
    updated_at: datetime


def get_call_feedback_service(
    db_client: Client = Depends(get_db_client),
) -> CallFeedbackService:
    return CallFeedbackService(db_client.pool)


def _tenant_id(current_user: CurrentUser) -> str:
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="No tenant context")
    return str(current_user.tenant_id)


def _response(row: dict) -> CallFeedbackResponse:
    call_id = str(row["call_id"])
    status = str(row["transcript_status"])
    return CallFeedbackResponse(
        id=str(row["id"]),
        call_id=call_id,
        audio_url=f"/api/v1/calls/{call_id}/feedback/audio",
        audio_mime_type=str(row["audio_mime_type"]),
        audio_size_bytes=int(row["audio_size_bytes"]),
        duration_seconds=(
            float(row["duration_seconds"]) if row.get("duration_seconds") is not None else None
        ),
        transcript=row.get("transcript"),
        transcript_status=status,  # type: ignore[arg-type]
        transcript_error=row.get("transcript_error"),
        transcription_attempts=int(row.get("transcription_attempts") or 0),
        retryable=status == "failed",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _expected_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, CallNotFoundError):
        return HTTPException(status_code=404, detail="Call not found")
    if isinstance(exc, FeedbackNotFoundError):
        return HTTPException(status_code=404, detail="Feedback not found")
    if isinstance(exc, FeedbackAlreadyExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FeedbackTranscriptionInProgressError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FeedbackStorageError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail="Call feedback operation failed")


@router.post(
    "/{call_id}/feedback",
    response_model=CallFeedbackResponse,
    dependencies=[Depends(require_permission(Permission.CALLS_CREATE))],
)
async def submit_call_feedback(
    call_id: str,
    audio: UploadFile = File(..., description="MediaRecorder audio blob"),
    # 30s hard cap (was 300). A note about one call is a sentence or two, and
    # every note gets transcribed, so length is a real cost. The recorder stops
    # itself at 30s; this rejects anything that reports more.
    #
    # Honest about what this does NOT do: duration_seconds is client-reported,
    # so a hostile client can under-report it. The enforceable limit is
    # feedback_max_audio_bytes(), which is measured on the actual upload below.
    duration_seconds: float | None = Form(None, ge=0, le=30),
    replace: bool = Form(
        False,
        description="Supersede this call's existing note instead of conflicting with it",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: CallFeedbackService = Depends(get_call_feedback_service),
) -> CallFeedbackResponse:
    """Save the note durably, then attempt transcription in this request.

    Deepgram failure is represented by a successful response whose
    ``transcript_status`` is ``failed``. A non-2xx response means the recording
    itself was not accepted/durably linked (or the request was unauthorized).
    """
    tenant_id = _tenant_id(current_user)
    mime_type = normalized_mime_type(audio.content_type)
    if mime_type not in supported_feedback_mime_types():
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported feedback audio type. Supported types: "
                + ", ".join(sorted(supported_feedback_mime_types()))
            ),
        )

    max_bytes = feedback_max_audio_bytes()
    raw = await audio.read(max_bytes + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Feedback audio is empty")
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Feedback audio is too large (max {max_bytes} bytes)",
        )

    try:
        row = await service.submit(
            tenant_id=tenant_id,
            call_id=call_id,
            created_by=str(current_user.id),
            audio=raw,
            mime_type=mime_type,
            duration_seconds=duration_seconds,
            replace=replace,
        )
    except Exception as exc:
        if not isinstance(
            exc,
            (
                CallNotFoundError,
                FeedbackAlreadyExistsError,
                FeedbackStorageError,
            ),
        ):
            logger.exception("submit_call_feedback failed call=%s", call_id[:12])
        raise _expected_error(exc) from exc
    return _response(row)


@router.get(
    "/{call_id}/feedback",
    response_model=CallFeedbackResponse,
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_call_feedback(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: CallFeedbackService = Depends(get_call_feedback_service),
) -> CallFeedbackResponse:
    try:
        row = await service.get_feedback(
            tenant_id=_tenant_id(current_user),
            call_id=call_id,
        )
    except Exception as exc:
        if not isinstance(exc, (CallNotFoundError, FeedbackNotFoundError)):
            logger.exception("get_call_feedback failed call=%s", call_id[:12])
        raise _expected_error(exc) from exc
    return _response(row)


@router.post(
    "/{call_id}/feedback/transcription/retry",
    response_model=CallFeedbackResponse,
    dependencies=[Depends(require_permission(Permission.CALLS_CREATE))],
)
async def retry_call_feedback_transcription(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: CallFeedbackService = Depends(get_call_feedback_service),
) -> CallFeedbackResponse:
    """Retry a failed (or stale pending) note using its durable audio object."""
    try:
        row = await service.retry_transcription(
            tenant_id=_tenant_id(current_user),
            call_id=call_id,
        )
    except Exception as exc:
        if not isinstance(
            exc,
            (
                CallNotFoundError,
                FeedbackNotFoundError,
                FeedbackTranscriptionInProgressError,
            ),
        ):
            logger.exception("retry_call_feedback_transcription failed call=%s", call_id[:12])
        raise _expected_error(exc) from exc
    return _response(row)


@router.get(
    "/{call_id}/feedback/audio",
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_call_feedback_audio(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: CallFeedbackService = Depends(get_call_feedback_service),
):
    """Serve local-dev audio or redirect to a short-lived S3 URL."""
    try:
        delivery = await service.get_audio(
            tenant_id=_tenant_id(current_user),
            call_id=call_id,
        )
    except Exception as exc:
        if not isinstance(
            exc,
            (CallNotFoundError, FeedbackNotFoundError, FeedbackStorageError),
        ):
            logger.exception("get_call_feedback_audio failed call=%s", call_id[:12])
        raise _expected_error(exc) from exc

    if delivery.redirect_url:
        return RedirectResponse(delivery.redirect_url, status_code=302)
    data = delivery.data or b""
    return Response(
        content=data,
        media_type=delivery.mime_type,
        headers={
            "Content-Length": str(len(data)),
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": "inline",
        },
    )
