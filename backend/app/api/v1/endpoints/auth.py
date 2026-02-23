"""
Authentication Endpoints
Local JWT-based authentication replacing PostgreSQL OTP auth.

Flow:
  POST /auth/register  → create tenant + user_profile + return JWT
  POST /auth/login     → verify password + return JWT
  GET  /auth/me        → return current user info
  PATCH /auth/me       → update user profile fields
  POST /auth/change-password → update account password
  POST /auth/logout    → client-side token discard (stateless JWT)
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import bcrypt
from fastapi import APIRouter, HTTPException, Depends, Request, status
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.v1.dependencies import get_current_user, CurrentUser, get_db_client
from app.core.config import get_settings
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)

# Rate limiter — keyed by client IP address
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================
# Request/Response Models
# ============================================

class RegisterRequest(BaseModel):
    """User registration request"""
    email: EmailStr
    password: str
    business_name: str
    plan_id: str = "basic"
    name: Optional[str] = None


class LoginRequest(BaseModel):
    """Login request"""
    email: EmailStr
    password: str


class AuthTokenResponse(BaseModel):
    """Auth response with JWT token"""
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    business_name: Optional[str] = None
    minutes_remaining: int = 0
    message: str


class MeResponse(BaseModel):
    """Current user response"""
    id: str
    email: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    role: str
    minutes_remaining: int


class UpdateMeRequest(BaseModel):
    """Profile update request."""
    name: Optional[str] = None
    business_name: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    """Password change request."""
    old_password: str
    new_password: str


# ============================================
# Helpers
# ============================================

def _require_jwt_secret() -> str:
    """Return configured JWT secret or fail closed."""
    secret = get_settings().effective_jwt_secret
    if secret:
        return secret
    logger.error("JWT_SECRET is not configured")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Server authentication is not configured",
    )


def _create_jwt(user_id: str, email: str, role: str) -> str:
    """Create a signed JWT token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, _require_jwt_secret(), algorithm=settings.jwt_algorithm)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


# ============================================
# Endpoints
# ============================================

@router.post("/register", response_model=AuthTokenResponse)
@limiter.limit("3/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    db_client: Client = Depends(get_db_client),
):
    """
    Register a new user.

    1. Validates plan exists
    2. Checks email not already registered
    3. Creates tenant
    4. Creates user_profile with hashed password
    5. Returns JWT token (user is immediately logged in)
    """
    async with db_client.pool.acquire() as conn:
        # Check plan exists
        plan = await conn.fetchrow(
            "SELECT id, minutes FROM plans WHERE id = $1", body.plan_id
        )
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid plan_id: {body.plan_id}",
            )

        # Check email not already registered
        existing = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE email = $1", body.email
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

        # Create tenant
        tenant = await conn.fetchrow(
            """
            INSERT INTO tenants (business_name, plan_id, minutes_allocated, minutes_used)
            VALUES ($1, $2, $3, 0)
            RETURNING id, business_name, minutes_allocated
            """,
            body.business_name,
            body.plan_id,
            plan["minutes"],
        )

        # Create user profile
        user_id = str(uuid.uuid4())
        hashed_pw = _hash_password(body.password)
        await conn.execute(
            """
            INSERT INTO user_profiles (id, email, name, tenant_id, role, password_hash)
            VALUES ($1, $2, $3, $4, 'owner', $5)
            """,
            user_id,
            body.email,
            body.name,
            tenant["id"],
            hashed_pw,
        )

    token = _create_jwt(user_id, body.email, "owner")
    return AuthTokenResponse(
        access_token=token,
        user_id=user_id,
        email=body.email,
        role="owner",
        business_name=body.business_name,
        minutes_remaining=plan["minutes"],
        message="Registration successful",
    )


@router.post("/login", response_model=AuthTokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db_client: Client = Depends(get_db_client),
):
    """
    Login with email + password. Returns JWT token.
    """
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT up.id, up.email, up.name, up.role, up.password_hash, up.tenant_id,
                   t.business_name, t.minutes_allocated, t.minutes_used
            FROM user_profiles up
            LEFT JOIN tenants t ON t.id = up.tenant_id
            WHERE up.email = $1
            """,
            body.email,
        )

    if not row or not row["password_hash"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not _verify_password(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    minutes_remaining = max(
        0,
        (row["minutes_allocated"] or 0) - (row["minutes_used"] or 0),
    )

    token = _create_jwt(str(row["id"]), row["email"], row["role"])
    return AuthTokenResponse(
        access_token=token,
        user_id=str(row["id"]),
        email=row["email"],
        role=row["role"],
        business_name=row["business_name"],
        minutes_remaining=minutes_remaining,
        message="Login successful",
    )


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get current authenticated user info."""
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
):
    """Update editable user profile fields."""
    next_name = _normalize_optional_text(body.name) if body.name is not None else None
    next_business_name = (
        _normalize_optional_text(body.business_name)
        if body.business_name is not None
        else None
    )

    if body.business_name is not None and not next_business_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="business_name cannot be empty",
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
                        detail="User is not associated with a tenant",
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
            FROM user_profiles up
            LEFT JOIN tenants t ON t.id = up.tenant_id
            WHERE up.id = $1
            """,
            current_user.id,
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
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


@router.post("/logout")
async def logout(
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Logout the current user.

    JWT is stateless — the client should discard the token.
    For token revocation, implement a Redis blocklist.
    """
    return {"detail": "Logged out"}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Change the current user's password."""
    old_password = body.old_password
    new_password = body.new_password
    if not old_password or not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="old_password and new_password are required",
        )
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new_password must be at least 8 characters",
        )
    if old_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new_password must be different from old_password",
        )

    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM user_profiles WHERE id = $1",
            current_user.id,
        )
        if not row or not _verify_password(old_password, row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect",
            )
        new_hash = _hash_password(new_password)
        await conn.execute(
            "UPDATE user_profiles SET password_hash = $1 WHERE id = $2",
            new_hash,
            current_user.id,
        )
    return {"detail": "Password changed successfully"}
