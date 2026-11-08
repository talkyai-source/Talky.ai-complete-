"""D4-3: tenant users never receive platform-only roles or permissions.

GET /rbac/roles returned every role (platform_admin, partner_admin) to any
caller, and GET /rbac/permissions every permission (platform:*). A tenant
caller now sees only tenant-scoped roles at or below its own level and no
platform permission; a platform admin still sees everything.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.rbac import permissions as permissions_ep
from app.api.v1.endpoints.rbac import roles as roles_ep

# The seeded rows (database/complete_schema.sql), including the trap that
# partner_admin is seeded tenant_scoped=TRUE.
ROLES = [
    {"id": f"00000000-0000-4000-8000-00000000000{i}", "name": name, "description": name,
     "level": level, "is_system_role": True, "tenant_scoped": scoped}
    for i, (name, level, scoped) in enumerate([
        ("platform_admin", 100, False), ("partner_admin", 80, True), ("tenant_admin", 60, True),
        ("campaign_manager", 50, True), ("user", 40, True), ("agent", 35, True),
        ("billing_user", 30, True), ("readonly", 20, True)])
]
PERMISSIONS = [
    {"id": "p1", "name": "campaigns:read", "description": "", "resource": "campaigns", "action": "read"},
    {"id": "p2", "name": "platform:admin", "description": "", "resource": "platform", "action": "admin"},
    {"id": "p3", "name": "platform:tenants:manage", "description": "", "resource": "platform", "action": "tenants:manage"},
    {"id": "p4", "name": "billing:read", "description": "", "resource": "billing", "action": "read"},
]


class Conn:
    async def fetch(self, query, *args):
        if "FROM roles" in query:
            return list(ROLES)
        if "FROM role_permissions" in query:
            return list(PERMISSIONS)
        if "FROM permissions" in query:
            return [p for p in PERMISSIONS if not args or p["resource"] == args[0]]
        raise AssertionError(query)

    async def fetchrow(self, query, *args):
        assert "FROM roles" in query
        return next((r for r in ROLES if r["id"] == args[0]), None)


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    @asynccontextmanager
    async def acquire(_pool):
        yield Conn()

    monkeypatch.setattr(roles_ep, "acquire_connection", acquire)
    monkeypatch.setattr(permissions_ep, "acquire_connection", acquire)


DB = SimpleNamespace(pool=object())


def caller(role):
    return SimpleNamespace(id="u", tenant_id="t", role=role)


def role_id(name):
    return next(r["id"] for r in ROLES if r["name"] == name)


@pytest.mark.parametrize("role,expected", [
    ("tenant_admin", ["tenant_admin", "campaign_manager", "user", "agent", "billing_user", "readonly"]),
    ("owner", ["tenant_admin", "campaign_manager", "user", "agent", "billing_user", "readonly"]),
    ("user", ["user", "agent", "billing_user", "readonly"]),
    ("readonly", ["readonly"]),
    ("partner_admin", ["partner_admin", "tenant_admin", "campaign_manager", "user", "agent", "billing_user", "readonly"]),
    ("platform_admin", [r["name"] for r in ROLES]),
])
async def test_role_list_is_bounded_by_the_callers_level(role, expected):
    listed = await roles_ep.list_roles(current_user=caller(role), db_client=DB)
    assert [r.name for r in listed] == expected


@pytest.mark.parametrize("hidden", ["platform_admin", "partner_admin"])
async def test_a_tenant_admin_cannot_read_a_hidden_role_or_its_permissions(hidden):
    with pytest.raises(HTTPException) as one:
        await roles_ep.get_role(role_id=role_id(hidden), current_user=caller("tenant_admin"), db_client=DB)
    with pytest.raises(HTTPException) as perms:
        await roles_ep.get_role_permissions(role_id=role_id(hidden), current_user=caller("tenant_admin"), db_client=DB)
    assert one.value.status_code == perms.value.status_code == 404


async def test_a_visible_roles_permissions_never_include_platform_permissions():
    tenant = await roles_ep.get_role_permissions(role_id=role_id("readonly"), current_user=caller("tenant_admin"), db_client=DB)
    platform = await roles_ep.get_role_permissions(role_id=role_id("readonly"), current_user=caller("platform_admin"), db_client=DB)
    assert [p.name for p in tenant.permissions] == ["campaigns:read", "billing:read"]
    assert len(platform.permissions) == len(PERMISSIONS)


@pytest.mark.parametrize("resource", [None, "platform"])
async def test_the_permission_catalogue_hides_platform_permissions_from_tenants(resource):
    tenant = await permissions_ep.list_permissions(current_user=caller("tenant_admin"), db_client=DB, resource=resource)
    platform = await permissions_ep.list_permissions(current_user=caller("platform_admin"), db_client=DB, resource=resource)
    assert not any(p.name.startswith("platform:") for p in tenant)
    assert any(p.name.startswith("platform:") for p in platform)
