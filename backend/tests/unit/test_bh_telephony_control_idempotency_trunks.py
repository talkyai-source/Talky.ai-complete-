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
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._conn.idempotency = self._snapshot
        return False


class _RollbackConn(FakeTelephonyConn):
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
    )
    trunk_id = _body(created)["id"]
    bad = SIPTrunkUpdateRequest(clear_auth=True, auth_username="someone")

    first = await _update(pool, user, trunk_id, bad, "bh-patch")
    assert first.status_code == 400
    assert _body(first)["title"] == "Invalid Authentication Patch"

    # Same key + same payload: evaluated again, not 'Request In Progress'.
    second = await _update(pool, user, trunk_id, bad, "bh-patch")
    assert second.status_code == 400, _body(second)
    assert _body(second)["title"] == "Invalid Authentication Patch"

    # Same key after fixing the payload: not 'reused with a different payload'.
    fixed = await _update(pool, user, trunk_id, SIPTrunkUpdateRequest(metadata={"region": "eu"}), "bh-patch")
    assert getattr(fixed, "status_code", 200) == 200, fixed
    assert fixed.metadata == {"region": "eu"}


@pytest.mark.asyncio
async def test_retry_after_trunk_not_found_is_not_stuck(rb_context):
    _conn, pool, user = rb_context
    missing = uuid4()

    async def activate():
        return await activate_sip_trunk(
            trunk_id=missing,
            request=_make_request(f"/api/v1/telephony/sip/trunks/{missing}/activate"),
            idempotency_key="bh-activate-missing",
            current_user=user,
            db_pool=pool,
        )

    first = await activate()
    second = await activate()
    assert first.status_code == 404
    assert second.status_code == 404, _body(second)
    assert _body(second)["title"] == "Trunk Not Found"
