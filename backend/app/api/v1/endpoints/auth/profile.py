"""GET /auth/me + PATCH /auth/me — profile read/update."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client

from ._shared import normalize_optional_text
from .schemas import MeResponse, UpdateMeRequest

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
) -> MeResponse:
    """Return the current authenticated user's profile."""
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        business_name=current_user.business_name,
        role=current_user.role,
        minutes_remaining=current_user.minutes_remaining,
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
