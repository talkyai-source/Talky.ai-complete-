"""Team invites for workspace administrators (QA F1-15 / D4-2).

  GET    /rbac/tenant-invites         - open invites, newest first
  POST   /rbac/tenant-invites         - invite someone by email (again: a new link)
  DELETE /rbac/tenant-invites/{id}    - withdraw an invite

Owner decisions, 11 Nov 2026: an email that already has an account is
refused, an invite lasts 7 days, and any role up to tenant admin may be
invited. Invites live in Redis (app.domain.services.team_invites); nothing
reaches Postgres until the person accepts at POST /auth/invites/accept.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import (
    CurrentUser,
    UserRole,
    get_audit_logger,
    get_db_client,
    require_role,
)
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.auth_abuse import enforce_budget
from app.core.security.rbac import normalize_role
from app.domain.services import team_invites
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.email_service import get_email_service

from .schemas import (
    InviteTeammateRequest,
    TenantInviteCreatedResponse,
    TenantInviteResponse,
    TenantInviteWithdrawnResponse,
)
from .tenant_users import _check_role_ceiling, _current_target_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rbac"])


def _workspace_of(current_user: CurrentUser) -> str:
    if not current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant ID required")
    return str(current_user.tenant_id)


def _redis_or_503():
    from app.core.container import get_container
    container = get_container()
    if not container.is_initialized or not container.redis_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invites are unavailable right now. Try again shortly.",
        )
    return container.redis


def _summary(invite):
    return dict(id=invite.id, email=invite.email, role=invite.role_name,
                created_at=invite.created_at, expires_at=invite.expires_at)


def _state(invite):
    if invite is None:
        return None
    return {"id": invite.id, "email": invite.email, "role": invite.role_name, "expires_at": invite.expires_at}


@router.get("/tenant-invites", response_model=List[TenantInviteResponse])
async def list_tenant_invites(
    current_user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN)),
    db_client: Client = Depends(get_db_client),
) -> List[TenantInviteResponse]:
    """Open invites to the caller's workspace. **Requires tenant_admin.**"""
    tenant_id = _workspace_of(current_user)
    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        await _current_target_admin(conn, current_user, tenant_id)
    invites = await team_invites.pending(_redis_or_503(), tenant_id, now=datetime.now(timezone.utc))
    return [TenantInviteResponse(**_summary(invite)) for invite in invites]


@router.post(
    "/tenant-invites",
    response_model=TenantInviteCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_teammate(
    request: InviteTeammateRequest,
    current_user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN)),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> TenantInviteCreatedResponse:
    """Email someone a link to join the caller's workspace with a role.

    **Requires tenant_admin.** Inviting the same email again replaces the
    earlier link. The response says whether the email went out.
    """
    tenant_id = _workspace_of(current_user)
    email = str(request.email).strip().lower()
    if normalize_role(request.role_name).level > normalize_role(current_user.role).level:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot grant a role above your own tier")
    redis = _redis_or_503()
    # The account check must see every workspace, as signup's does, so this
    # runs without a tenant context; each tenant query names its tenant.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await audit_logger.lock_transaction(conn)
        user_role = await _current_target_admin(conn, current_user, tenant_id)
        _check_role_ceiling(user_role, request.role_name)
        # Security review: cap invite emails per workspace and per recipient,
        # which also bounds how often the account check below can be asked
        # (public signup already answers it). Claimed only once authorized.
        await enforce_budget(redis, "invite-tenant", tenant_id, limit=20, seconds=24 * 60 * 60)
        await enforce_budget(redis, "invite-recipient", email, limit=3, seconds=24 * 60 * 60)
        if await conn.fetchval("SELECT 1 FROM user_profiles WHERE LOWER(email) = $1", email):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email already has an account, so it cannot be invited.",
            )
        workspace = await conn.fetchval("SELECT business_name FROM tenants WHERE id = $1", tenant_id)
        invite, token, previous = await team_invites.issue(
            redis, tenant_id=tenant_id, email=email, role_name=request.role_name,
            invited_by=str(current_user.id), now=datetime.now(timezone.utc),
        )
        try:
            await audit_logger.log_in_transaction(
                conn, event_type=AuditEvent.USER_UPDATED, action="rbac.invite.create",
                actor_id=current_user.id, actor_role=current_user.role, tenant_id=tenant_id,
                resource_type="tenant_invite", resource_id=invite.id,
                before_state=_state(previous), after_state=_state(invite),
            )
        except BaseException:
            # No receipt, no working link.
            await team_invites.withdraw(redis, invite)
            raise
    try:
        sent = await get_email_service().send_team_invite_email(
            recipient_email=email, workspace_name=workspace or "", inviter=current_user.name or current_user.email,
            role_name=request.role_name, token=token, expires_at=invite.expires_at,
        )
    except Exception as exc:
        logger.warning("team_invite_email_failed error_type=%s", type(exc).__name__)
        sent = False
    return TenantInviteCreatedResponse(**_summary(invite), email_sent=bool(sent))


@router.delete("/tenant-invites/{invite_id}", response_model=TenantInviteWithdrawnResponse)
async def withdraw_invite(
    invite_id: str,
    current_user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN)),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> TenantInviteWithdrawnResponse:
    """Withdraw an open invite; its link stops working at once. **Requires tenant_admin.**"""
    tenant_id = _workspace_of(current_user)
    redis = _redis_or_503()
    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        await audit_logger.lock_transaction(conn)
        user_role = await _current_target_admin(conn, current_user, tenant_id)
        invite = await team_invites.find(redis, tenant_id, invite_id, now=datetime.now(timezone.utc))
        if invite is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
        _check_role_ceiling(user_role, invite.role_name)
        await audit_logger.log_in_transaction(
            conn, event_type=AuditEvent.USER_UPDATED, action="rbac.invite.withdraw",
            actor_id=current_user.id, actor_role=current_user.role, tenant_id=tenant_id,
            resource_type="tenant_invite", resource_id=invite.id,
            before_state=_state(invite), after_state=None,
        )
        # Last, so a failure here rolls the receipt back with it.
        await team_invites.withdraw(redis, invite)
    return TenantInviteWithdrawnResponse(detail="Invite withdrawn")
