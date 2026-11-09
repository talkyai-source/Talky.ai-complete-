"""GET /auth/me + PATCH /auth/me — profile read/update."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.rbac import UserRole, normalize_role
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.reporting_time import reporting_zone
from app.domain.services.subscription_status import SUSPENDED, canonical

from ._shared import get_client_ip, get_user_agent, normalize_optional_text
from .schemas import (
    AccountTimezoneRequest,
    AccountTimezoneResponse,
    LegalAcceptanceRequest,
    MeResponse,
    UpdateMeRequest,
)

router = APIRouter(tags=["auth"])


def _derive_suspension_scope(
    tenant_status: Optional[str], partner_status: Optional[str]
) -> Optional[str]:
    # Tenant suspension takes precedence — a suspended tenant under a
    # healthy partner is still tenant-scoped. Partner-only suspension
    # cascades to every tenant under it.
    if tenant_status == "suspended":
        return "tenant"
    if partner_status == "suspended":
        return "partner"
    return None


def _zone_name(*candidates) -> Optional[str]:
    """The first valid IANA zone among the candidates, as a name."""
    for value in candidates:
        try:
            return reporting_zone(value).key
        except ValueError:
            continue
    return None


def _derive_account_state(
    suspended_scope: Optional[str], subscription_status: Optional[str]
) -> tuple[str, Optional[str]]:
    """One customer-facing account state plus a stable reason code.

    Admin "Suspend" writes ``tenants.subscription_status``; a formal hold
    writes ``tenants.status``. A hold outranks the subscription. Billing states
    such as ``past_due`` are not suspensions and stay in ``subscription_status``.
    The reason is a code, never the operator's free-text note.
    """
    if suspended_scope == "tenant":
        return "suspended_hold", "tenant_hold"
    if suspended_scope == "partner":
        return "suspended_hold", "partner_hold"
    if subscription_status == SUSPENDED:
        return "suspended_subscription", "subscription_suspended"
    return "active", None


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    # Last and optional: FastAPI injects it by type; direct callers that
    # predate G2 pass (current_user, db_client) and get mfa_verified=None.
    request: Request = None,
) -> MeResponse:
    """Return the current authenticated user's profile plus suspension state.

    Suspension fields (partner_status, tenant_status, suspended_*) are
    returned so the frontend can derive its SuspensionState directly from
    AuthContext.user without firing a parallel /auth/me query. The fields
    are nullable for users without a tenant or without a partner link.
    """
    async with acquire_with_tenant(db_client.pool, current_user.tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT t.id                       AS tenant_id,
                   up.mfa_enabled             AS mfa_enabled,
                   t.white_label_partner_id   AS partner_id,
                   t.status                   AS tenant_status,
                   t.subscription_status      AS subscription_status,
                   t.calling_rules->>'timezone' AS account_timezone,
                   t.calling_rules->>'reporting_timezone' AS reporting_timezone,
                   t.suspended_at             AS tenant_suspended_at,
                   t.suspension_reason        AS tenant_suspension_reason,
                   p.status                   AS partner_status,
                   p.suspended_at             AS partner_suspended_at,
                   p.suspension_reason        AS partner_suspension_reason
            FROM   user_profiles up
            LEFT   JOIN tenants t                ON t.id = up.tenant_id
            LEFT   JOIN white_label_partners p   ON p.id = t.white_label_partner_id
            WHERE  up.id = $1 AND up.tenant_id IS NOT DISTINCT FROM $2::uuid
            """,
            current_user.id,
            current_user.tenant_id,
        )
        from app.domain.services.legal_acceptance import acceptance_status
        legal_status = await acceptance_status(
            conn, tenant_id=current_user.tenant_id, user_id=current_user.id,
        )

    tenant_id = str(row["tenant_id"]) if row and row["tenant_id"] else None
    partner_id = str(row["partner_id"]) if row and row["partner_id"] else None
    tenant_status = row["tenant_status"] if row else None
    partner_status = row["partner_status"] if row else None
    # D4-8: the saved reporting zone first, then the dialing zone.
    reporting_timezone = _zone_name(row.get("reporting_timezone") if row else None)
    account_timezone = reporting_timezone or _zone_name(row.get("account_timezone") if row else None)

    suspended_scope = _derive_suspension_scope(tenant_status, partner_status)
    subscription_status = canonical(row.get("subscription_status") if row else None) or None
    account_state, account_state_reason = _derive_account_state(suspended_scope, subscription_status)
    # Pick the reason / timestamp from whichever scope is suspended.
    # Tenant takes precedence (same precedence rule as suspended_scope).
    if suspended_scope == "tenant":
        suspension_reason = row["tenant_suspension_reason"] if row else None
        suspended_at_dt = row["tenant_suspended_at"] if row else None
    elif suspended_scope == "partner":
        suspension_reason = row["partner_suspension_reason"] if row else None
        suspended_at_dt = row["partner_suspended_at"] if row else None
    else:
        suspension_reason = None
        suspended_at_dt = None

    return MeResponse(
        **legal_status,
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        business_name=current_user.business_name,
        account_timezone=account_timezone,
        reporting_timezone=reporting_timezone,
        role=current_user.role,
        minutes_remaining=current_user.minutes_remaining,
        minutes_state=current_user.minutes_state,
        partner_id=partner_id,
        tenant_id=tenant_id or current_user.tenant_id,
        partner_status=partner_status,
        tenant_status=tenant_status,
        suspended_scope=suspended_scope,
        suspension_reason=suspension_reason,
        suspended_at=suspended_at_dt.isoformat() if suspended_at_dt else None,
        subscription_status=subscription_status,
        account_state=account_state,
        account_state_reason=account_state_reason,
        mfa_enabled=bool(row and row.get("mfa_enabled") is True),
        mfa_verified=getattr(getattr(request, "state", None), "session_mfa_verified", None),
    )


