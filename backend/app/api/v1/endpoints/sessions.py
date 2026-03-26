"""
Session Management Endpoints (Day 5)

Allows users to manage their active sessions across devices.

OWASP Session Management Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

Features:
- List all active sessions with device info
- Revoke specific sessions (selective logout)
- Verify suspicious sessions
- View session security status
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
    """Response for revoking a specific session."""

    detail: str
    session_id: str
    revoked: bool


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

        # Revoke the session
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
        "User revoked specific session: user=%s session=%s",
        current_user.id,
        session_id,
    )

    return SessionRevokeResponse(
        detail="Session revoked successfully.",
        session_id=session_id,
        revoked=True,
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
