"""Item04 SIP mutations retain idempotency and audit-before-external ordering."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.api.v1.endpoints.telephony_sip import codec_policies as codec, route_policies as routes, trunks
from app.api.v1.endpoints.telephony_sip.schemas import (
    CodecPolicyUpdateRequest, RoutePolicyCreateRequest, RoutePolicyUpdateRequest, SIPTrunkUpdateRequest,
)
from tests.unit.test_privileged_mutation_audit import (
    ACTOR, CIPHER, NOW, PLAIN, TENANT, Audit, AuditUnavailable, actor, invoke,
)
from tests.unit.test_telephony_sip_api import (
    FakeTelephonyConn, _create_codec_payload, _create_payload, _make_request,
)

TRUNK = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CODEC = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ROUTE = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class TransactionalSip(FakeTelephonyConn):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.locked = False
        self.order = []
        self.receipts = []
        self.mutations = 0
        self.external = []

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def acquire(self):
        yield self

    def snapshot(self):
        return deepcopy((self.trunks, self.codec_policies, self.route_policies,
                         self.idempotency, self.receipts, self.mutations))

    @asynccontextmanager
    async def transaction(self):
        before = self.snapshot()
        locked = self.locked
        self.depth += 1
        try:
            yield self
        except BaseException:
            (self.trunks, self.codec_policies, self.route_policies,
             self.idempotency, self.receipts, self.mutations) = before
            self.order.append("rollback")
            raise
        else:
            self.order.append("commit")
        finally:
            self.depth -= 1
            self.locked = locked

    def track(self, sql):
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.order.append("write")
            self.mutations += 1

    async def fetchrow(self, sql, *args):
        self.track(sql)
        # Asyncpg Records are snapshots, not the fixture's mutable table row.
        return deepcopy(await super().fetchrow(sql, *args))

    async def execute(self, sql, *args):
        self.track(sql)
        return await super().execute(sql, *args)


def seed(conn, case):
    common = {"tenant_id": TENANT, "created_at": NOW, "updated_at": NOW,
              "created_by": ACTOR, "updated_by": ACTOR, "metadata": {}}
    conn.trunks[TRUNK] = {
        **common, "id": TRUNK, "trunk_name": "synthetic-trunk", "sip_domain": "sip.example.test",
        "port": 5060, "transport": "udp", "direction": "both", "is_active": True,
        "auth_username": "synthetic", "auth_password_encrypted": CIPHER,
        "last_tested_at": None, "last_test_result": None, "deleted_at": None,
        "live_registration_status": "registered", "live_status_detail": None,
        "live_status_checked_at": NOW,
    }
    conn.codec_policies[CODEC] = {
        **common, "id": CODEC, "policy_name": "synthetic-codec", "allowed_codecs": ["PCMU", "PCMA"],
        "preferred_codec": "PCMU", "sample_rate_hz": 8000, "ptime_ms": 20,
        "max_bitrate_kbps": None, "jitter_buffer_ms": 60, "is_active": True,
    }
    conn.route_policies[ROUTE] = {
        **common, "id": ROUTE, "policy_name": "synthetic-route", "route_type": "outbound",
        "priority": 100, "match_pattern": ".*", "target_trunk_id": TRUNK, "codec_policy_id": CODEC,
        "strip_digits": 0, "prepend_digits": None, "is_active": True,
    }
    family, operation = case.split("_")
    table = {"trunk": conn.trunks, "codec": conn.codec_policies, "route": conn.route_policies}[family]
    identifier = {"trunk": TRUNK, "codec": CODEC, "route": ROUTE}[family]
    if operation == "activate":
        table[identifier]["is_active"] = False


def prepare(monkeypatch, case):
    conn = TransactionalSip()
    seed(conn, case)
    for module in (trunks, codec, routes):
        monkeypatch.setattr(module, "_enforce_ws_i_quota", AsyncMock(return_value=None))
    monkeypatch.setattr(trunks, "get_encryption_service", lambda: SimpleNamespace(encrypt=lambda value: CIPHER))

    async def sync(row, **kwargs):
        conn.order.append("external")
        conn.external.append((str(row["id"]), kwargs["active"]))

    monkeypatch.setattr(trunks, "_sync_trunk_pjsip_config", sync)
    return conn


async def perform(case, conn, audit):
    family, operation = case.split("_")
    module = {"trunk": trunks, "codec": codec, "route": routes}[family]
    suffix = {"trunk": "sip_trunk", "codec": "codec_policy", "route": "route_policy"}[family]
    function = getattr(module, f"{operation}_{suffix}")
    kwargs = {"request": _make_request("/synthetic"), "current_user": actor(),
              "idempotency_key": "synthetic-audit-key", "x_request_id": None}
    if operation != "create":
        kwargs["trunk_id" if family == "trunk" else "policy_id"] = UUID(
            {"trunk": TRUNK, "codec": CODEC, "route": ROUTE}[family])
    if operation == "create":
        if family == "trunk":
            kwargs["payload"] = _create_payload("new-synthetic-trunk").model_copy(update={"auth_password": PLAIN})
        elif family == "codec":
            kwargs["payload"] = _create_codec_payload("new-synthetic-codec")
        else:
            kwargs["payload"] = RoutePolicyCreateRequest(
                policy_name="new-synthetic-route", match_pattern=".*", target_trunk_id=UUID(TRUNK))
    elif operation == "update":
        kwargs["payload"] = {"trunk": SIPTrunkUpdateRequest(port=5070),
                             "codec": CodecPolicyUpdateRequest(ptime_ms=30),
                             "route": RoutePolicyUpdateRequest(priority=120)}[family]
    return await invoke(function, conn, audit, **kwargs)


CASES = [f"{family}_{operation}" for family in ("trunk", "codec", "route")
         for operation in ("create", "update", "activate", "deactivate")]


@pytest.mark.parametrize("case", CASES)
async def test_sip_mutation_has_atomic_masked_before_after_receipt(monkeypatch, case):
    conn = prepare(monkeypatch, case)
    before = conn.snapshot()
    await perform(case, conn, Audit(conn))
    assert conn.snapshot() != before and conn.mutations > 0
    assert len(conn.receipts) == 1, f"{case}: committed without a transactional audit receipt"
    receipt = conn.receipts[0]
    assert str(receipt["actor_id"]) == ACTOR and str(receipt["tenant_id"]) == TENANT
    assert receipt["before_state"] != receipt["after_state"]
    assert (not receipt["before_state"]) == case.endswith("create")
    serialized = json.dumps(receipt, default=str)
    assert PLAIN not in serialized and CIPHER not in serialized
    assert conn.order.index("audit_lock") < conn.order.index("write") < conn.order.index("audit")
    if conn.external:
        assert conn.order.index("audit") < conn.order.index("external")


@pytest.mark.parametrize("case", CASES)
async def test_sip_audit_failure_rolls_back_business_and_idempotency_before_external_effect(monkeypatch, case):
    conn = prepare(monkeypatch, case)
    before = conn.snapshot()
    with pytest.raises(AuditUnavailable):
        await perform(case, conn, Audit(conn, fail=True))
    assert conn.snapshot() == before
    assert not conn.external, "An audit failure must not alter Asterisk configuration"
    assert "rollback" in conn.order


@pytest.mark.parametrize("family", ["trunk", "codec", "route"])
async def test_idempotent_sip_replay_does_not_duplicate_audit_or_external_effect(monkeypatch, family):
    case = family + "_create"
    conn = prepare(monkeypatch, case)
    audit = Audit(conn)
    await perform(case, conn, audit)
    assert len(conn.receipts) == 1
    before = deepcopy((conn.trunks, conn.codec_policies, conn.route_policies, conn.receipts, conn.external))
    conn.order.clear()
    await perform(case, conn, audit)
    assert (conn.trunks, conn.codec_policies, conn.route_policies, conn.receipts, conn.external) == before


@pytest.mark.parametrize("encoded", [False, True])
def test_sip_snapshot_masks_nested_credentials_with_or_without_json_codec(encoded):
    from app.api.v1.endpoints.telephony_sip._audit import snapshot

    metadata = {"ordinary": "keep", "nested": [{"api_key": PLAIN}, {"private-key": CIPHER}]}
    row = {"metadata": json.dumps(metadata) if encoded else metadata, "auth_password_encrypted": CIPHER}
    result = snapshot(row)
    assert result["metadata"]["ordinary"] == "keep"
    assert PLAIN not in json.dumps(result) and CIPHER not in json.dumps(result)
    assert row["metadata"] == (json.dumps(metadata) if encoded else metadata), "Masking must not mutate the saved row"
