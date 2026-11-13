"""QA F1-15 review: the Team screen's limits hold on the server.

`is_primary` marks a member's home workspace (one per person, see
idx_tu_user_primary), not a workspace owner, so nearly every member has it.
Only the platform team changes it. Nobody changes or removes their own
membership, through any of the three routes, and no change leaves a
workspace without an active administrator.
"""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.rbac import tenant_users as members
from app.api.v1.endpoints.rbac.schemas import AddTenantUserRequest, UpdateTenantUserRequest
from tests.unit.test_privileged_mutation_audit import (
    ACTOR, RESOURCE, TARGET, TENANT, Audit, Store, actor, invoke,
)

OTHER_ADMIN = "99999999-9999-4999-8999-999999999999"


class MembershipStore(Store):
    """One membership row in TENANT; a tenant-admin caller is an active admin there."""

    def __init__(self, *, user_id=TARGET, role="user", is_primary=True, other_admins=()):
        super().__init__("member")
        self.rows[0].update(user_id=user_id, current_role=role, is_primary=is_primary)
        self.other_admins = list(other_admins)
        self.admin_reads = []

    async def fetchrow(self, sql, *args):
        if self.sql(sql).startswith("select r.name from tenant_users tu"):
            assert (str(args[0]), str(args[1])) == (ACTOR, TENANT)
            return {"name": "tenant_admin"}
        return await super().fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        query = self.sql(sql)
        if "from tenant_users" in query:
            self.admin_reads.append((query, args))
            return [{"id": admin} for admin in self.other_admins]
        return await super().fetch(sql, *args)


def tenant_admin():
    return actor(tenant=TENANT, role="tenant_admin")


async def run(operation, store, caller, **fields):
    if operation == "update":
        return await invoke(members.update_tenant_user, store, Audit(store), current_user=caller,
                            tenant_user_id=RESOURCE, request=UpdateTenantUserRequest(**fields))
    if operation == "remove":
        return await invoke(members.remove_user_from_tenant, store, Audit(store), current_user=caller,
                            tenant_user_id=RESOURCE)
    return await invoke(members.add_user_to_tenant, store, Audit(store), current_user=caller,
                        request=AddTenantUserRequest(tenant_id=TENANT, **fields))


async def refused(operation, store, caller, **fields):
    before = deepcopy(store.rows)
    with pytest.raises(HTTPException) as caught:
        await run(operation, store, caller, **fields)
    assert store.rows == before and store.mutations == 0 and not store.receipts
    return caught.value


@pytest.mark.parametrize("operation,fields", [
    ("update", {"is_primary": False}),
    ("update", {"is_primary": True}),
    ("add", {"user_id": TARGET, "role_name": "user", "is_primary": True}),
])
async def test_a_workspace_admin_never_sets_a_members_home_workspace(operation, fields):
    error = await refused(operation, MembershipStore(is_primary=not fields["is_primary"]), tenant_admin(), **fields)
    assert error.status_code == 403
    assert "home workspace" in str(error.detail)


@pytest.mark.parametrize("operation,caller,fields", [
    ("remove", "tenant_admin", {}),
    ("update", "platform_admin", {"is_primary": False}),
    ("add", "tenant_admin", {"user_id": ACTOR, "role_name": "readonly"}),
])
@pytest.mark.parametrize("home", [True, False])
async def test_nobody_changes_or_removes_their_own_membership(operation, caller, fields, home):
    store = MembershipStore(user_id=ACTOR, role="tenant_admin", is_primary=home, other_admins=[OTHER_ADMIN])
    error = await refused(operation, store, actor(tenant=TENANT, role=caller), **fields)
    assert error.status_code == 400
    assert "your own membership" in str(error.detail)


LEAVING_ADMIN = [
    ("update", {"role_name": "readonly"}),
    ("update", {"status": "suspended"}),
    ("remove", {}),
    ("add", {"user_id": TARGET, "role_name": "readonly"}),
]


@pytest.mark.parametrize("operation,fields", LEAVING_ADMIN)
async def test_the_last_active_administrator_is_kept(operation, fields):
    store = MembershipStore(role="tenant_admin")
    error = await refused(operation, store, actor(tenant=TENANT, role="platform_admin"), **fields)
    assert error.status_code == 409
    assert "last active administrator" in str(error.detail)
    [(query, args)] = store.admin_reads
    assert "tu.tenant_id = $1" in query and "tu.id <> $2" in query
    assert "tu.status = 'active'" in query and "for update of tu" in query
    assert (str(args[0]), str(args[1])) == (TENANT, RESOURCE)
    assert set(args[2]) == {"tenant_admin", "partner_admin"}


@pytest.mark.parametrize("operation,fields", LEAVING_ADMIN)
async def test_another_active_administrator_lets_the_change_through(operation, fields):
    store = MembershipStore(role="tenant_admin", other_admins=[OTHER_ADMIN])
    await run(operation, store, actor(tenant=TENANT, role="platform_admin"), **fields)
    assert store.mutations == 1 and len(store.receipts) == 1
    assert len(store.admin_reads) == 1


async def test_changing_a_non_admin_does_not_read_or_lock_the_administrators():
    store = MembershipStore(role="agent")
    await run("update", store, tenant_admin(), role_name="readonly")
    assert store.mutations == 1 and len(store.receipts) == 1
    assert store.admin_reads == []
