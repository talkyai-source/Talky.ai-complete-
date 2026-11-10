"""Accepting a team invite (QA F1-15 / D4-2).

  POST /auth/invites/preview   - what the invite is for
  POST /auth/invites/accept    - create the account and join the workspace

The token comes from the email link's fragment and travels only in these
request bodies. Accepting creates the account and its active membership in
one transaction, and spends the token last: a link withdrawn or used in the
meantime rolls the whole account back. The new member then signs in.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from asyncpg import UniqueViolationError
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.error_handlers import safe_exception_message
from app.core.postgres_adapter import Client
from app.core.security.auth_abuse import enforce_budget
from app.core.security.password import PasswordValidationError, hash_password, validate_password_strength
from app.domain.services import team_invites
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.legal_acceptance import record_acceptance, validate_signup_acceptance

from ._shared import get_client_ip, get_user_agent
from .schemas import InviteAcceptRequest, InviteAcceptResponse, InvitePreviewResponse, InviteTokenRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def _redis_or_503():
    from app.core.container import get_container
    container = get_container()
    if not container.is_initialized or not container.redis_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invites are unavailable right now. Try again shortly.",
        )
    return container.redis


def _no_longer_valid() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="This invite link is no longer valid. Ask for a new invite.",
    )


async def _invite_for(request: Request, token: str, *, limit: int):
    redis = _redis_or_503()
    await enforce_budget(redis, "invite-ip", get_client_ip(request), limit=limit, seconds=60)
    invite = await team_invites.by_token(redis, token, now=datetime.now(timezone.utc))
    if invite is None:
        raise _no_longer_valid()
    return redis, invite


@router.post("/invites/preview", response_model=InvitePreviewResponse)
async def preview_invite(
    request: Request,
    body: InviteTokenRequest,
    db_client: Client = Depends(get_db_client),
) -> InvitePreviewResponse:
    """Who the invite is for and which workspace it joins."""
    _, invite = await _invite_for(request, body.token, limit=20)
    async with acquire_with_tenant(db_client.pool, invite.tenant_id) as conn:
        workspace = await conn.fetchval("SELECT business_name FROM tenants WHERE id = $1", invite.tenant_id)
    return InvitePreviewResponse(email=invite.email, workspace_name=workspace or "",
                                 role=invite.role_name, expires_at=invite.expires_at)


@router.post("/invites/accept", response_model=InviteAcceptResponse)
async def accept_invite(
    request: Request,
    body: InviteAcceptRequest,
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> InviteAcceptResponse:
    """Create the invited person's account and active membership."""
    legal_versions = validate_signup_acceptance(body.terms_version, body.privacy_version)
    if body.password != body.confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match.")
    try:
        validate_password_strength(body.password)
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_exception_message(exc, "Invalid password."),
        ) from exc
    redis, invite = await _invite_for(request, body.token, limit=10)
    user_id = str(uuid.uuid4())
    password_hash = hash_password(body.password)
    # Like signup, the account check and insert must see every workspace.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await audit_logger.lock_transaction(conn)
        tenant = await conn.fetchrow("SELECT id, status FROM tenants WHERE id = $1", invite.tenant_id)
        if tenant is None or (tenant["status"] or "active") != "active":
            raise _no_longer_valid()
        if await conn.fetchval("SELECT 1 FROM user_profiles WHERE LOWER(email) = $1", invite.email):
            raise _already_has_account()
        role_id = await conn.fetchval("SELECT id FROM roles WHERE name = $1", invite.role_name)
        if role_id is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Account role unavailable; contact support.")
        try:
            await conn.execute(
                """
                INSERT INTO user_profiles
                    (id, email, name, tenant_id, role, password_hash, is_verified, email_verified_at)
                VALUES ($1, $2, $3, $4, $5, $6, TRUE, NOW())
                """,
                user_id, invite.email, body.name, invite.tenant_id, invite.role_name, password_hash,
            )
        except UniqueViolationError as exc:
            if exc.constraint_name == "user_profiles_email_key":
                raise _already_has_account() from exc
            raise
        # The invite is their home workspace: they had no account before it.
        membership_id = await conn.fetchval(
            """
            INSERT INTO tenant_users
                (user_id, tenant_id, role_id, is_primary, status, invited_by, invited_at, joined_at)
            VALUES ($1, $2, $3, TRUE, 'active', (SELECT id FROM user_profiles WHERE id = $4), $5, NOW())
            RETURNING id
            """,
            user_id, invite.tenant_id, role_id, invite.invited_by, datetime.fromisoformat(invite.created_at),
        )
        await record_acceptance(
            conn, tenant_id=invite.tenant_id, user_id=user_id, versions=legal_versions,
            ip_address=get_client_ip(request), user_agent=get_user_agent(request),
        )
        await audit_logger.log_in_transaction(
            conn, event_type=AuditEvent.USER_CREATED, action="rbac.invite.accept",
            actor_id=user_id, actor_role=invite.role_name, tenant_id=invite.tenant_id,
            resource_type="tenant_user", resource_id=membership_id, before_state=None,
            after_state={"invite_id": invite.id, "user_id": user_id, "role": invite.role_name,
                         "status": "active", "invited_by": invite.invited_by},
        )
        if not await team_invites.spend(redis, invite):
            raise _no_longer_valid()
    try:
        await team_invites.forget(redis, invite)
    except Exception:
        logger.warning("team_invite_list_cleanup_failed")
    return InviteAcceptResponse(email=invite.email, message="You have joined the workspace. Sign in to continue.")


def _already_has_account() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="This email already has an account. Sign in instead.",
    )
