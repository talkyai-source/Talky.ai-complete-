"""Session *management* endpoints — inspect / revoke sessions across devices.

Mount: included directly in `app/api/v1/routes.py` with its own `/sessions`
prefix, so the routes are exposed as `/api/v1/sessions/*`.

Scope:
  GET    /sessions/active            → list all active sessions for the user
  DELETE /sessions/{session_id}      → revoke ONE specific session (not current)
  POST   /sessions/verify            → "yes, that was me" on a suspicious session
  GET    /sessions/security-status   → binding + suspicious flags for current session

This file does NOT handle current-session teardown — that belongs to its
sibling, `app/api/v1/endpoints/auth/sessions.py`, which exposes
`/auth/logout` and `/auth/logout-all` (lifecycle).

The boundary in one line:
  - there (`auth/sessions.py`)        → "log me out"          → /auth/*
  - here  (`endpoints/sessions.py`)   → "manage my devices"   → /sessions/*

OWASP Session Management Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.device_fingerprint import generate_device_fingerprint
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    get_active_sessions_detailed,
    get_session_security_status,
    hash_session_token,
    revoke_session_by_id,
    verify_suspicious_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# =============================================================================
# Request/Response Models
# =============================================================================


class SessionInfoResponse(BaseModel):
    """Session information for the session list endpoint."""

    id: str
    device_name: Optional[str] = None
    device_type: Optional[str] = None
    browser: Optional[str] = None
    os: Optional[str] = None
    ip_address: Optional[str] = None
    is_current: bool = False
    is_suspicious: bool = False
    suspicious_reason: Optional[str] = None
    requires_verification: bool = False
    created_at: str
    last_active_at: str
    expires_at: str


class SessionListResponse(BaseModel):
    """Response for listing active sessions."""

    sessions: list[SessionInfoResponse]
    total_count: int
    current_session_id: Optional[str] = None


class SessionRevokeResponse(BaseModel):
    """Response for revoking a specific session.

    `revoked` refers to the `security_sessions` row ONLY. It does not
    mean the device lost access — see the DELETE handler's docstring for
    why the device's refresh token cannot be revoked with the current
    schema. `refresh_token_revoked` is reported explicitly so callers
    cannot mistake a session revoke for a full sign-out.
    """

    detail: str
    session_id: str
    revoked: bool
    refresh_token_revoked: bool = False


class SessionVerificationRequest(BaseModel):
    """Request to verify a suspicious session."""

    session_id: str = Field(..., description="ID of the suspicious session to verify")
    confirm_ownership: bool = Field(
        True, description="User confirms this session belongs to them"
    )


class SessionVerificationResponse(BaseModel):
    """Response for session verification."""

    detail: str
    session_id: str
    verified: bool


class SessionSecurityStatus(BaseModel):
    """Security status of the current session."""

    is_bound: bool
    ip_binding: bool
    fingerprint_binding: bool
    has_fingerprint: bool
    is_suspicious: bool
    suspicious_reason: Optional[str] = None
    requires_verification: bool
    recommendations: list[str]


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/active", response_model=SessionListResponse)
async def list_active_sessions(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> SessionListResponse:
    """
    List all active sessions for the current user.

    Returns detailed device info for each session including:
    - Device name, type, browser, OS
    - IP address
    - Security status (suspicious flags)
    - Current session indicator

    Use this for a "Manage Active Sessions" UI where users can see
    all devices they're logged in from.
    """
    # Get current session hash to mark as "this device"
    current_token_hash = hash_session_token(talky_sid) if talky_sid else None

    async with db_client.pool.acquire() as conn:
        sessions = await get_active_sessions_detailed(
            conn,
            user_id=current_user.id,
            current_session_token_hash=current_token_hash,
        )

    # Find current session ID
    current_session_id = None
    for session in sessions:
        if session.get("is_current"):
            current_session_id = str(session["id"])
            break

    # Convert to response model
    session_responses = []
    for session in sessions:
        session_responses.append(
            SessionInfoResponse(
                id=str(session["id"]),
                device_name=session.get("device_name"),
                device_type=session.get("device_type"),
                browser=session.get("browser"),
                os=session.get("os"),
                ip_address=session.get("ip_address"),
                is_current=bool(session.get("is_current")),
                is_suspicious=bool(session.get("is_suspicious")),
                suspicious_reason=session.get("suspicious_reason"),
                requires_verification=bool(session.get("requires_verification")),
                created_at=session["created_at"].isoformat(),
                last_active_at=session["last_active_at"].isoformat(),
                expires_at=session["expires_at"].isoformat(),
            )
        )

    return SessionListResponse(
        sessions=session_responses,
        total_count=len(session_responses),
        current_session_id=current_session_id,
    )


@router.delete("/{session_id}", response_model=SessionRevokeResponse)
async def revoke_specific_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> SessionRevokeResponse:
    """
    Revoke a specific session by ID (selective logout).

    Use case: "Log out from my phone" while keeping desktop session active.

    Security:
    - Can only revoke own sessions
    - Cannot revoke current session (use /auth/logout instead)

    Returns 404 if session not found or doesn't belong to user.

    !! KNOWN LIMITATION — this does NOT fully log the device out. !!

    This endpoint revokes the target row in `security_sessions` and
    nothing else. It cannot revoke that device's refresh token, because
    the two stores have no column linking them:

      - `refresh_tokens` (Alembic 0002_add_refresh_tokens.py;
        database/schema/baseline_2026-06-02.sql:1172-1188) holds
        id / family_id / user_id / tenant_id / token_hash / parent_id /
        ip / user_agent — no session_id, no security_sessions FK.
      - `security_sessions` (baseline:1272-1305) holds no family_id and
        no refresh-token reference either.

    The only overlapping columns are (user_id, ip, user_agent), which is
    a heuristic and NOT an identity — two browsers on one machine share
    both, and a roaming phone changes ip. Guessing a family from them
    would revoke the WRONG device, so we deliberately do not try.

    Consequence, stated plainly: the revoked device keeps full access.
    `get_current_user` does not consult `security_sessions` on the
    `talky_at` cookie path (app/api/v1/dependencies.py:206-224), and
    POST /auth/refresh consults only `refresh_tokens`
    (app/api/v1/endpoints/auth/refresh.py) — so the device can rotate
    `talky_rt` indefinitely on a rolling 7-day window. What this call
    genuinely invalidates is the legacy `talky_sid` server-side session.

    Fixing this needs a schema change, not code here: add
    `session_id UUID REFERENCES security_sessions(id) ON DELETE SET NULL`
    to `refresh_tokens`, stamp it in `issue_initial_refresh_token`
    (the caller, `auth/_shared.py:issue_cookie_auth`, already HAS the
    session_id and currently discards the family_id), carry it onto
    successors in `rotate_refresh_token`, then revoke by session_id here.
    Until then this response must not claim more than it does.

    Do NOT "fix" this by calling revoke_all_user_refresh_tokens() — that
    would sign the user out of EVERY device when they asked to sign out
    of one, which is a worse bug than this one.
    """
    # Prevent revoking current session through this endpoint
    async with db_client.pool.acquire() as conn:
        # Check if this is the current session
        if talky_sid:
            current_hash = hash_session_token(talky_sid)
            current_session = await conn.fetchrow(
                "SELECT id FROM security_sessions WHERE session_token_hash = $1",
                current_hash,
            )
            if current_session and str(current_session["id"]) == session_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot revoke current session. Use /auth/logout instead.",
                )

        # Revoke the session row only. See the docstring: there is no
        # column correlating this session to a refresh-token family, so
        # the device's `talky_rt` survives and can still mint access
        # JWTs. Revoking ALL families here would be wrong — the user
        # asked to sign out ONE device.
        revoked = await revoke_session_by_id(
            conn,
            session_id=session_id,
            user_id=current_user.id,
            reason="user_initiated",
        )

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or already revoked.",
        )

    logger.info(
        "User revoked specific session: user=%s session=%s "
        "(refresh token NOT revoked — no session/family correlation in schema)",
        current_user.id,
        session_id,
    )

    return SessionRevokeResponse(
        detail=(
            "Session revoked. Note: this device's refresh token could not "
            "be revoked, so it may retain access until the token expires. "
            "To fully sign out everywhere, use /auth/logout-all or change "
            "your password."
        ),
        session_id=session_id,
        revoked=True,
        refresh_token_revoked=False,
    )


@router.post("/verify", response_model=SessionVerificationResponse)
async def verify_session(
    body: SessionVerificationRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> SessionVerificationResponse:
    """
    Verify ownership of a suspicious session.

    Called when user confirms "Yes, this was me" after security alert.
    Clears suspicious flag and updates device fingerprint.

    Requires user to be authenticated (proves they can access the account).
    """
    if not body.confirm_ownership:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ownership confirmation required.",
        )

    # Generate new fingerprint from current request
    new_fingerprint = generate_device_fingerprint(request)

    async with db_client.pool.acquire() as conn:
        verified = await verify_suspicious_session(
            conn,
            session_id=body.session_id,
            user_id=current_user.id,
            new_fingerprint=new_fingerprint,
        )

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or already revoked.",
        )

    logger.info(
        "User verified suspicious session: user=%s session=%s",
        current_user.id,
        body.session_id,
    )

    return SessionVerificationResponse(
        detail="Session verified successfully. Suspicious flags cleared.",
        session_id=body.session_id,
        verified=True,
    )


@router.get("/security-status", response_model=SessionSecurityStatus)
async def get_current_session_security_status(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> SessionSecurityStatus:
    """
    Get security status of the current session.

    Returns:
    - Binding status (IP, fingerprint)
    - Suspicious activity flags
    - Security recommendations

    Use this for a security dashboard or to show warnings in the UI.
    """
    if not talky_sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session.",
        )

    token_hash = hash_session_token(talky_sid)

    async with db_client.pool.acquire() as conn:
        status_info = await get_session_security_status(conn, token_hash)

    if not status_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired.",
        )

    return SessionSecurityStatus(**status_info)
