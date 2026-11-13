"""Bughunt telephony-control-9: an early error return must not leave a claim behind.

Before the fix, a 404 / 400 returned from inside the claim transaction
committed an Idempotency-Key row with no result, so every retry with that key
got 409 'Request In Progress' (same payload) or 409 'different payload'.
The fake connection below rolls the idempotency table back when the
transaction body raises, as Postgres does.
"""
import copy
import json
from uuid import uuid4

import pytest

from app.api.v1.endpoints.telephony_sip import (
    SIPTrunkUpdateRequest,
    activate_sip_trunk,
    create_sip_trunk,
    update_sip_trunk,
)
from tests.unit.test_telephony_sip_api import (  # noqa: F401  (fixture re-export)
    FakePool,
    FakeTelephonyConn,
    _create_payload,
    _make_request,
    wsf_context,
)


class _RollbackTransaction:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        self._snapshot = copy.deepcopy(self._conn.idempotency)
        self._audit_snapshot = copy.deepcopy(self._conn.audit_events)
        self._conn.transaction_depth += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._conn.idempotency = self._snapshot
            self._conn.audit_events = self._audit_snapshot
        self._conn.transaction_depth -= 1
        return False


class _Audit:
    def __init__(self, conn):
        self.conn = conn

    async def lock_transaction(self, conn):
        assert conn is self.conn
        assert conn.transaction_depth > 0
        conn.audit_lock_calls += 1

    async def log_in_transaction(self, conn, **event):
        assert conn is self.conn
        assert conn.transaction_depth > 0 and conn.audit_lock_calls > 0
        assert event["tenant_id"] == conn.current_tenant_id
        assert event["actor_id"] == conn.current_user_id
        conn.audit_events.append(copy.deepcopy(event))


class _RollbackConn(FakeTelephonyConn):
    def __init__(self):
        super().__init__()
        self.transaction_depth = 0
        self.audit_lock_calls = 0
        self.audit_events = []
        self.audit = _Audit(self)

    def transaction(self):
        return _RollbackTransaction(self)


@pytest.fixture
def rb_context(wsf_context):
    seeded, _pool, user = wsf_context
    conn = _RollbackConn()
    conn.threshold_policies = seeded.threshold_policies
    return conn, FakePool(conn), user


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


async def _update(pool, user, trunk_id, payload, key):
    return await update_sip_trunk(
        trunk_id=trunk_id,
        payload=payload,
        request=_make_request(f"/api/v1/telephony/sip/trunks/{trunk_id}"),
        idempotency_key=key,
        current_user=user,
        db_pool=pool,
        audit_logger=pool._conn.audit,
    )


@pytest.mark.asyncio
async def test_retry_after_invalid_patch_is_not_stuck_in_progress(rb_context):
    conn, pool, user = rb_context
    created = await create_sip_trunk(
        payload=_create_payload("idem-trunk"),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="bh-create",
        current_user=user,
        db_pool=pool,
        audit_logger=conn.audit,
    )
    trunk_id = _body(created)["id"]
    assert [event["action"] for event in conn.audit_events] == ["sip.trunk.create"]
    bad = SIPTrunkUpdateRequest(clear_auth=True, auth_username="someone")

    first = await _update(pool, user, trunk_id, bad, "bh-patch")
    assert first.status_code == 400
    assert _body(first)["title"] == "Invalid Authentication Patch"

    # Same key + same payload: evaluated again, not 'Request In Progress'.
    second = await _update(pool, user, trunk_id, bad, "bh-patch")
    assert second.status_code == 400, _body(second)
    assert _body(second)["title"] == "Invalid Authentication Patch"

    assert [event["action"] for event in conn.audit_events] == ["sip.trunk.create"]
    assert not any(key[2] == "bh-patch" for key in conn.idempotency)

    # Same key after fixing the payload: not 'reused with a different payload'.
    fixed = await _update(pool, user, trunk_id, SIPTrunkUpdateRequest(metadata={"region": "eu"}), "bh-patch")
    assert getattr(fixed, "status_code", 200) == 200, fixed
    assert fixed.metadata == {"region": "eu"}
    assert [event["action"] for event in conn.audit_events] == ["sip.trunk.create", "sip.trunk.update"]
    assert conn.audit_lock_calls == 4 and conn.transaction_depth == 0


@pytest.mark.asyncio
async def test_retry_after_trunk_not_found_is_not_stuck(rb_context):
    conn, pool, user = rb_context
    missing = uuid4()

    async def activate():
        return await activate_sip_trunk(
            trunk_id=missing,
            request=_make_request(f"/api/v1/telephony/sip/trunks/{missing}/activate"),
            idempotency_key="bh-activate-missing",
            current_user=user,
            db_pool=pool,
            audit_logger=conn.audit,
        )

    first = await activate()
    second = await activate()
    assert first.status_code == 404
    assert second.status_code == 404, _body(second)
    assert _body(second)["title"] == "Trunk Not Found"
    assert conn.idempotency == {} and conn.audit_events == []
    assert conn.audit_lock_calls == 2 and conn.transaction_depth == 0
