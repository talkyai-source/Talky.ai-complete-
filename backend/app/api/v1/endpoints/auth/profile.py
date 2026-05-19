"""GET /auth/me + PATCH /auth/me — profile read/update."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client

from ._shared import normalize_optional_text
from .schemas import MeResponse, UpdateMeRequest

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


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MeResponse:
    """Return the current authenticated user's profile plus suspension state.

    Suspension fields (partner_status, tenant_status, suspended_*) are
    returned so the frontend can derive its SuspensionState directly from
    AuthContext.user without firing a parallel /auth/me query. The fields
    are nullable for users without a tenant or without a partner link.
    """
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT t.id                       AS tenant_id,
                   t.white_label_partner_id   AS partner_id,
                   t.status                   AS tenant_status,
                   t.suspended_at             AS tenant_suspended_at,
                   t.suspension_reason        AS tenant_suspension_reason,
                   p.status                   AS partner_status,
                   p.suspended_at             AS partner_suspended_at,
                   p.suspension_reason        AS partner_suspension_reason
            FROM   user_profiles up
            LEFT   JOIN tenants t                ON t.id = up.tenant_id
            LEFT   JOIN white_label_partners p   ON p.id = t.white_label_partner_id
            WHERE  up.id = $1
            """,
            current_user.id,
        )

    tenant_id = str(row["tenant_id"]) if row and row["tenant_id"] else None
    partner_id = str(row["partner_id"]) if row and row["partner_id"] else None
    tenant_status = row["tenant_status"] if row else None
    partner_status = row["partner_status"] if row else None

    suspended_scope = _derive_suspension_scope(tenant_status, partner_status)
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
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        business_name=current_user.business_name,
        role=current_user.role,
        minutes_remaining=current_user.minutes_remaining,
        partner_id=partner_id,
        tenant_id=tenant_id or current_user.tenant_id,
        partner_status=partner_status,
        tenant_status=tenant_status,
        suspended_scope=suspended_scope,
        suspension_reason=suspension_reason,
        suspended_at=suspended_at_dt.isoformat() if suspended_at_dt else None,
    )


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateMeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MeResponse:
    """Update editable profile fields (name, business_name)."""
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

    if body.name is None and body.business_name is None:
        return MeResponse(
            id=current_user.id,
            email=current_user.email,
            name=current_user.name,
            business_name=current_user.business_name,
            role=current_user.role,
            minutes_remaining=current_user.minutes_remaining,
        )

    async with db_client.pool.acquire() as conn:
        async with conn.transaction():
            if body.name is not None:
                await conn.execute(
                    "UPDATE user_profiles SET name = $1 WHERE id = $2",
                    next_name,
                    current_user.id,
                )
            if body.business_name is not None:
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

    minutes_remaining = max(
        0,
        (row["minutes_allocated"] or 0) - (row["minutes_used"] or 0),
    )
    return MeResponse(
        id=str(row["id"]),
        email=row["email"],
        name=row["name"],
        business_name=row["business_name"],
        role=row["role"],
        minutes_remaining=minutes_remaining,
    )
