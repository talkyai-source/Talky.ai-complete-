"""Regression test for P0-4: role-assignment escalation ceiling.

Verifies that POST /rbac/tenant-users and PATCH /rbac/tenant-users/{id}
reject requests where the *requested* role's level exceeds the *caller's*
own role level, closing the hole where a tenant_admin (60) could grant
partner_admin (80) or platform_admin (100) to a user.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.rbac.schemas import AddTenantUserRequest, UpdateTenantUserRequest
from app.api.v1.endpoints.rbac.tenant_users import add_user_to_tenant, update_tenant_user


def _tenant_admin_user(tenant_id: str = "tenant-1", user_id: str = "caller-1") -> CurrentUser:
    return CurrentUser(id=user_id, email="admin@example.com", tenant_id=tenant_id, role="tenant_admin")


class _FakeConnCtx:
    """Mimics `async with db_client.pool.acquire() as conn:`."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _fake_db_client(conn) -> MagicMock:
    db_client = MagicMock()
    db_client.pool.acquire = MagicMock(return_value=_FakeConnCtx(conn))
    return db_client


@pytest.mark.parametrize("target_role", ["platform_admin", "partner_admin"])
async def test_post_tenant_admin_cannot_grant_role_above_own_tier(target_role):
    """tenant_admin (60) granting platform_admin (100) / partner_admin (80) -> 403."""
    caller = _tenant_admin_user()
    request = AddTenantUserRequest(
        user_id="target-user",
        tenant_id=caller.tenant_id,
        role_name=target_role,
    )
    conn = AsyncMock()
    db_client = _fake_db_client(conn)

    with pytest.raises(HTTPException) as exc_info:
        await add_user_to_tenant(request=request, current_user=caller, db_client=db_client)

    assert exc_info.value.status_code == 403
    # Must fail *before* touching the DB.
    conn.fetchrow.assert_not_called()


@pytest.mark.parametrize("target_role", ["user", "readonly"])
async def test_post_tenant_admin_can_grant_role_at_or_below_own_tier(target_role):
    """tenant_admin (60) granting user (40) / readonly (20) -> allowed."""
    caller = _tenant_admin_user()
    request = AddTenantUserRequest(
        user_id="target-user",
        tenant_id=caller.tenant_id,
        role_name=target_role,
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"id": "target-user"},  # user exists
        {"id": caller.tenant_id},  # tenant exists
        {"id": "role-id-1"},  # role lookup
        {  # insert result
            "id": "tu-1",
            "user_id": "target-user",
            "tenant_id": caller.tenant_id,
            "role_id": "role-id-1",
            "is_primary": False,
            "status": "active",
            "invited_at": None,
            "joined_at": None,
        },
        {  # role details
            "id": "role-id-1",
            "name": target_role,
            "description": None,
            "level": 40 if target_role == "user" else 20,
            "is_system_role": True,
            "tenant_scoped": True,
        },
    ]
    db_client = _fake_db_client(conn)

    result = await add_user_to_tenant(request=request, current_user=caller, db_client=db_client)

    assert result.role.name == target_role


async def test_patch_tenant_admin_cannot_grant_role_above_own_tier():
    """PATCH: tenant_admin (60) escalating an existing member to platform_admin (100) -> 403."""
    caller = _tenant_admin_user()
    request = UpdateTenantUserRequest(role_name="platform_admin")
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": "tu-1",
        "user_id": "target-user",
        "tenant_id": caller.tenant_id,
        "role_id": "old-role-id",
        "is_primary": False,
        "status": "active",
    }
    db_client = _fake_db_client(conn)
    audit_logger = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await update_tenant_user(
            tenant_user_id="tu-1",
            request=request,
            current_user=caller,
            db_client=db_client,
            audit_logger=audit_logger,
        )

    assert exc_info.value.status_code == 403
    audit_logger.log.assert_not_called()


async def test_patch_tenant_admin_can_grant_role_at_own_tier():
    """PATCH: tenant_admin (60) assigning tenant_admin (60, same tier) -> allowed."""
    caller = _tenant_admin_user()
    request = UpdateTenantUserRequest(role_name="tenant_admin")
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {  # current record
            "id": "tu-1",
            "user_id": "target-user",
            "tenant_id": caller.tenant_id,
            "role_id": "old-role-id",
            "is_primary": False,
            "status": "active",
        },
        {"id": "role-id-tenant-admin"},  # role lookup
        {  # update result
            "id": "tu-1",
            "user_id": "target-user",
            "tenant_id": caller.tenant_id,
            "role_id": "role-id-tenant-admin",
            "is_primary": False,
            "status": "active",
            "invited_at": None,
            "joined_at": None,
        },
        {  # role details
            "id": "role-id-tenant-admin",
            "name": "tenant_admin",
            "description": None,
            "level": 60,
            "is_system_role": True,
            "tenant_scoped": True,
        },
    ]
    db_client = _fake_db_client(conn)
    audit_logger = AsyncMock()

    result = await update_tenant_user(
        tenant_user_id="tu-1",
        request=request,
        current_user=caller,
        db_client=db_client,
        audit_logger=audit_logger,
    )

    assert result.role.name == "tenant_admin"
    audit_logger.log.assert_called_once()
