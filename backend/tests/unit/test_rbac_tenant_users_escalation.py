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
from app.domain.services.audit_logger import AuditEvent


def _tenant_admin_user(
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    user_id: str = "caller-1",
) -> CurrentUser:
    return CurrentUser(id=user_id, email="admin@example.com", tenant_id=tenant_id, role="tenant_admin")


class _FakeConnCtx:
    """Mimics `async with db_client.pool.acquire() as conn:`."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        assert self.conn.fixture_transaction_depth == 0
        self.conn.fixture_transaction_depth = 1
        self.writes_before = list(self.conn.fixture_writes)
        self.audit_before = list(self.conn.fixture_audit.events)
        return self

    async def __aexit__(self, exc_type, *_exc):
        assert self.conn.fixture_transaction_depth == 1
        if exc_type is not None:
            self.conn.fixture_writes[:] = self.writes_before
            self.conn.fixture_audit.events[:] = self.audit_before
        self.conn.fixture_transaction_depth = 0
        self.conn.fixture_tenant_id = None
        return False


class _MembershipAudit:
    def __init__(self, conn):
        self.conn = conn
        self.lock_calls = 0
        self.events = []

    async def lock_transaction(self, conn):
        assert conn is self.conn and conn.fixture_transaction_depth == 1
        assert conn.fixture_tenant_id == _tenant_admin_user().tenant_id
        self.lock_calls += 1

    async def log_in_transaction(self, conn, **event):
        assert conn is self.conn and conn.fixture_transaction_depth == 1
        assert self.lock_calls == 1 and len(conn.fixture_writes) == 1
        assert event["tenant_id"] == conn.fixture_tenant_id
        assert event["actor_id"] == _tenant_admin_user().id
        assert event["actor_role"] == "tenant_admin"
        self.events.append(event)


def _fake_db_client(conn) -> MagicMock:
    conn.fixture_transaction_depth = 0
    conn.fixture_tenant_id = None
    conn.fixture_writes = []
    conn.fixture_audit = _MembershipAudit(conn)
    original = conn.fetchrow

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        assert conn.fixture_transaction_depth == 1
        if flat.startswith("SELECT r.name FROM tenant_users tu"):
            assert "tu.tenant_id = $2" in flat and "tu.status = 'active'" in flat
            assert args == (_tenant_admin_user().id, _tenant_admin_user().tenant_id)
            return {"name": "tenant_admin"}
        if flat.startswith("SELECT tu.*, r.name AS current_role FROM tenant_users tu"):
            # This add-path before-state lookup must not consume the later
            # user/tenant/role/INSERT response sequence.
            assert args == ("target-user", _tenant_admin_user().tenant_id)
            assert "tu.tenant_id=$2" in flat and "FOR UPDATE OF tu" in flat
            return None
        if flat.lstrip().startswith(("INSERT INTO tenant_users", "UPDATE tenant_users")):
            assert conn.fixture_audit.lock_calls == 1
            if flat.startswith("INSERT"):
                assert "tenant_id" in flat and args[1] == conn.fixture_tenant_id
            else:
                assert "AND tenant_id =" in flat and args[-1] == conn.fixture_tenant_id
            conn.fixture_writes.append((flat, args))
        row = await original(sql, *args)
        if isinstance(row, dict) and "tenant_id" in row:
            return {"current_role": "user", **row}
        return row

    async def execute(sql, *args):
        assert conn.fixture_transaction_depth == 1
        flat = " ".join(sql.split())
        assert flat.startswith(("SET LOCAL ", "SELECT set_config("))
        if flat.startswith("SET LOCAL app.current_tenant_id"):
            tenant = _tenant_admin_user().tenant_id
            assert tenant in flat
            conn.fixture_tenant_id = tenant
        return "SELECT 1"

    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    conn.execute = AsyncMock(side_effect=execute)
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(side_effect=lambda: _FakeTransaction(conn))
    db_client = MagicMock()
    db_client.pool.acquire = MagicMock(return_value=_FakeConnCtx(conn))
    return db_client


def _assert_membership_audit(conn, caller, *, action, before, role_id):
    assert conn.fixture_transaction_depth == 0 and conn.fixture_tenant_id is None
    assert len(conn.fixture_writes) == 1 and conn.fixture_audit.lock_calls == 1
    assert conn.fixture_audit.events == [{
        "event_type": AuditEvent.USER_UPDATED,
        "action": action,
        "actor_id": caller.id,
        "actor_role": caller.role,
        "tenant_id": caller.tenant_id,
        "resource_type": "tenant_user",
        "resource_id": "tu-1",
        "before_state": before,
        "after_state": {"id": "tu-1", "user_id": "target-user", "tenant_id": caller.tenant_id,
                        "role_id": role_id, "is_primary": False, "status": "active"},
    }]


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
        await add_user_to_tenant(
            request=request, current_user=caller, db_client=db_client, audit_logger=conn.fixture_audit,
        )

    assert exc_info.value.status_code == 403
    # Must fail *before* touching the DB.
    conn.fetchrow.assert_not_called()
    assert conn.fixture_audit.lock_calls == 0 and conn.fixture_audit.events == []
    assert conn.fixture_writes == []


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

    result = await add_user_to_tenant(
        request=request, current_user=caller, db_client=db_client, audit_logger=conn.fixture_audit,
    )

    assert result.role.name == target_role
    _assert_membership_audit(
        conn, caller, action="rbac.member.add", before=None, role_id="role-id-1",
    )


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
    audit_logger = conn.fixture_audit

    with pytest.raises(HTTPException) as exc_info:
        await update_tenant_user(
            tenant_user_id="tu-1",
            request=request,
            current_user=caller,
            db_client=db_client,
            audit_logger=audit_logger,
        )

    assert exc_info.value.status_code == 403
    assert audit_logger.events == [] and conn.fixture_writes == []
    assert audit_logger.lock_calls == 1 and conn.fixture_transaction_depth == 0


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
    audit_logger = conn.fixture_audit

    result = await update_tenant_user(
        tenant_user_id="tu-1",
        request=request,
        current_user=caller,
        db_client=db_client,
        audit_logger=audit_logger,
    )

    assert result.role.name == "tenant_admin"
    _assert_membership_audit(
        conn, caller, action="rbac.member.update", role_id="role-id-tenant-admin",
        before={"id": "tu-1", "user_id": "target-user", "tenant_id": caller.tenant_id,
                "role_id": "old-role-id", "is_primary": False, "status": "active"},
    )


# QA F1-15: the Team screen lets administrators change roles. Nobody changes
# their own role or status there: a slip would leave the workspace without an
# administrator, so another administrator does it.
@pytest.mark.parametrize("fields", [{"role_name": "readonly"}, {"status": "suspended"}])
async def test_patch_refuses_changing_your_own_role_or_status(fields):
    caller = _tenant_admin_user()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": "tu-1",
        "user_id": caller.id,
        "tenant_id": caller.tenant_id,
        "role_id": "old-role-id",
        "is_primary": False,
        "status": "active",
    }
    db_client = _fake_db_client(conn)
    with pytest.raises(HTTPException) as exc_info:
        await update_tenant_user(
            tenant_user_id="tu-1",
            request=UpdateTenantUserRequest(**fields),
            current_user=caller,
            db_client=db_client,
            audit_logger=conn.fixture_audit,
        )
    assert exc_info.value.status_code == 400
    assert "your own role or status" in str(exc_info.value.detail)
    assert conn.fixture_audit.events == [] and conn.fixture_writes == []
