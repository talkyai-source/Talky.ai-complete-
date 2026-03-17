"""
Shared API Dependencies

Provides dependency injection for:
- Database connection (get_db) — via asyncpg pool from ServiceContainer
- Current authenticated user (get_current_user) — via JWT
- Admin authorization (require_admin)
- Optional user (get_optional_user)

Uses local JWT verification.
"""
import logging
from typing import Optional
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Header, status
import asyncpg

from app.core.container import get_db_pool_from_container
from app.core.jwt_security import JWTValidationError, decode_and_validate_token

logger = logging.getLogger(__name__)


@dataclass
class CurrentUser:
    """User context extracted from JWT token"""
    id: str
    email: str
    tenant_id: Optional[str] = None
    role: str = "user"
    name: Optional[str] = None
    business_name: Optional[str] = None
    minutes_remaining: int = 0


def get_db_pool() -> asyncpg.Pool:
    """
    FastAPI dependency — returns the asyncpg pool from the ServiceContainer.
    """
    return get_db_pool_from_container()


from app.core.postgres_adapter import Client

# Backward-compat alias
def get_db_client(pool: asyncpg.Pool = Depends(get_db_pool)) -> Client:
    """
    Backward-compat alias -> returns Postgres adapter client wrapping asyncpg pool.
    Shim allows legacy code using .table() to work.
    """
    # When called outside FastAPI dependency injection, `pool` can be a
    # Depends marker instead of an asyncpg pool. Resolve from container.
    if not hasattr(pool, "acquire"):
        pool = get_db_pool()
    return Client(pool)


async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db_client: Client = Depends(get_db_client),
) -> CurrentUser:
    """
    Extract and validate JWT token from Authorization header.

    Returns CurrentUser with user info and tenant context.
    JWT is signed with JWT_SECRET (HS256).
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format. Expected: Bearer <token>",
        )

    token = parts[1]

    try:
        payload = decode_and_validate_token(token)
    except JWTValidationError as e:
        if e.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            logger.error(e.detail)
        else:
            logger.warning("Token verification failed: %s", e.detail)
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
        )

    # Fetch user profile with tenant info from PostgreSQL
    try:
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT up.id, up.email, up.name, up.role, up.tenant_id,
                       t.business_name, t.minutes_allocated, t.minutes_used
                FROM user_profiles up
                LEFT JOIN tenants t ON t.id = up.tenant_id
                WHERE up.id = $1
                """,
                user_id,
            )
    except Exception as e:
        logger.warning(f"Failed to fetch user profile: {e}")
        row = None

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User profile not found",
        )

    minutes_remaining = max(
        0,
        (row["minutes_allocated"] or 0) - (row["minutes_used"] or 0)
    )

    return CurrentUser(
        id=str(row["id"]),
        email=row["email"] or "",
        tenant_id=str(row["tenant_id"]) if row["tenant_id"] else None,
        role=row["role"] or "user",
        name=row["name"],
        business_name=row["business_name"],
        minutes_remaining=minutes_remaining,
    )


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Require admin role for endpoint access."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_optional_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db_client: Client = Depends(get_db_client),
) -> Optional[CurrentUser]:
    """
    Get current user if authenticated, otherwise return None.
    Useful for endpoints that work both with and without auth.
    """
    if not authorization:
        return None
    try:
        return await get_current_user(authorization=authorization, db_client=db_client)
    except HTTPException:
        return None
