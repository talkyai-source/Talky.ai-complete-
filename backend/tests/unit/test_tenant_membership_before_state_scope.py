"""Membership before-state reads must not lock another tenant's row."""
from copy import deepcopy

from fastapi import HTTPException
import pytest

from app.api.v1.endpoints.rbac import tenant_users as members
from app.api.v1.endpoints.rbac.schemas import UpdateTenantUserRequest
from tests.unit.test_privileged_mutation_audit import (
    ACTOR, OTHER, RESOURCE, TENANT, Audit, Store, actor, invoke,
)


class ScopedMembershipStore(Store):
    def __init__(self):
        super().__init__("member")
        self.rows[0]["tenant_id"] = OTHER
        self.target_reads = 0
        self.foreign_row_locked = False

    async def fetchrow(self, sql, *args):
        query = self.sql(sql)
        if "from tenant_users tu" in query and "where tu.id = $1" in query:
            self.target_reads += 1
            assert str(args[0]) == RESOURCE
            scoped = "($2::uuid is null or tu.tenant_id = $2::uuid)" in query
            if scoped:
                assert len(args) == 2
                if args[1] is not None and str(args[1]) != OTHER:
                    return None
            self.foreign_row_locked = True
            return deepcopy(self.rows[0])
        return await super().fetchrow(sql, *args)


@pytest.mark.parametrize("operation", ["update", "remove"])
async def test_tenant_admin_never_fetches_or_locks_foreign_before_state(operation):
    store = ScopedMembershipStore()
    before = deepcopy(store.rows)
    handler = (members.update_tenant_user if operation == "update"
               else members.remove_user_from_tenant)
    kwargs = {"tenant_user_id": RESOURCE,
              "current_user": actor(tenant=TENANT, role="tenant_admin")}
    if operation == "update":
        kwargs["request"] = UpdateTenantUserRequest(status="suspended")
    with pytest.raises(HTTPException) as caught:
        await invoke(handler, store, Audit(store), **kwargs)
    assert caught.value.status_code == 404
    assert store.target_reads == 1
    assert not store.foreign_row_locked, "Foreign membership was fetched under FOR UPDATE"
    assert store.rows == before and not store.receipts and store.mutations == 0


@pytest.mark.parametrize("operation", ["update", "remove"])
async def test_platform_admin_retains_explicit_cross_tenant_targeting(operation):
    store = ScopedMembershipStore()
    handler = (members.update_tenant_user if operation == "update"
               else members.remove_user_from_tenant)
    kwargs = {"tenant_user_id": RESOURCE,
              "current_user": actor(tenant=TENANT, role="platform_admin")}
    if operation == "update":
        kwargs["request"] = UpdateTenantUserRequest(status="suspended")
    await invoke(handler, store, Audit(store), **kwargs)
    assert store.target_reads == 1 and store.foreign_row_locked
    assert len(store.receipts) == 1
    assert str(store.receipts[0]["actor_id"]) == ACTOR
    assert str(store.receipts[0]["tenant_id"]) == OTHER
