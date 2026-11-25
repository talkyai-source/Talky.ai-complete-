"""Platform staff review of telephony number requests.

List, read, open one document, approve or reject. Every list, read, document
opening and decision is written to the audit log. A document is opened only
after its audit row is committed. The customer is emailed the decision after
the decision has committed; the email never undoes it.

Nothing from a file's content or its original name reaches a log line, an audit
field, an email or an error message.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_db_pool, require_platform_admin
from app.core.db_utils import acquire_with_tenant
from app.core.errors import ApiError
from app.core.security.platform_admin_guard import (
    require_current_platform_admin,
    require_platform_admin_mutation,
)
from app.core.security.principal import PrincipalUnavailable, load_session_principal
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.number_requests import config, notify, scan, staff_service
from app.domain.services.number_requests import store as document_store
from app.domain.services.number_requests.errors import DocumentFileMissing

logger = logging.getLogger(__name__)

router = APIRouter()

_RESOURCE = "telephony_number_request"
_DOWNLOAD_EXT = {t.content_type: t.download_ext for t in config.ACCEPTED_TYPES}
_DOCUMENT_RESPONSES = {
    200: {
        "description": "The decrypted document, as a download.",
        "content": {t.content_type: {"schema": {"type": "string", "format": "binary"}}
                    for t in config.ACCEPTED_TYPES},
    }
}


class RequestListItem(BaseModel):
    id: UUID
    tenant_id: UUID
    business_name: str | None = None
    requested_by_user_id: UUID
    use_type: str
    status: str
    submitted_at: datetime
    reviewed_at: datetime | None = None
    files_deleted: bool


class RequestList(BaseModel):
    items: list[RequestListItem]
    limit: int
    offset: int
    has_more: bool


class DocumentDetail(BaseModel):
    id: UUID
    kind: str
    file_name: str | None = None
    content_type: str
    size_bytes: int
    sha256: str
    scan_status: str
    created_at: datetime
    available: bool


class RequestDetail(BaseModel):
    id: UUID
    tenant_id: UUID
    business_name: str | None = None
    requested_by_user_id: UUID
    requester_email: str | None = None
    use_type: str
    status: str
    review_note: str | None = None
    reviewed_by_user_id: UUID | None = None
    reviewed_at: datetime | None = None
    submitted_at: datetime
    files_deleted: bool
    documents: list[DocumentDetail]


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    note: str | None = Field(None, max_length=1000)


class DecisionOut(BaseModel):
    id: UUID
    status: str
    reviewed_at: datetime
    email: Literal["sent", "failed", "skipped", "off"]


def _unavailable() -> ApiError:
    return ApiError(code="staff_unavailable", status=503,
                    message="This could not be completed. Nothing was changed. Try again.")


def _not_found(code: str, message: str) -> ApiError:
    return ApiError(code=code, status=404, message=message)


async def _session(conn, request, user):
    actor = str(user.id)
    sid = getattr(request.state, "authenticated_session_id", None)
    if getattr(request.state, "authenticated_user_id", None) != actor or not sid:
        raise HTTPException(401, "Current authenticated session is required.")
    try:
        principal = await load_session_principal(conn, {"sub": actor, "sid": sid, "tenant_id": user.tenant_id})
    except PrincipalUnavailable as exc:
        raise HTTPException(401, "Current account or session is unavailable.") from exc
    if principal["role"] != "platform_admin":
        raise HTTPException(403, "Current platform admin authority is required.")


def _detail(row: dict) -> dict:
    docs = [{
        "id": d["id"], "kind": d["kind"], "file_name": d["file_name"], "content_type": d["content_type"],
        "size_bytes": d["size_bytes"], "sha256": d["sha256"], "scan_status": d["scan_status"],
        "created_at": d["created_at"], "available": d["files_deleted_at"] is None,
    } for d in row["documents"]]
    return {
        "id": row["id"], "tenant_id": row["tenant_id"], "business_name": row["business_name"],
        "requested_by_user_id": row["requested_by_user_id"], "requester_email": row["requester_email"],
        "use_type": row["use_type"], "status": row["status"], "review_note": row["review_note"],
        "reviewed_by_user_id": row["reviewed_by_user_id"], "reviewed_at": row["reviewed_at"],
        "submitted_at": row["submitted_at"], "files_deleted": row["files_deleted_at"] is not None,
        "documents": docs,
    }


@router.get("/telephony/number-requests", response_model=RequestList)
async def list_number_requests(
    request: Request,
    status: Literal["submitted", "approved", "rejected"] = Query("submitted"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=1000),
    user: CurrentUser = Depends(require_platform_admin),
    pool: asyncpg.Pool = Depends(get_db_pool),
    audit: AuditLogger = Depends(get_audit_logger),
):
    try:
        async with asyncio.timeout(10), acquire_with_tenant(pool, None, user_id=user.id, timeout=3) as conn:
            await _session(conn, request, user)
            rows, has_more = await staff_service.list_requests(conn, status=status, limit=limit, offset=offset)
        items = [{
            "id": r["id"], "tenant_id": r["tenant_id"], "business_name": r["business_name"],
            "requested_by_user_id": r["requested_by_user_id"], "use_type": r["use_type"],
            "status": r["status"], "submitted_at": r["submitted_at"], "reviewed_at": r["reviewed_at"],
            "files_deleted": r["files_deleted_at"] is not None,
        } for r in rows]
        await audit.log(
            AuditEvent.RECORD_VIEWED, action="telephony_number_requests.listed", actor_id=user.id,
            actor_role="platform_admin", resource_type=_RESOURCE,
            metadata={"status": status, "limit": limit, "offset": offset, "returned": len(items)},
        )
        return {"items": items, "limit": limit, "offset": offset, "has_more": has_more}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("number_request_list_failed type=%s", type(exc).__name__)
        raise _unavailable() from exc


@router.get("/telephony/number-requests/{tenant_id}/{request_id}", response_model=RequestDetail)
async def get_number_request(
    tenant_id: UUID,
    request_id: UUID,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin),
    pool: asyncpg.Pool = Depends(get_db_pool),
    audit: AuditLogger = Depends(get_audit_logger),
):
    try:
        async with asyncio.timeout(10), acquire_with_tenant(pool, None, user_id=user.id, timeout=3) as conn:
            await audit.lock_transaction(conn)
            await require_current_platform_admin(conn, user.id)
            await _session(conn, request, user)
            row = await staff_service.get_request(conn, str(tenant_id), str(request_id))
            if row is None:
                raise _not_found("number_request_not_found", "That request was not found.")
            await audit.log_in_transaction(
                conn, event_type=AuditEvent.RECORD_VIEWED, action="telephony_number_request.viewed",
                actor_id=user.id, actor_role="platform_admin", tenant_id=str(tenant_id),
                resource_type=_RESOURCE, resource_id=str(request_id),
                metadata={"documents": len(row["documents"])},
            )
        return _detail(row)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("number_request_view_failed type=%s", type(exc).__name__)
        raise _unavailable() from exc


@router.get(
    "/telephony/number-requests/{tenant_id}/{request_id}/documents/{document_id}",
    response_class=Response,
    responses=_DOCUMENT_RESPONSES,
)
async def open_number_request_document(
    tenant_id: UUID,
    request_id: UUID,
    document_id: UUID,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin),
    pool: asyncpg.Pool = Depends(get_db_pool),
    audit: AuditLogger = Depends(get_audit_logger),
):
    tenant, req, doc = str(tenant_id), str(request_id), str(document_id)
    try:
        async with asyncio.timeout(10), acquire_with_tenant(pool, None, user_id=user.id, timeout=3) as conn:
            await _session(conn, request, user)
            row = await staff_service.get_document(conn, tenant, req, doc)
        if row is None:
            raise _not_found("document_not_found", "That document was not found.")
        if row["files_deleted_at"] is not None or row["wrapped_key"] is None:
            raise ApiError(code="document_deleted", status=410, message="That document has been deleted.")

        try:
            dek = await document_store.unwrap_dek(bytes(row["wrapped_key"]))
            plaintext = await asyncio.to_thread(
                document_store.get_store().read_plaintext, row["storage_key"],
                tenant_id=tenant, document_id=doc, kind=row["kind"], dek=dek)
        except DocumentFileMissing as exc:
            logger.error("number_request_document_file_missing tenant=%s document=%s", tenant, doc)
            raise _not_found("document_file_missing", "The stored file for that document is missing.") from exc
        except Exception as exc:  # DocumentUnreadable, a key-service failure or a read error
            logger.error("number_request_document_unreadable tenant=%s document=%s type=%s",
                         tenant, doc, type(exc).__name__)
            raise ApiError(code="document_unreadable", status=500,
                           message="That document could not be read.") from exc
        if hashlib.sha256(plaintext).hexdigest() != row["sha256"]:
            logger.error("number_request_document_unreadable tenant=%s document=%s type=sha256_mismatch",
                         tenant, doc)
            raise ApiError(code="document_unreadable", status=500, message="That document could not be read.")

        status = scan.scan_document(plaintext)
        if status == "not_scanned":
            status = row["scan_status"]
        if status == "infected" or (config.require_clean_scan() and status != "clean"):
            raise ApiError(code="document_not_cleared", status=409,
                           message="That document has not been cleared by a virus scan.",
                           details={"scan_status": status})

        # The audit row is committed before any byte is sent.
        async with asyncio.timeout(10), acquire_with_tenant(pool, None, user_id=user.id, timeout=3) as conn:
            await audit.lock_transaction(conn)
            await require_current_platform_admin(conn, user.id)
            await audit.log_in_transaction(
                conn, event_type=AuditEvent.RECORD_VIEWED, action="telephony_number_request.document_opened",
                actor_id=user.id, actor_role="platform_admin", tenant_id=tenant,
                resource_type="telephony_number_request_document", resource_id=doc,
                metadata={"request_id": req, "kind": row["kind"], "size_bytes": row["size_bytes"],
                          "sha256_verified": True, "scan_status": status},
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("number_request_document_failed type=%s", type(exc).__name__)
        raise _unavailable() from exc

    filename = f"{row['kind']}-{doc[:8]}{_DOWNLOAD_EXT.get(row['content_type'], '')}"
    return Response(
        plaintext, media_type=row["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Document-Scan": status,
        },
    )


@router.post("/telephony/number-requests/{tenant_id}/{request_id}/decision", response_model=DecisionOut)
async def decide_number_request(
    tenant_id: UUID,
    request_id: UUID,
    body: DecisionIn,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin),
    pool: asyncpg.Pool = Depends(get_db_pool),
    audit: AuditLogger = Depends(get_audit_logger),
):
    note = (body.note or "").strip() or None
    if body.decision == "rejected" and note is None:
        raise ApiError(code="note_required", status=422, message="Give a reason when you reject a request.",
                       details={"field": "note"})
    tenant, req = str(tenant_id), str(request_id)
    try:
        async with asyncio.timeout(10), acquire_with_tenant(pool, None, user_id=user.id, timeout=3) as conn:
            await audit.lock_transaction(conn)
            await require_platform_admin_mutation(conn, user.id)
            await _session(conn, request, user)
            decided = await staff_service.decide(
                conn, tenant_id=tenant, request_id=req, decision=body.decision, note=note,
                reviewer_id=str(user.id))
            if decided is None:
                current = await staff_service.fetch_request_row(conn, tenant, req)
                if current is None:
                    raise _not_found("number_request_not_found", "That request was not found.")
                raise ApiError(code="already_decided", status=409,
                               message="That request has already been decided.",
                               details={"status": current["status"]})
            await audit.log_in_transaction(
                conn, event_type=AuditEvent.TENANT_UPDATED, action=f"telephony_number_request.{body.decision}",
                actor_id=user.id, actor_role="platform_admin", tenant_id=tenant,
                resource_type=_RESOURCE, resource_id=req,
                before_state={"status": "submitted"}, after_state={"status": body.decision},
                metadata={"note_length": len(note or "")},
            )
            recipient = await staff_service.requester_email(conn, tenant, decided["requested_by_user_id"])
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("number_request_decision_failed type=%s", type(exc).__name__)
        raise ApiError(code="decision_unconfirmed", status=503,
                       message="The decision is not confirmed. Read the request again before trying again.") from exc

    if config.decision_email_enabled():
        email = await notify.send_decision_email(
            recipient=recipient, decision=body.decision, note=note, submitted_at=decided["submitted_at"])
    else:
        email = "off"
    return {"id": decided["id"], "status": decided["status"], "reviewed_at": decided["reviewed_at"],
            "email": email}
