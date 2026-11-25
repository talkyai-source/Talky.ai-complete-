"""Customer routes for telephony number requests (the "Buy Now" documents).

What happens to the upload, in plain words. The security middleware has already
read the whole request body into memory (up to the limit for this path, about
20 MB) before this route runs. This route declares no ``File``/``Form``
parameters, so FastAPI does not parse it again; it hands that in-memory copy to
``SubmissionReader`` piece by piece. What the per-file limit bounds is the DISK:
each file is encrypted as it is written to a temp file and is refused once it
passes the limit. Nothing from a file's content or original name is logged or
returned.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.api.v1.endpoints.auth import limiter
from app.core.errors import ApiError
from app.core.security.rbac import normalize_role
from app.domain.services.number_requests import config, errors, service
from app.domain.services.number_requests.intake import SubmissionReader
from app.domain.services.number_requests.store import get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telephony/number-requests", tags=["telephony"])

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

_STORAGE_MESSAGE = "Your documents could not be saved. Nothing was kept. Please try again."
_UNCONFIRMED_MESSAGE = "We could not confirm that your documents were saved. Please send the same files again."


class NumberRequestConfigOut(BaseModel):
    accepted_types: list[str]
    accepted_extensions: list[str]
    max_file_bytes: int


class NumberRequestDocumentOut(BaseModel):
    kind: str
    file_name: str | None
    content_type: str
    size_bytes: int


class NumberRequestOut(BaseModel):
    id: UUID
    use_type: Literal["business", "personal"]
    status: Literal["submitted", "approved", "rejected"]
    submitted_at: datetime
    documents: list[NumberRequestDocumentOut]


def _user_key(request: Request) -> str:
    uid = getattr(request.state, "authenticated_user_id", None)
    if uid:
        return f"number-requests-user:{uid}"
    return f"number-requests-ip:{request.client.host if request.client else 'unknown'}"


def _require_member(current_user: CurrentUser = Depends(get_current_user)) -> None:
    """403 checks that run before the rate limiter, as the design orders them."""
    if not current_user.tenant_id:
        raise ApiError(code="tenant_required", status=403,
                       message="Sign in to an account to send documents.")
    if config.SUBMIT_ROLES is not None and normalize_role(current_user.role).value not in config.SUBMIT_ROLES:
        raise ApiError(code="role_not_allowed", status=403,
                       message="Only an account admin can send these documents.")


def _refusal(exc: errors.NumberRequestRefused) -> ApiError:
    details = {"field": exc.field, **exc.extra} if exc.field else dict(exc.extra)
    return ApiError(code=exc.code, status=exc.status, message=exc.message, details=details)


def _unavailable() -> ApiError:
    return ApiError(code="storage_unavailable", status=503, message=_STORAGE_MESSAGE,
                    headers={"Retry-After": "30"})


def _free_disk_bytes(root: str) -> int:
    """Free bytes on the disk holding ``root`` (or its nearest existing parent folder)."""
    path = root
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return shutil.disk_usage(path).free


async def _refuse_if_disk_low() -> None:
    floor = config.min_free_disk_bytes()
    if floor <= 0:
        return
    try:
        free = await asyncio.to_thread(_free_disk_bytes, config.documents_root())
    except Exception as exc:
        # Cannot measure: do not refuse; the write path decides.
        logger.warning("number_request_disk_unmeasurable error=%s", type(exc).__name__)
        return
    if free < floor:
        logger.error("number_request_disk_low free_mb=%d floor_mb=%d", free // 1048576, floor // 1048576)
        raise _unavailable()


@router.get("/config", response_model=NumberRequestConfigOut)
async def get_number_request_config(
    current_user: CurrentUser = Depends(get_current_user),
) -> NumberRequestConfigOut:
    return NumberRequestConfigOut(
        accepted_types=config.accepted_content_types(),
        accepted_extensions=config.accepted_extensions(),
        max_file_bytes=config.max_file_bytes(),
    )


_FILE_FIELDS = {
    "type": "object",
    "properties": {
        "use_type": {"type": "string", "enum": list(config.USE_TYPES)},
        **{kind: {"type": "string", "format": "binary"} for kind in config.KIND_LABELS},
    },
    "required": ["use_type"],
}


@router.post(
    "",
    status_code=201,
    response_model=NumberRequestOut,
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": _FILE_FIELDS}}}},
)
@limiter.limit(config.SUBMISSIONS_PER_USER, key_func=_user_key)
async def submit_number_request(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    _member: None = Depends(_require_member),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise ApiError(code="idempotency_key_required", status=400,
                       message="Send an Idempotency-Key header with a new random ID for this submission.",
                       details={"field": "Idempotency-Key"})
    if not _KEY_RE.match(key):
        raise ApiError(code="idempotency_key_invalid", status=400,
                       message="The Idempotency-Key must be 8 to 128 letters, digits, hyphens or underscores.",
                       details={"field": "Idempotency-Key"})

    await _refuse_if_disk_low()

    store = get_store()
    reader: SubmissionReader | None = None
    try:
        reader = SubmissionReader(request.headers.get("content-type", ""),
                                  tenant_id=str(current_user.tenant_id), store=store)
        async for chunk in request.stream():
            await asyncio.to_thread(reader.feed, chunk)
        sealed = await asyncio.to_thread(reader.finish)
        out, replayed = await service.submit(
            pool, tenant_id=str(current_user.tenant_id), user_id=str(current_user.id),
            idempotency_key=key, sealed=sealed, store=store)
    except errors.NumberRequestRefused as exc:
        if reader is not None:
            reader.abort()
        raise _refusal(exc) from None
    except errors.SaveUnconfirmed:
        if reader is not None:
            reader.abort()
        logger.error("number_request_save_unconfirmed tenant=%s", current_user.tenant_id)
        raise ApiError(code="save_unconfirmed", status=503, message=_UNCONFIRMED_MESSAGE,
                       headers={"Retry-After": "30"}) from None
    except (errors.StorageUnavailable, errors.StoragePathError, OSError):
        if reader is not None:
            reader.abort()
        logger.error("number_request_storage_unavailable tenant=%s", current_user.tenant_id)
        raise _unavailable() from None
    except BaseException:
        if reader is not None:
            reader.abort()
        raise

    headers = {"Idempotent-Replay": "true"} if replayed else None
    return JSONResponse(status_code=201, content=jsonable_encoder(NumberRequestOut(**out)), headers=headers)
