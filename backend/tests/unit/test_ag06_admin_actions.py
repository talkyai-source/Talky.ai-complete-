"""No external effects: legacy Admin controls must not fabricate cancellation/retry."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.admin import actions


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

    async def fetchval(self, *_args):
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


async def test_claim_after_old_status_read_cannot_be_reported_cancelled():
    db = ActionDB(claimed_during_read=True)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], SimpleNamespace(id=str(uuid4()), role="platform_admin"), db)
    assert caught.value.status_code == 409
    assert db.row["status"] == "running" and not db.writes


async def test_pending_legacy_send_audit_is_not_a_cancellable_execution_claim():
    db = ActionDB(owned=False)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], SimpleNamespace(id=str(uuid4()), role="platform_admin"), db)
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
async def test_admin_without_tenant_cannot_read_or_mutate_receipts(role):
    db = ActionDB()
    user = SimpleNamespace(id=str(uuid4()), role=role, tenant_id=None)
    for endpoint in (actions.get_admin_action_detail, actions.retry_action, actions.cancel_action):
        with pytest.raises(HTTPException) as caught:
            await endpoint(db.row["id"], user, db)
        assert caught.value.status_code == 403
    assert not db.writes


@pytest.mark.parametrize("status", ["running", "unknown", "completed", "failed", "scheduled"])
async def test_uncancellable_receipts_are_not_reset(status):
    db = ActionDB(status=status)
    with pytest.raises(HTTPException) as caught:
        await actions.cancel_action(db.row["id"], SimpleNamespace(id=str(uuid4()), role="platform_admin"), db)
    assert caught.value.status_code == 409
    assert db.row["status"] == status and not db.writes
