"""Current-principal account authority, distinct from subscription billing."""
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.core.security import principal


class PrincipalPort:
    def __init__(self, *, role="user", account_status="active", subscription="active",
                 parent_status=None, active_holds=None, lookup_error=None):
        self.user, self.tenant, self.parent = uuid4(), uuid4(), uuid4()
        self.role = role
        self.bypass = "off"
        self.lookups = 0
        self.lookup_error = lookup_error
        self.account = {
            "id": self.tenant, "status": account_status, "subscription_status": subscription,
            "suspended_at": None, "suspended_by": None, "suspension_reason": None,
            "white_label_partner_id": self.parent if parent_status else None,
            "formal_partner_id": self.parent if parent_status else None,
            "formal_partner_status": parent_status,
            "active_holds": active_holds or [],
        }

    async def fetchrow(self, query, *_args):
        if "FROM user_profiles up" in query:
            return {"id": self.user, "email": "synthetic@example.invalid", "name": "Synthetic",
                    "profile_role": self.role, "tenant_id": self.tenant,
                    "is_active": True, "is_verified": True,
                    "business_name": "Synthetic", "minutes_allocated": 0, "minutes_used": 0,
                    "membership_status": "active", "membership_role": "user"}
        self.lookups += 1
        if self.lookup_error:
            raise self.lookup_error
        return {**self.account, "authority_scope_ok": self.bypass == "on"}

    async def fetchval(self, *_args):
        return self.bypass

    async def execute(self, query, *args):
        if "set_config" in query:
            self.bypass = args[0]

    @asynccontextmanager
    async def transaction(self):
        previous = self.bypass
        try:
            yield
        except BaseException:
            self.bypass = previous
            raise


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", [
    {"parent_status": "suspended"},
    {"account_status": "suspended"},
    {"active_holds": [{"target_type": "partner", "immediate": True}]},
])
async def test_active_membership_does_not_override_current_formal_account_authority(restriction):
    conn = PrincipalPort(**restriction)
    with pytest.raises(principal.PrincipalUnavailable, match="account_restricted"):
        await principal.load_current_principal(conn, conn.user)
    assert conn.lookups == 1
    assert conn.bypass == "off"


@pytest.mark.asyncio
async def test_unavailable_formal_authority_is_not_silently_treated_as_active_membership():
    failure = RuntimeError("Synthetic authority storage unavailable")
    conn = PrincipalPort(lookup_error=failure)
    with pytest.raises(RuntimeError) as rejected:
        await principal.load_current_principal(conn, conn.user)
    assert rejected.value is failure
    assert conn.bypass == "off"


@pytest.mark.asyncio
async def test_subscription_suspension_does_not_remove_login_for_billing_resolution():
    conn = PrincipalPort(subscription="suspended")
    result = await principal.load_current_principal(conn, conn.user)
    assert result["id"] == str(conn.user)
    assert conn.bypass == "off"


@pytest.mark.asyncio
async def test_platform_admin_keeps_global_access_when_its_home_tenant_is_held():
    conn = PrincipalPort(role="platform_admin", parent_status="suspended")
    result = await principal.load_current_principal(conn, conn.user)
    assert result["role"] == "platform_admin"
    assert conn.lookups == 0
    assert conn.bypass == "off"