@router.put("/account/timezone", response_model=AccountTimezoneResponse)
async def set_account_timezone(
    body: AccountTimezoneRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> AccountTimezoneResponse:
    """Set the account's reporting timezone (D4-8); null clears it.

    Dashboards, analytics and /auth/me use it for dates. It is stored apart
    from calling_rules.timezone, the dialer's fallback window zone, so
    changing it never moves calling hours. Tenant-wide: tenant admins only,
    like the business name.
    """
    if not normalize_role(current_user.role).can_access(UserRole.TENANT_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a tenant admin can change the account timezone.",
        )
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant.",
        )
    zone = None
    if body.timezone is not None and body.timezone.strip():
        zone = _zone_name(body.timezone.strip())
        if zone is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_timezone",
                        "message": "Choose a valid IANA timezone, for example Asia/Karachi."},
            )

    async with acquire_with_tenant(db_client.pool, current_user.tenant_id) as conn:
        async with conn.transaction():
            before = await conn.fetchval(
                "SELECT calling_rules->>'reporting_timezone' FROM tenants WHERE id = $1::uuid FOR UPDATE",
                current_user.tenant_id,
            )
            row = await conn.fetchrow(
                """
                UPDATE tenants
                SET calling_rules = CASE
                        WHEN $2::text IS NULL
                        THEN COALESCE(calling_rules, '{}'::jsonb) - 'reporting_timezone'
                        ELSE jsonb_set(COALESCE(calling_rules, '{}'::jsonb),
                                       '{reporting_timezone}', to_jsonb($2::text), true)
                    END,
                    updated_at = NOW()
                WHERE id = $1::uuid
                RETURNING calling_rules->>'reporting_timezone' AS reporting_timezone,
                          calling_rules->>'timezone' AS dialing_timezone
                """,
                current_user.tenant_id,
                zone,
            )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    await audit_logger.log(
        event_type=AuditEvent.TENANT_UPDATED,
        action="tenant_reporting_timezone_changed",
        actor_id=current_user.id,
        actor_role=current_user.role,
        tenant_id=current_user.tenant_id,
        resource_type="tenant",
        resource_id=current_user.tenant_id,
        description="Account reporting timezone changed via PUT /auth/account/timezone",
        before_state={"reporting_timezone": before},
        after_state={"reporting_timezone": zone},
    )
    saved = _zone_name(row["reporting_timezone"])
    return AccountTimezoneResponse(
        reporting_timezone=saved,
        account_timezone=saved or _zone_name(row["dialing_timezone"]),
    )


