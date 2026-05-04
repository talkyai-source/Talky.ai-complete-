"""POST /auth/change-password — verified password change with session sweep."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
    get_db_client,
)
from app.core.postgres_adapter import Client
from app.core.security.password import (
    PasswordValidationError,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    hash_session_token,
    revoke_all_user_sessions,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger

from ._shared import get_client_ip, get_user_agent
from .schemas import ChangePasswordRequest

router = APIRouter(tags=["auth"])


@router.post("/change-password")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, str]:
    """
    Change the current user's password.

    Security controls:
      1. Verify current password before allowing change.
      2. Validate new password strength (NIST SP 800-63B).
      3. New password must differ from old password.
      4. Hash new password with Argon2id.
      5. Record password_changed_at timestamp.
      6. Revoke ALL other sessions (OWASP: invalidate sessions on password change).
         The current session (if any) is preserved so the user stays logged in.
    """
    old_password = body.old_password.strip() if body.old_password else ""
    new_password = body.new_password.strip() if body.new_password else ""

    if not old_password or not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both old_password and new_password are required.",
        )

    if old_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current password.",
        )

    # --- new password strength check ------------------------------------------
    try:
        validate_password_strength(new_password)
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM user_profiles WHERE id = $1",
            current_user.id,
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )

        if not verify_password(old_password, row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect.",
            )

        new_hash = hash_password(new_password)

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE user_profiles
                   SET password_hash       = $1,
                       password_changed_at = NOW()
                 WHERE id = $2
                """,
                new_hash,
                current_user.id,
            )

            # --- revoke all OTHER sessions (keep current session alive) --------
            # OWASP: "Invalidate all existing sessions on password change."
            current_token_hash = hash_session_token(talky_sid) if talky_sid else None

            await revoke_all_user_sessions(
                conn,
                current_user.id,
                reason="password_change",
                exclude_token_hash=current_token_hash,
            )

    # --- log password change event (Day 8) -------------------------------------
    await audit_logger.log(
        event_type=AuditEvent.USER_UPDATED,
        actor_id=current_user.id,
        actor_type="user",
        tenant_id=current_user.tenant_id,
        action="password_changed",
        description="User changed their password",
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

    return {
        "detail": "Password changed successfully. All other sessions have been revoked."
    }
