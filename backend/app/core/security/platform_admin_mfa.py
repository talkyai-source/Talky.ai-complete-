"""Default-off platform-admin MFA policy over already-verified DB evidence.

Enrollment and token/client claims are not a completed second factor. Callers
must first establish the current principal and an active session for that user.
This module neither authenticates service tokens nor creates/mutates sessions.
"""
from app.core.config import get_settings
from app.core.security.rbac import UserRole, normalize_role


MFA_REQUIRED_CODE = "platform_admin_mfa_required"


def policy_enabled() -> bool:
    return getattr(get_settings(), "platform_admin_mfa_required", False) is True


def requires_verified_mfa(principal, session, *, enabled: bool) -> bool:
    """Pure decision: only strict server-recorded MFA for this user satisfies it."""
    if not enabled or normalize_role(principal.get("role") or "user") != UserRole.PLATFORM_ADMIN:
        return False
    return (
        not session
        or session.get("mfa_verified") is not True
        or str(session.get("user_id")) != str(principal.get("id"))
    )


def denial_detail() -> dict:
    return {
        "code": MFA_REQUIRED_CODE,
        "message": "Platform administrators must sign in again and complete two-factor verification.",
    }
