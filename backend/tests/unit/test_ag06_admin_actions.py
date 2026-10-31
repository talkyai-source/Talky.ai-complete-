"""No external effects: legacy Admin controls must not fabricate cancellation/retry."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.admin import actions


@pytest.fixture
def synthetic_cancel_principal(monkeypatch):
    """Keep these fake-DB tests about dispatch ownership, not authentication.

    The normal-auth PostgreSQL cancellation suite exercises the real principal
    loader. Only requests explicitly registered here use this synthetic port.
    """
    principals = {}

    def request_for(actor):
        session_id = str(uuid4())
        request = Request({"type": "http", "method": "POST", "path": "/cancel", "headers": []})
        request.state.authenticated_user_id = actor.id
        request.state.authenticated_session_id = session_id
        principals[(actor.id, session_id)] = {
            "id": actor.id, "role": actor.role, "tenant_id": getattr(actor, "tenant_id", None),
        }
        return request

    async def load_principal(_conn, claims):
        principal = principals[(claims["sub"], claims["sid"])]
        assert claims.get("tenant_id") == principal["tenant_id"]
        return dict(principal)

    monkeypatch.setattr(actions, "load_session_principal", load_principal)
    return request_for


class ActionDB:
    def __init__(self, *, status="pending", claimed_during_read=False, owned=True):
        self.row = {"id": str(uuid4()), "tenant_id": str(uuid4()), "type": "send_email",
                    "status": status, "input_data": {"request_hash": "a" * 64},
                    "output_data": {}, "idempotency_key": "reviewed-request" if owned else None,
                    "triggered_by": "assistant"}
        self.claimed_during_read = claimed_during_read
        self.writes = []
        self.pool = self

    def table(self, _name):
        return Query(self)

    @asynccontextmanager
    async def acquire(self, **_kwargs):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, *_args):
        return "SET"

    async def fetchrow(self, _query, *_args):
        # The new locked read sees the claimant's current state, not a
        # previously read status. SQL/concurrency itself is tested on PG.
        if self.claimed_during_read:
            self.row["status"] = "running"
        return dict(self.row)

    async def fetchval(self, query, *_args):
        if query.strip().upper() == "SHOW TRANSACTION_ISOLATION":
            return "read committed"
        return False


class Query:
    def __init__(self, db):
        self.db = db
        self.operation = "select"
        self.payload = None

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def single(self):
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def execute(self):
        if self.operation == "select":
            previous = deepcopy(self.db.row)
            if self.db.claimed_during_read:
                self.db.row["status"] = "running"
            return SimpleNamespace(data=previous, error=None)
        self.db.writes.append((self.operation, self.payload))
        if self.operation == "update":
            self.db.row.update(self.payload)
        return SimpleNamespace(data=[self.payload], error=None)


async def test_claim_after_old_status_read_cannot_be_reported_cancelled(synthetic_cancel_principal):
    db = ActionDB(claimed_during_read=True)
    actor = SimpleNamespace(id=str(uuid4()), role="platform_admin", tenant_id=None)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], actor, db, request=synthetic_cancel_principal(actor))
    assert caught.value.status_code == 409
    assert db.row["status"] == "running" and not db.writes


async def test_pending_legacy_send_audit_is_not_a_cancellable_execution_claim(synthetic_cancel_principal):
    db = ActionDB(owned=False)
    actor = SimpleNamespace(id=str(uuid4()), role="platform_admin", tenant_id=None)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], actor, db, request=synthetic_cancel_principal(actor))
    assert caught.value.status_code == 409
    assert db.row["status"] == "pending" and not db.writes


@pytest.mark.parametrize("kind", ["send_email", "send_sms", "set_reminder"])
async def test_unsupported_retry_never_creates_an_unconsumed_pending_row(kind):
    db = ActionDB(status="failed")
    db.row["type"] = kind
    with pytest.raises(HTTPException) as caught:
        await actions.retry_action(db.row["id"], SimpleNamespace(id=str(uuid4()), role="platform_admin"), db)
    assert caught.value.status_code == 501
    assert not db.writes and db.row["status"] == "failed"


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin"])
async def test_admin_without_tenant_cannot_read_or_mutate_receipts(role, synthetic_cancel_principal):
    db = ActionDB()
    user = SimpleNamespace(id=str(uuid4()), role=role, tenant_id=None)
    for endpoint in (actions.get_admin_action_detail, actions.retry_action, actions.cancel_action):
        with pytest.raises(HTTPException) as caught:
            kwargs = {"request": synthetic_cancel_principal(user)} if endpoint is actions.cancel_action else {}
            await endpoint(db.row["id"], user, db, **kwargs)
        assert caught.value.status_code == 403
    assert not db.writes


@pytest.mark.parametrize("status", ["running", "unknown", "completed", "failed", "scheduled"])
async def test_uncancellable_receipts_are_not_reset(status, synthetic_cancel_principal):
    db = ActionDB(status=status)
    actor = SimpleNamespace(id=str(uuid4()), role="platform_admin", tenant_id=None)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], actor, db, request=synthetic_cancel_principal(actor))
    assert caught.value.status_code == 409
    assert db.row["status"] == status and not db.writes


@pytest.mark.parametrize("state", ["missing_actor", "missing_session", "different_actor"])
async def test_cancel_requires_matching_authenticated_request_state(state, monkeypatch):
    db = ActionDB()
    actor = SimpleNamespace(id=str(uuid4()), role="platform_admin", tenant_id=None)
    request = Request({"type": "http", "method": "POST", "path": "/cancel", "headers": []})
    if state != "missing_actor":
        request.state.authenticated_user_id = str(uuid4()) if state == "different_actor" else actor.id
    if state != "missing_session":
        request.state.authenticated_session_id = str(uuid4())

    async def unexpected_principal_read(*_args):
        pytest.fail("Unverified request state must not reach the principal lookup")

    monkeypatch.setattr(actions, "load_session_principal", unexpected_principal_read)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], actor, db, request=request)
    assert caught.value.status_code == 401
    assert db.row["status"] == "pending" and not db.writes


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable"])
async def test_cancel_rejects_stale_snapshot_before_resource_or_principal_access(
    isolation, monkeypatch, synthetic_cancel_principal,
):
    db = ActionDB()
    before = deepcopy(db.row)
    actor = SimpleNamespace(id=str(uuid4()), role="platform_admin", tenant_id=None)
    request = synthetic_cancel_principal(actor)

    async def transaction_setting(query, *_args):
        assert query.strip().upper() == "SHOW TRANSACTION_ISOLATION"
        return isolation

    async def forbidden_access(*_args):
        pytest.fail("An older transaction snapshot must not read the action or admit a principal")

    monkeypatch.setattr(db, "fetchval", transaction_setting)
    monkeypatch.setattr(db, "fetchrow", forbidden_access)
    monkeypatch.setattr(actions, "load_session_principal", forbidden_access)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], actor, db, request=request)
    assert caught.value.status_code == 503
    assert db.row == before and not db.writes
