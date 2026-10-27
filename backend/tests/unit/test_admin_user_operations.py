"""Actual recovery routes/helpers, synthetic transactional DB/audit/auth ports.

These cases prove API lifecycle and fail-closed semantics. They do not prove
PostgreSQL locks, SQL/index execution, or production authentication/ACLs.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_db_client, require_platform_admin
from app.api.v1.endpoints.admin import user_operations as operations
from app.core.error_handlers import validation_exception_handler

ACTOR = UUID("10000000-0000-0000-0000-000000000001")
OTHER = UUID("10000000-0000-0000-0000-000000000002")
TARGET = UUID("20000000-0000-0000-0000-000000000001")
TENANT = UUID("30000000-0000-0000-0000-000000000001")
BASE = "/api/v1/admin/users/operations"
TOKEN = "v1:" + "a" * 64
PASSWORD = "synthetic-operation-password-only"


class MemoryDB:
    def __init__(self):
        self.rows = []
        self.now = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)
        self.depth = 0
        self.statements = []
        self.fail_read = False
        self.fail_commit = False
        self.authorized = {ACTOR, OTHER}

    @asynccontextmanager
    async def transaction(self):
        old = deepcopy(self.rows)
        self.depth += 1
        try:
            yield self
            if self.fail_commit:
                raise RuntimeError("Synthetic commit failure")
        except BaseException:
            self.rows[:] = old
            raise
        finally:
            self.depth -= 1

    def is_in_transaction(self):
        return self.depth > 0

    async def execute(self, sql, *args):
        self.statements.append(sql)

    async def fetchval(self, sql, *args):
        assert sql == "SELECT clock_timestamp()"
        return self.now

    async def fetchrow(self, sql, *args):
        if self.fail_read:
            raise RuntimeError("Synthetic read failure")
        assert "event_id=$1::uuid AND actor_id=$2::uuid" in sql
        return next((deepcopy(row) for row in self.rows if str(row["event_id"]) == args[0]
                     and str(row["actor_id"]) == args[1] and row["action"] == operations.PREPARED_ACTION), None)

    async def fetch(self, sql, *args):
        if self.fail_read:
            raise RuntimeError("Synthetic read failure")
        actor = UUID(args[0])
        terminal = [row for row in self.rows if row["actor_id"] == actor and row["action"] in operations.TERMINAL_ACTIONS
                    and type(row["metadata"].get("user_operation_version")) is int
                    and row["metadata"]["user_operation_version"] == 1]
        if "SELECT p.event_id" not in sql:
            assert "metadata ? 'user_operation_id'" in sql and "resource_type='user'" in sql
            return deepcopy([row for row in terminal if row["metadata"]["user_operation_id"] in args[2]])
        _, cursor_time, cursor_id, state, _, limit = args
        terminal_ids = {row["metadata"]["user_operation_id"] for row in terminal}
        rows = [row for row in self.rows if row["actor_id"] == actor and row["action"] == operations.PREPARED_ACTION]
        rows = [row for row in rows if state == "all" or (state == "terminal") == (str(row["event_id"]) in terminal_ids)]
        if cursor_time is not None:
            rows = [row for row in rows if (row["event_time"], str(row["event_id"])) < (cursor_time, cursor_id)]
        rows.sort(key=lambda row: (row["event_time"], row["event_id"]), reverse=True)
        return deepcopy(rows[:limit])


class MemoryAudit:
    signing_key = "synthetic-operation-signing-key-only"

    def __init__(self, db):
        self.db = db
        self.fail = False

    async def lock_transaction(self, conn):
        assert conn.is_in_transaction()
        conn.statements.append("AUDIT LOCK")

    async def log_in_transaction(self, conn, **event):
        if self.fail:
            raise RuntimeError("Synthetic audit insert failure")
        assert conn.is_in_transaction()
        event_id = uuid4()
        self.db.rows.append({"event_id": event_id, "event_time": self.db.now + timedelta(microseconds=len(self.db.rows)),
            "actor_id": UUID(str(event["actor_id"])), "action": event["action"],
            "resource_type": event["resource_type"], "resource_id": event.get("resource_id"),
            "metadata": deepcopy(event["metadata"])})
        return event_id


@pytest.fixture
def system(monkeypatch):
    db = MemoryDB()
    audit = MemoryAudit(db)
    owner = {"id": ACTOR}

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        assert pool is db and tenant_id is None
        async with db.transaction():
            yield db

    async def current_authority(conn, actor_id):
        if UUID(str(actor_id)) not in db.authorized:
            raise HTTPException(403, "Current platform admin authority is required.")

    monkeypatch.setattr(operations, "acquire_with_tenant", acquire)
    monkeypatch.setattr(operations, "require_current_platform_admin", current_authority)
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(operations.router, prefix="/api/v1/admin")
    app.dependency_overrides[require_platform_admin] = lambda: CurrentUser(
        id=str(owner["id"]), email="synthetic-admin@example.com", role="platform_admin", tenant_id=None)
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=db)
    app.dependency_overrides[get_audit_logger] = lambda: audit
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        yield SimpleNamespace(db=db, audit=audit, owner=owner, client=client)


def payload(kind="update", **changes):
    intents = {
        "create": {"name": "Prepared Name", "email": "prepared@example.com", "role": "user", "tenant_id": str(TENANT)},
        "update": {"expected_review_token": TOKEN, "name": "Prepared Name"},
        "delete": {"expected_review_token": TOKEN},
        "primary_membership": {"expected_review_token": "pm1:" + "b" * 64, "tenant_id": str(TENANT), "role": "user"},
    }
    result = {"kind": kind, "target_user_id": None if kind == "create" else str(TARGET), "intent": intents[kind]}
    if kind == "create":
        result["password"] = PASSWORD
    result.update(changes)
    return result


def prepare(system, kind="update", **changes):
    response = system.client.post(BASE, json=payload(kind, **changes))
    assert response.status_code == 201, response.text
    return response.json()


async def admission(system, view, **changes):
    body = payload(view["kind"])
    args = dict(actor_id=system.owner["id"], operation_id=view["operation_id"], kind=body["kind"],
        target_user_id=body["target_user_id"], intent=body["intent"], audit_logger=system.audit,
        password=body.get("password"))
    args.update(changes)
    return await operations.require_prepared_operation(system.db, **args)


def user_snapshot(**changes):
    value = {"id": str(TARGET), "email": "prepared@example.com", "name": "Prepared Name", "role": "user",
             "is_active": True, "is_verified": True, "review_token": "v1:" + "c" * 64, "tenant_id": str(TENANT)}
    value.update(changes)
    return value


def receipt(view):
    if view["kind"] == "create":
        return {"request_id": view["operation_id"], "state": "created", "audit_event_id": str(uuid4()),
                "user_id": str(TARGET), "user": user_snapshot(), "intent": view["intent"]}
    if view["kind"] == "update":
        return {**user_snapshot(), "operation_id": view["operation_id"], "operation": "updated", "audit_event_id": str(uuid4())}
    if view["kind"] == "delete":
        return {"id": str(TARGET), "operation_id": view["operation_id"], "outcome": "deleted", "audit_event_id": str(uuid4()), "detail": "Deleted", "user": None}
    return {"operation": "primary_membership_configured", "operation_id": view["operation_id"], "user": user_snapshot(),
            "membership": {"id": str(uuid4()), "role": "user", "status": "active", "is_primary": True},
            "tenant_id": str(TENANT), "audit_event_id": str(uuid4()),
            "credential_cutoff": {"revoked_sessions": 1, "revoked_refresh_tokens": 1,
                                  "invalidated_mfa_challenges": 0, "invalidated_passkey_challenges": 0}}


@pytest.mark.parametrize("kind", ["create", "update", "delete", "primary_membership"])
def test_fresh_client_discovers_exact_prepared_intent_without_browser_metadata(system, kind):
    view = prepare(system, kind)
    listed = system.client.get(BASE).json()
    read = system.client.get(BASE + "/" + view["operation_id"]).json()
    assert listed["items"] == [view] and read == view
    assert view["state"] == "prepared" and view["receipt"] is None and view["dispatch_eligible"] is True
    assert PASSWORD not in json.dumps(system.db.rows, default=str)
    assert "password" not in json.dumps(view)
    assert len(system.db.rows) == 1  # preparation is the only effect
    assert system.db.statements[0] == "AUDIT LOCK"
    assert "platform-admin-authority" in system.db.statements[1]


def test_second_prepare_is_fenced_even_after_original_dispatch_window_expires(system):
    first = prepare(system)
    system.db.now += timedelta(minutes=16)
    rejected = system.client.post(BASE, json=payload("delete"))
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["operation_id"] == first["operation_id"]
    current = system.client.get(BASE).json()["items"][0]
    assert current["state"] == "prepared" and current["dispatch_eligible"] is False
    assert current["terminal_audit_event_id"] is None


@pytest.mark.asyncio
async def test_expiry_is_not_cancellation_but_delayed_dispatch_cannot_execute(system):
    view = prepare(system)
    system.db.now += timedelta(minutes=15)
    async with system.db.transaction():
        with pytest.raises(HTTPException) as error:
            await admission(system, view)
    assert error.value.detail["code"] == "user_operation_expired"
    cancelled = system.client.post(BASE + "/" + view["operation_id"] + "/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    prepare(system, "delete")


@pytest.mark.asyncio
async def test_cancelled_operation_rejects_late_original_request_without_effect(system):
    view = prepare(system)
    first = system.client.post(BASE + "/" + view["operation_id"] + "/cancel").json()
    second = system.client.post(BASE + "/" + view["operation_id"] + "/cancel").json()
    assert first == second and first["state"] == "cancelled"
    assert len(system.db.rows) == 2
    async with system.db.transaction():
        with pytest.raises(HTTPException) as error:
            await admission(system, view)
    assert error.value.detail["code"] == "user_operation_cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["create", "update", "delete", "primary_membership"])
async def test_committed_receipt_is_original_snapshot_and_cancel_never_undoes_or_replays(system, kind):
    view = prepare(system, kind)
    async with system.db.transaction():
        prepared = await admission(system, view)
        assert prepared.replay_receipt is None
        data = receipt(view)
        metadata = operations.user_operation_terminal_metadata(prepared, data)
        assert "audit_event_id" not in metadata["user_operation_receipt"]
        event_id = await system.audit.log_in_transaction(system.db, actor_id=ACTOR, resource_type="user",
            resource_id=TARGET, action={"create": "user_created", "update": "user_updated", "delete": "user_deleted",
                "primary_membership": "user_primary_membership_configured"}[kind], metadata=metadata)
    system.db.now += timedelta(days=1)
    # There is deliberately no current user lookup in this fixture. Receipt
    # recovery remains possible after the target was edited or removed.
    recovered = system.client.get(BASE + "/" + view["operation_id"]).json()
    cancelled = system.client.post(BASE + "/" + view["operation_id"] + "/cancel").json()
    assert recovered == cancelled and recovered["state"] == "committed"
    assert recovered["receipt"]["audit_event_id"] == str(event_id)
    async with system.db.transaction():
        replay = await admission(system, view)
    assert replay.replay_receipt == recovered["receipt"] and len(system.db.rows) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["target", "kind", "intent", "password"])
async def test_exact_bound_scope_and_create_secret_cannot_be_substituted(system, change):
    view = prepare(system, "create" if change == "password" else "update")
    changes = {"target": {"target_user_id": str(uuid4())}, "kind": {"kind": "delete", "intent": {"expected_review_token": TOKEN}},
               "intent": {"intent": {"expected_review_token": TOKEN, "name": "Different name"}},
               "password": {"password": "different-synthetic-password"}}
    async with system.db.transaction():
        with pytest.raises(HTTPException) as error:
            await admission(system, view, **changes[change])
    assert error.value.status_code == 409 and error.value.detail["code"] == "user_operation_mismatch"
    assert len(system.db.rows) == 1


@pytest.mark.asyncio
async def test_missing_or_retained_evidence_cannot_recreate_an_operation(system):
    view = prepare(system)
    system.db.rows.clear()  # models absent retained evidence, not a retry path
    async with system.db.transaction():
        with pytest.raises(HTTPException) as error:
            await admission(system, view)
    assert error.value.status_code == 404
    assert system.client.post(BASE + "/" + view["operation_id"] + "/cancel").status_code == 404
    assert not system.db.rows


def test_foreign_actor_cannot_discover_cancel_or_borrow_preparation(system):
    view = prepare(system)
    system.owner["id"] = OTHER
    assert system.client.get(BASE).json()["items"] == []
    assert system.client.get(BASE + "/" + view["operation_id"]).status_code == 404
    assert system.client.post(BASE + "/" + view["operation_id"] + "/cancel").status_code == 404
    other = prepare(system, "delete")
    assert other["operation_id"] != view["operation_id"]
    system.owner["id"] = ACTOR
    assert system.client.get(BASE).json()["items"][0]["operation_id"] == view["operation_id"]


@pytest.mark.asyncio
async def test_current_authority_rechecked_at_dispatch_and_read_cancel_prepare(system):
    view = prepare(system)
    system.db.authorized.remove(ACTOR)
    async with system.db.transaction():
        with pytest.raises(HTTPException) as error:
            await admission(system, view)
    assert error.value.status_code == 403
    for response in (system.client.get(BASE), system.client.get(BASE + "/" + view["operation_id"]),
                     system.client.post(BASE + "/" + view["operation_id"] + "/cancel"), system.client.post(BASE, json=payload())):
        assert response.status_code == 403
    assert len(system.db.rows) == 1


@pytest.mark.parametrize("failure", ["read", "audit", "commit"])
def test_prepare_failure_never_returns_success_or_leaves_uncommitted_record(system, failure):
    if failure == "read":
        system.db.fail_read = True
    elif failure == "audit":
        system.audit.fail = True
    else:
        system.db.fail_commit = True
    response = system.client.post(BASE, json=payload("create"))
    assert response.status_code == 503 and response.json()["detail"]["code"] == "user_operation_unavailable"
    assert PASSWORD not in response.text and not system.db.rows


def test_discovery_failure_is_unavailable_not_empty_and_cancel_audit_failure_keeps_pending(system):
    view = prepare(system)
    system.db.fail_read = True
    assert system.client.get(BASE).status_code == 503
    system.db.fail_read = False
    system.audit.fail = True
    assert system.client.post(BASE + "/" + view["operation_id"] + "/cancel").status_code == 503
    assert system.client.get(BASE).json()["items"][0]["state"] == "prepared"


def test_paginated_terminal_history_does_not_hide_pending_or_lose_equal_timestamp_items(system):
    identifiers = []
    for _ in range(6):
        view = prepare(system)
        identifiers.append(view["operation_id"])
        assert system.client.post(BASE + "/" + view["operation_id"] + "/cancel").status_code == 200
    pending = prepare(system, "delete")
    assert system.client.get(BASE).json()["items"][0]["operation_id"] == pending["operation_id"]
    seen, cursor = [], None
    for _ in range(3):
        params = {"state": "terminal", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        result = system.client.get(BASE, params=params).json()
        seen += [item["operation_id"] for item in result["items"]]
        cursor = result["next_cursor"]
    assert set(seen) == set(identifiers) and len(seen) == 6 and cursor is None
    assert system.client.get(BASE, params={"cursor": "malformed"}).status_code == 422


@pytest.mark.parametrize("mutate", ["version", "uuid", "resource", "scope"])
def test_malformed_preparation_never_claims_readable_safe_state(system, mutate):
    view = prepare(system)
    row = system.db.rows[0]
    if mutate == "version":
        row["metadata"]["user_operation_version"] = True
    elif mutate == "uuid":
        row["metadata"]["user_operation_target_user_id"] = str(TARGET).replace("-", "")
    elif mutate == "resource":
        row["resource_id"] = OTHER
    else:
        row["metadata"]["user_operation_expires_at"] = system.db.now.isoformat()
    assert system.client.get(BASE + "/" + view["operation_id"]).status_code == 503


@pytest.mark.asyncio
async def test_terminal_validation_rejects_wrong_updated_effect_before_audit(system):
    view = prepare(system)
    async with system.db.transaction():
        prepared = await admission(system, view)
        wrong = {**receipt(view), "name": "Not the reviewed name"}
        with pytest.raises(ValueError, match="requested changes"):
            operations.user_operation_terminal_metadata(prepared, wrong)
    assert len(system.db.rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("name", "Wrong name"), ("email", "different@example.com"),
    ("role", "platform_admin"), ("tenant_id", str(OTHER)), ("is_active", False), ("is_verified", "true")])
async def test_create_snapshot_must_match_original_intent_and_actual_active_verified_flags(system, field, value):
    view = prepare(system, "create")
    async with system.db.transaction():
        prepared = await admission(system, view)
        wrong = receipt(view)
        wrong["user"][field] = value
        with pytest.raises(ValueError, match="prepared active verified"):
            operations.user_operation_terminal_metadata(prepared, wrong)
    assert len(system.db.rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["update", "delete", "primary_membership"])
async def test_another_same_target_operation_receipt_is_rejected_before_audit(system, kind):
    view = prepare(system, kind)
    async with system.db.transaction():
        prepared = await admission(system, view)
        wrong = {**receipt(view), "operation_id": str(uuid4())}
        with pytest.raises(ValueError, match="exact prepared operation"):
            operations.user_operation_terminal_metadata(prepared, wrong)
    assert len(system.db.rows) == 1


@pytest.mark.parametrize("extra", [{"password": PASSWORD}, {"arbitrary_metadata": PASSWORD}, {"is_active": "true"}])
def test_strict_intents_reject_extra_or_coerced_fields_without_echoing_inputs(system, extra):
    body = payload()
    body["intent"].update(extra)
    response = system.client.post(BASE, json=body)
    assert response.status_code == 422 and PASSWORD not in response.text
    assert not system.db.rows


def test_existing_blank_name_update_remains_supported_and_normalized(system):
    view = prepare(system, intent={"expected_review_token": TOKEN, "name": "   "})
    assert view["intent"]["name"] == ""


@pytest.mark.asyncio
async def test_duplicate_terminal_evidence_is_unknown_never_a_guessed_success(system):
    view = prepare(system)
    system.client.post(BASE + "/" + view["operation_id"] + "/cancel")
    duplicate = deepcopy(system.db.rows[-1])
    duplicate["event_id"] = uuid4()
    system.db.rows.append(duplicate)
    assert system.client.get(BASE + "/" + view["operation_id"]).status_code == 503
    async with system.db.transaction():
        with pytest.raises(HTTPException) as error:
            await admission(system, view)
    assert error.value.status_code == 503
