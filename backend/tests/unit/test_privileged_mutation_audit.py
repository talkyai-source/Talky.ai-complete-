"""Item04: actual privileged handlers commit their mutation and audit together.

The store stages rows and receipts in nested transactions. It is a behavioral
fake, not PostgreSQL evidence. No application connection or provider is used.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import inspect
import json
import re
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
import pytest

from app.api.v1.endpoints import abuse_monitoring as abuse
from app.api.v1.endpoints import tenant_ai_credentials as keys
from app.api.v1.endpoints.rbac import roles, tenant_users as members
from app.api.v1.endpoints.rbac.schemas import AddTenantUserRequest, UpdateTenantUserRequest


TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ACTOR = "33333333-3333-4333-8333-333333333333"
TARGET = "44444444-4444-4444-8444-444444444444"
RESOURCE = "55555555-5555-4555-8555-555555555555"
ROLE = "66666666-6666-4666-8666-666666666666"
PERMISSION = "77777777-7777-4777-8777-777777777777"
NEW_KEY = "88888888-8888-4888-8888-888888888888"
NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
PLAIN = "synthetic-provider-key-sensitive-1234"
CIPHER = "synthetic-encrypted-key-sensitive"


def actor(*, tenant=TENANT, role="platform_admin"):
    return SimpleNamespace(id=ACTOR, tenant_id=tenant, role=role)


class AuditUnavailable(RuntimeError):
    pass


class Store:
    def __init__(self, family, *, existing=True):
        self.family = family
        self.depth = 0
        self.locked = False
        self.order = []
        self.receipts = []
        self.buffered = []
        self.cache_events = []
        self.mutations = 0
        self.role = {"id": ROLE, "name": "user", "description": None,
                     "level": 40, "is_system_role": True, "tenant_scoped": True}
        if family == "member":
            self.rows = [{"id": RESOURCE, "user_id": TARGET, "tenant_id": TENANT,
                          "role_id": ROLE, "is_primary": False, "status": "active",
                          "invited_at": None, "joined_at": None,
                          "current_role": "user"}] if existing else []
        elif family == "permission":
            self.rows = [{"role_id": ROLE, "permission_id": PERMISSION}] if existing else []
        elif family == "rule":
            self.rows = [{"id": RESOURCE, "tenant_id": UUID(TENANT),
                          "rule_name": "before", "rule_type": "velocity_spike",
                          "parameters": {"window": 5}, "warn_threshold": 2,
                          "block_threshold": 4, "action_on_trigger": "flag",
                          "analysis_window_minutes": 60, "priority": 100,
                          "is_active": True, "created_at": NOW, "updated_at": NOW}] if existing else []
        elif family == "event":
            self.rows = [{"id": UUID(RESOURCE), "tenant_id": UUID(TENANT),
                          "tenant_name": "Synthetic", "partner_id": None,
                          "event_type": "velocity_spike", "severity": "high",
                          "trigger_value": 6, "threshold_value": 4,
                          "phone_number_called": None, "call_id": None,
                          "destination_country": None, "action_taken": "flag",
                          "action_details": {}, "resolved_at": None,
                          "resolved_by": None, "resolution_notes": None,
                          "false_positive": False, "created_at": NOW}]
        else:
            self.rows = [{"id": RESOURCE, "tenant_id": TENANT, "provider": "groq",
                          "credential_kind": "api_key", "encrypted_key": "old-" + CIPHER,
                          "last4": "9999", "label": "before", "status": "active",
                          "created_at": NOW, "last_used_at": None}] if existing else []

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy((self.rows, self.receipts, self.mutations))
        previous_lock = self.locked
        self.depth += 1
        try:
            yield self
        except BaseException:
            self.rows, self.receipts, self.mutations = snapshot
            self.order.append("rollback")
            raise
        else:
            self.order.append("commit")
        finally:
            self.depth -= 1
            self.locked = previous_lock

    def changed(self):
        self.order.append("write")
        self.mutations += 1

    @staticmethod
    def sql(sql):
        return " ".join(sql.lower().split())

    async def fetch(self, sql, *args):
        query = self.sql(sql)
        assert query.startswith("select"), query
        if self.family == "key":
            assert "tenant_id" in query and str(args[0]) == TENANT
        return deepcopy(self.rows)

    async def fetchval(self, sql, *args):
        query = self.sql(sql)
        if "from tenant_users" in query:
            return self.rows[0]["current_role"] if self.rows else None
        if "exists" in query or "from role_permissions" in query:
            return bool(self.rows)
        raise AssertionError(query)

    async def fetchrow(self, sql, *args):
        query = self.sql(sql)
        if query.startswith("select"):
            if "from roles" in query and "from role_permissions" not in query:
                if "name =" in query or "name=" in query:
                    self.role["name"] = args[0]
                    self.role["id"] = ROLE if args[0] == "user" else NEW_KEY
                return deepcopy(self.role)
            if "from permissions" in query:
                return {"id": PERMISSION}
            if "from user_profiles" in query or "from tenants " in query:
                return {"id": args[0]}
            if self.family == "key":
                assert "tenant_id" in query and str(args[0]) == TENANT
                candidates = [r for r in self.rows if len(args) < 2 or str(args[1]) in (r["id"], r["provider"])]
                return deepcopy(candidates[0]) if candidates else None
            return deepcopy(self.rows[0]) if self.rows else None
        if query.startswith("insert into tenant_users"):
            self.changed()
            if self.rows:
                self.rows[0].update(role_id=args[2], status="active")
            else:
                self.rows.append({"id": RESOURCE, "user_id": args[0], "tenant_id": args[1],
                                  "role_id": args[2], "is_primary": args[3], "status": "active",
                                  "invited_at": None, "joined_at": None, "current_role": self.role["name"]})
            return deepcopy(self.rows[0])
        if query.startswith("insert into abuse_detection_rules"):
            names = ("tenant_id", "rule_name", "rule_type", "parameters", "warn_threshold",
                     "block_threshold", "action_on_trigger", "analysis_window_minutes", "priority", "is_active")
            self.changed()
            self.rows.append({"id": RESOURCE, "created_at": NOW, "updated_at": NOW, **dict(zip(names, args))})
            return deepcopy(self.rows[-1])
        if query.startswith("insert into tenant_ai_credentials"):
            self.changed()
            self.rows.append({"id": NEW_KEY, "tenant_id": args[0], "provider": args[1],
                              "credential_kind": args[2], "encrypted_key": args[3],
                              "last4": args[4], "label": args[5], "status": "active",
                              "created_at": NOW, "last_used_at": None})
            return deepcopy(self.rows[-1])
        if query.startswith("update") or query.startswith("delete"):
            result = await self.execute(sql, *args)
            return deepcopy(self.rows[0]) if self.rows and result != "UPDATE 0" else None
        raise AssertionError(query)

    async def execute(self, sql, *args):
        query = self.sql(sql)
        if query.startswith(("set local", "select set_config")):
            return "SELECT 1"
        if query.startswith("insert into role_permissions"):
            if not self.rows:
                self.changed()
                self.rows.append({"role_id": args[0], "permission_id": args[1]})
                return "INSERT 0 1"
            return "INSERT 0 0"
        if query.startswith("delete"):
            count = len(self.rows)
            if count:
                self.changed()
                self.rows.clear()
            return f"DELETE {count}"
        if query.startswith("update tenant_ai_credentials"):
            assert "tenant_id" in query and str(args[0]) == TENANT
            targets = [r for r in self.rows if str(args[1]) in (r["id"], r["provider"])
                       and r["status"] == "active"]
            for target in targets:
                self.changed()
                target["status"] = "disabled"
            return f"UPDATE {len(targets)}"
        if query.startswith("update"):
            if not self.rows:
                return "UPDATE 0"
            self.changed()
            row = self.rows[0]
            for column, index in re.findall(r"(\w+)\s*=\s*\$(\d+)", query.split(" where ")[0]):
                row[column] = args[int(index) - 1]
            if "status = 'removed'" in query:
                row["status"] = "removed"
            if "resolved_at = now()" in query:
                row["resolved_at"] = NOW
            return "UPDATE 1"
        raise AssertionError(query)


class Audit:
    def __init__(self, store, *, fail=False):
        self.store = store
        self.fail = fail

    async def lock_transaction(self, conn):
        assert conn is self.store and conn.is_in_transaction()
        assert "write" not in conn.order, "Audit lock must precede business mutation"
        conn.locked = True
        conn.order.append("audit_lock")

    async def log_in_transaction(self, conn, **entry):
        assert conn is self.store and conn.is_in_transaction() and conn.locked
        if self.fail:
            raise AuditUnavailable("synthetic audit persistence failure")
        conn.order.append("audit")
        conn.receipts.append(deepcopy(entry))
        return UUID(NEW_KEY)

    async def log(self, **entry):
        self.store.buffered.append(deepcopy(entry))


async def invoke(fn, store, audit, **kwargs):
    """Allow the unfixed handler to run: its missing audit input is the defect."""
    available = {"db_pool": store, "db_client": SimpleNamespace(pool=store),
                 "current_user": actor(), "audit_logger": audit, **kwargs}
    return await fn(**{k: v for k, v in available.items() if k in inspect.signature(fn).parameters})


CASES = ("permission_add", "permission_remove", "member_add", "member_upsert",
         "member_role", "member_status", "member_primary", "member_remove",
         "rule_create", "rule_create_global", "rule_update", "rule_delete",
         "event_resolve", "key_create", "key_rotate", "key_disable")


def setup_case(monkeypatch, case):
    family = case.split("_")[0]
    existing = case not in {"permission_add", "member_add", "rule_create", "rule_create_global", "key_create"}
    store = Store(family, existing=existing)
    monkeypatch.setattr(keys, "get_encryption_service", lambda: SimpleNamespace(encrypt=lambda value: CIPHER))

    def invalidate(**kwargs):
        assert store.depth == 0, "Credential cache must be invalidated only after commit"
        store.cache_events.append(kwargs)

    monkeypatch.setattr(keys.CredentialResolver, "invalidate_cache", invalidate)
    return store


async def perform(case, store, audit):
    if case.startswith("permission"):
        fn = roles.add_permission_to_role if case.endswith("add") else roles.remove_permission_from_role
        return await invoke(fn, store, audit, role_id=ROLE, permission_id=PERMISSION)
    if case in {"member_add", "member_upsert"}:
        return await invoke(members.add_user_to_tenant, store, audit,
                            request=AddTenantUserRequest(user_id=TARGET, tenant_id=TENANT, role_name="readonly"))
    if case == "member_remove":
        return await invoke(members.remove_user_from_tenant, store, audit, tenant_user_id=RESOURCE)
    if case.startswith("member"):
        field = {"member_role": {"role_name": "readonly"}, "member_status": {"status": "suspended"},
                 "member_primary": {"is_primary": True}}[case]
        return await invoke(members.update_tenant_user, store, audit, tenant_user_id=RESOURCE,
                            request=UpdateTenantUserRequest(**field))
    if case.startswith("rule"):
        payload = abuse.AbuseRuleSchema(rule_name="after", rule_type="velocity_spike", parameters={"window": 10})
        if case.startswith("rule_create"):
            return await invoke(abuse.create_abuse_rule, store, audit, rule=payload,
                                tenant_id=None if case.endswith("global") else UUID(TENANT))
        if case == "rule_update":
            return await invoke(abuse.update_abuse_rule, store, audit, rule_id=UUID(RESOURCE), rule=payload)
        return await invoke(abuse.delete_abuse_rule, store, audit, rule_id=UUID(RESOURCE))
    if case == "event_resolve":
        return await invoke(abuse.resolve_abuse_event, store, audit, event_id=UUID(RESOURCE),
                            request=abuse.ResolveEventRequest(notes="reviewed", false_positive=True))
    if case == "key_disable":
        return await invoke(keys.disable_credential, store, audit, credential_id=RESOURCE)
    return await invoke(keys.create_credential, store, audit,
                        payload=keys.CredentialCreateRequest(provider="groq", api_key=PLAIN, label="after"))


@pytest.mark.parametrize("case", CASES)
async def test_privileged_change_has_one_atomic_before_after_receipt(monkeypatch, case):
    store = setup_case(monkeypatch, case)
    original = deepcopy(store.rows)
    await perform(case, store, Audit(store))
    assert store.mutations > 0 and store.rows != original
    assert len(store.receipts) == 1, f"{case} committed without one transactional audit receipt"
    assert not store.buffered, "Buffered/postcommit audit is not an atomic receipt"
    entry = store.receipts[0]
    assert str(entry["actor_id"]) == ACTOR
    expected_tenant = None if case.startswith("permission") or case.endswith("global") else TENANT
    assert (str(entry["tenant_id"]) if entry.get("tenant_id") else None) == expected_tenant
    assert entry.get("action") and entry.get("resource_type")
    assert "before_state" in entry and "after_state" in entry
    assert entry["before_state"] != entry["after_state"], "Receipt must describe the actual change"
    if not original:
        assert not entry["before_state"]
    else:
        assert entry["before_state"]
    assert store.order.index("audit_lock") < store.order.index("write") < store.order.index("audit")
    serialized = json.dumps(entry, default=str)
    assert PLAIN not in serialized and CIPHER not in serialized and "old-" + CIPHER not in serialized
    if case.startswith("key"):
        assert len(store.cache_events) == 1
        if case == "key_rotate":
            assert [r["status"] for r in store.rows] == ["disabled", "active"]


@pytest.mark.parametrize("case", CASES)
async def test_audit_failure_rolls_back_business_change_and_no_cache_invalidation(monkeypatch, case):
    store = setup_case(monkeypatch, case)
    original = deepcopy(store.rows)
    with pytest.raises((AuditUnavailable, HTTPException)):
        await perform(case, store, Audit(store, fail=True))
    assert store.rows == original, f"{case}: business data survived failed mandatory audit"
    assert store.mutations == 0 and not store.receipts and not store.cache_events
    assert "rollback" in store.order


@pytest.mark.parametrize("state", ["missing", "disabled"])
async def test_idempotent_credential_disable_has_no_false_change_receipt(monkeypatch, state):
    store = setup_case(monkeypatch, "key_disable")
    if state == "missing":
        store.rows.clear()
    else:
        store.rows[0]["status"] = "disabled"
    await perform("key_disable", store, Audit(store))
    assert not store.receipts and not store.cache_events and store.mutations == 0


async def test_member_cross_tenant_denial_leaves_no_audit_or_mutation(monkeypatch):
    store = setup_case(monkeypatch, "member_add")
    with pytest.raises(HTTPException) as caught:
        await invoke(members.add_user_to_tenant, store, Audit(store),
                     current_user=actor(tenant=OTHER, role="tenant_admin"),
                     request=AddTenantUserRequest(user_id=TARGET, tenant_id=TENANT))
    assert caught.value.status_code == 403
    assert not store.rows and not store.receipts and store.mutations == 0
