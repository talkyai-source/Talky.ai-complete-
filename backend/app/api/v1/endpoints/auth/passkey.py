"""POST /auth/passkey-check — does this email have any registered passkeys?

Lets the login UI render a "Sign in with passkey" affordance without
revealing whether the email exists at all.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.v1.dependencies import get_db_client
from app.core.postgres_adapter import Client

from ._shared import limiter
from .schemas import LoginRequest

router = APIRouter(tags=["auth"])


@router.post("/passkey-check")
@limiter.limit("10/minute")
async def passkey_check(
    request: Request,
    body: LoginRequest,  # Reuse email field from LoginRequest
    db_client: Client = Depends(get_db_client),
) -> dict[str, bool]:
    """
    Check if a user has passkeys registered.

    This unauthenticated endpoint allows the login UI to show
    "Sign in with passkey" if the user has registered passkeys.

    Returns { "has_passkeys": true/false }

    Note: We deliberately don't reveal if the email exists at all —
    a non-existent email simply returns has_passkeys=false.
    """
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT passkey_count FROM user_profiles WHERE email = $1 AND is_active = TRUE",
            body.email.lower(),
        )

        has_passkeys = bool(row and row["passkey_count"] and row["passkey_count"] > 0)

    return {"has_passkeys": has_passkeys}
