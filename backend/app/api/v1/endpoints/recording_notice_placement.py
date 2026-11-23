"""Where the agent says the recording notice on outgoing calls (tenant admins only)."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_pool,
    require_admin_tenant,
)
from app.api.v1.endpoints.recordings import (
    RecordingPolicyResponse,
    _policy_response,
    _tenant_id,
)
from app.core.db_utils import acquire_with_tenant
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.notice_placement import placement_choice_enabled

router = APIRouter(prefix="/recordings", tags=["recordings"])
logger = logging.getLogger(__name__)


class NoticePlacementBody(BaseModel):
    outbound_notice_placement: Literal["own_sentence_first", "in_opening"]


@router.put("/policy/outbound-notice-placement", response_model=RecordingPolicyResponse)
async def put_outbound_notice_placement(
    body: NoticePlacementBody,
    user: CurrentUser = Depends(require_admin_tenant),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Choose where outgoing calls say the recording notice (tenant admins only).

    Changes only this choice on the saved policy; it never changes whether a
    notice is owed.
    """
    tenant_id = _tenant_id(user)
    if not placement_choice_enabled():
        # The platform has not opened this choice: nothing is read or written.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "notice_placement_unavailable"},
        )
    # The change and its audit record commit together; an audit failure rolls
    # the change back. Leaving the block by an exception writes nothing.
    async with acquire_with_tenant(db_pool, tenant_id) as conn:
        await audit_logger.lock_transaction(conn)
        try:
            row = await conn.fetchrow(
                "UPDATE tenant_recording_policy SET outbound_notice_placement = $2, "
                "updated_at = NOW() WHERE tenant_id = $1 RETURNING *",
                UUID(tenant_id),
                body.outbound_notice_placement,
            )
        except asyncpg.UndefinedColumnError:
            # Only this statement's own missing column: the database is older
            # than this code. A fault in the audit record is not hidden here.
            logger.warning(
                "recording_notice_placement_unavailable tenant=%s reason=column_missing",
                tenant_id[:8],
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "notice_placement_unavailable"},
            )
        if row is not None:
            await audit_logger.log_in_transaction(
                conn,
                event_type=AuditEvent.CONFIG_CHANGED,
                action="recording_notice_placement_updated",
                actor_id=str(user.id), actor_role=user.role, tenant_id=tenant_id,
                resource_type="tenant_recording_policy", resource_id=tenant_id,
                description="Tenant admin chose where outgoing calls say the recording notice",
                after_state={"outbound_notice_placement": body.outbound_notice_placement},
                metadata={"outbound_notice_placement": body.outbound_notice_placement},
            )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "recording_policy_not_saved"},
        )
    logger.info(
        "recording_notice_placement_saved tenant=%s placement=%s by user=%s",
        tenant_id[:8], body.outbound_notice_placement, user.id,
    )
    return _policy_response(dict(row))
