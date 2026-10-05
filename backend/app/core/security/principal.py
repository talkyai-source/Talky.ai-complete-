"""Current account and membership proof shared by existing auth flows.

No session is created here and no account is repaired or promoted implicitly.
"""

from fastapi import HTTPException
from contextvars import ContextVar
from uuid import UUID


class PrincipalUnavailable(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def load_current_principal(conn, user_id, *, tenant_id=None):
    row = await conn.fetchrow(
        """SELECT up.id,up.email,up.name,up.role AS profile_role,up.tenant_id,
                  up.is_active,up.is_verified,t.business_name,t.minutes_allocated,t.minutes_used,
                  tu.status AS membership_status,r.name AS membership_role
             FROM user_profiles up
             LEFT JOIN tenants t ON t.id=up.tenant_id
             LEFT JOIN tenant_users tu ON tu.user_id=up.id AND tu.tenant_id=up.tenant_id
             LEFT JOIN roles r ON r.id=tu.role_id
            WHERE up.id=$1::uuid""",
        str(user_id),
    )
    if not row or row["is_active"] is not True:
        raise PrincipalUnavailable("account_unavailable")
    if row["is_verified"] is not True:
        raise PrincipalUnavailable("email_verification_required")
    result = dict(row)
    actual_tenant = str(row["tenant_id"]) if row["tenant_id"] else None
    if tenant_id is not None and str(tenant_id) != actual_tenant:
        raise PrincipalUnavailable("tenant_context_changed")
    if row["profile_role"] == "platform_admin":
        effective_role = "platform_admin"
    else:
        if not actual_tenant or row["membership_status"] != "active":
            raise PrincipalUnavailable("membership_required")
        effective_role = row["membership_role"]
        # A tenant membership cannot create a global platform administrator.
        from app.core.security.rbac import UserRole

        if (
            effective_role not in {role.value for role in UserRole}
            or effective_role == "platform_admin"
        ):
            raise PrincipalUnavailable("membership_role_unavailable")
    result.update(id=str(row["id"]), tenant_id=actual_tenant, role=effective_role)
    return result


def assert_expected_identity(request, principal):
    """Optional browser consistency proof, never authentication or selection."""
    expected_user = request.headers.get("X-Talky-Expected-User")
    expected_tenant = request.headers.get("X-Talky-Expected-Tenant")
    actual_user = str(principal.get("id") or principal.get("user_id") or "")
    actual_tenant = str(principal.get("tenant_id") or "")
    if (expected_user is not None and expected_user != actual_user) or (
        expected_tenant is not None and expected_tenant != actual_tenant
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "identity_changed",
                "message": "Your signed-in account changed. Refresh this page before continuing.",
            },
        )


async def load_session_principal(conn, claims):
    """Current session and account proof for a fixed, verified JWT identity."""
    from app.core.security.sessions import get_session_by_id

    try:
        user_id = str(UUID(str(claims.get("sub"))))
        session_id = str(UUID(str(claims.get("sid"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PrincipalUnavailable("session_required") from exc
    if not await get_session_by_id(conn, session_id, user_id=user_id):
        raise PrincipalUnavailable("session_ended")
    return await load_current_principal(conn, user_id, tenant_id=claims.get("tenant_id"))


# Server-only context propagated to existing assistant tool tasks. No model or
# proposal argument can select it; socket owners reset it on exit.
assistant_session_context = ContextVar("assistant_session_context", default=None)


async def check_assistant_session(pool, user_id, tenant_id):
    claims = assistant_session_context.get()
    if claims is None:
        # Non-WebSocket internal callers retain their explicit permission gate.
        return
    if str(claims.get("sub")) != str(user_id) or str(claims.get("tenant_id")) != str(tenant_id):
        raise PrincipalUnavailable("session_context_changed")
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(pool, None) as conn:
        await load_session_principal(conn, claims)