@router.post("/legal-acceptance")
async def accept_legal_versions(
    request: Request,
    body: LegalAcceptanceRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Append the authenticated tenant user's explicit current-version receipt."""
    from app.domain.services.legal_acceptance import record_acceptance, validate_signup_acceptance
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant context required")
    versions = validate_signup_acceptance(body.terms_version, body.privacy_version)
    async with acquire_with_tenant(db_client.pool, current_user.tenant_id) as conn:
        await record_acceptance(
            conn, tenant_id=current_user.tenant_id, user_id=current_user.id,
            versions=versions, ip_address=get_client_ip(request), user_agent=get_user_agent(request),
        )
    return {"accepted": True, "terms_version": versions[0], "privacy_version": versions[1]}


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateMeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> MeResponse:
    """Update editable profile fields (name, business_name).

    ``business_name`` is tenant-wide (agent prompts, receipts), so changing it
    needs a tenant-admin role; the settings form re-sends the unchanged value
    for every member, which is accepted as a no-op.
    """
    next_name = normalize_optional_text(body.name) if body.name is not None else None
    next_business_name = (
        normalize_optional_text(body.business_name)
        if body.business_name is not None
        else None
    )

    if body.business_name is not None and not next_business_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="business_name cannot be empty.",
        )

    renames_tenant = (
        body.business_name is not None
        and next_business_name != current_user.business_name
    )
    if renames_tenant and not normalize_role(current_user.role).can_access(
        UserRole.TENANT_ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a tenant admin can change the business name.",
        )

    if body.name is None and body.business_name is None:
        return MeResponse(
            id=current_user.id,
            email=current_user.email,
            name=current_user.name,
            business_name=current_user.business_name,
            role=current_user.role,
            minutes_remaining=current_user.minutes_remaining,
            minutes_state=current_user.minutes_state,
        )

    async with acquire_with_tenant(db_client.pool, current_user.tenant_id) as conn:
        async with conn.transaction():
            if body.name is not None:
                await conn.execute(
                    "UPDATE user_profiles SET name = $1 WHERE id = $2",
                    next_name,
                    current_user.id,
                )
            if renames_tenant:
                if not current_user.tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="User is not associated with a tenant.",
                    )
                await conn.execute(
                    "UPDATE tenants SET business_name = $1 WHERE id = $2",
                    next_business_name,
                    current_user.tenant_id,
                )

        row = await conn.fetchrow(
            """
            SELECT up.id, up.email, up.name, up.role, up.tenant_id,
                   t.business_name, t.minutes_allocated, t.minutes_used
            FROM   user_profiles up
            LEFT   JOIN tenants t ON t.id = up.tenant_id
            WHERE  up.id = $1
            """,
            current_user.id,
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found.",
        )

    if renames_tenant:
        await audit_logger.log(
            event_type=AuditEvent.TENANT_UPDATED,
            action="tenant_business_name_changed",
            actor_id=current_user.id,
            actor_role=current_user.role,
            tenant_id=current_user.tenant_id,
            resource_type="tenant",
            resource_id=current_user.tenant_id,
            description="Tenant business name changed via PATCH /auth/me",
            before_state={"business_name": current_user.business_name},
            after_state={"business_name": next_business_name},
        )

    from app.services.scripts.tenant_minutes import compute_tenant_minutes_status
    meter = await compute_tenant_minutes_status(db_client.pool, current_user.tenant_id)

    return MeResponse(
        id=str(row["id"]),
        email=row["email"],
        name=row["name"],
        business_name=row["business_name"],
        role=row["role"],
        **meter.allowance(),
    )
