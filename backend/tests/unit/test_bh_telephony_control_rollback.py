"""Bughunt telephony-control-7: rollback target eligibility and honest response.

An explicit ``target_version`` had no build_status filter, so a version whose
apply/verify failed (or that was never applied) could be marked active and
last-good. The adapter also ignores the artifact, so the response must say it
applied the current database state and whether it only simulated.
"""
import json
from typing import Any, Dict

import pytest

from app.api.v1.endpoints.telephony_runtime import (
    RuntimeActivateRequest,
    RuntimeRollbackRequest,
    activate_runtime_policy,
    rollback_runtime_policy,
)
from app.infrastructure.telephony.runtime_policy_adapter import (
    RuntimeAdapterConfig,
    RuntimePolicyAdapter,
)
from tests.unit.test_telephony_runtime_api import (  # noqa: F401  (fixture re-export)
    FakeRuntimeAdapter,
    FakeRuntimeConn,
    FakePool,
    _request,
    ws_g_context,
)


class _FilteringConn(FakeRuntimeConn):
    """Honours the build_status predicate the way Postgres would."""

    async def fetchrow(self, query: str, *args):
        normalized = " ".join(query.split())
        if "WHERE tenant_id = $1 AND policy_version = $2" in normalized and "build_status IN" in normalized:
            tenant_id, policy_version = args
            for row in self.versions.values():
                if (
                    row["tenant_id"] == tenant_id
                    and row["policy_version"] == int(policy_version)
                    and row["build_status"] in {"active", "superseded", "rolled_back"}
                ):
                    from uuid import UUID

                    return {
                        "id": UUID(row["id"]),
                        "policy_version": row["policy_version"],
                        "compiled_artifact": row["compiled_artifact"],
                    }
            return None
        return await super().fetchrow(query, *args)


async def _activate_two_versions(conn, pool, user, adapter):
    for note, key in (("v1", "bh-v1"), ("v2", "bh-v2")):
        if note == "v2":
            conn.routes[next(iter(conn.routes))]["priority"] = 50
        result = await activate_runtime_policy(
            payload=RuntimeActivateRequest(note=note),
            request=_request("/api/v1/telephony/sip/runtime/activate"),
            idempotency_key=key,
            x_request_id=f"req-{key}",
            current_user=user,
            db_pool=pool,
            adapter=adapter,
        )
        assert result["build_status"] == "active"


def _rollback(user, pool, adapter, *, target, key):
    return rollback_runtime_policy(
        payload=RuntimeRollbackRequest(target_version=target, reason="operator"),
        request=_request("/api/v1/telephony/sip/runtime/rollback"),
        idempotency_key=key,
        x_request_id=f"req-{key}",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", ["failed", "compiled"])
async def test_explicit_rollback_target_must_be_an_applied_version(ws_g_context, bad_status):
    _conn, _pool, user, adapter = ws_g_context
    conn = _FilteringConn(tenant_id=user.tenant_id)
    pool = FakePool(conn)
    await _activate_two_versions(conn, pool, user, adapter)
    target = next(row for row in conn.versions.values() if row["policy_version"] == 1)
    target["build_status"] = bad_status
    applies_before = adapter.apply_calls

    response = await _rollback(user, pool, adapter, target=1, key=f"bh-rb-{bad_status}")

    assert getattr(response, "status_code", None) == 409, response
    assert json.loads(response.body)["type"].endswith("runtime-rollback-target-not-found")
    assert adapter.apply_calls == applies_before
    active = [row["policy_version"] for row in conn.versions.values() if row["is_active"]]
    assert active == [2]
    assert target["build_status"] == bad_status and not target["is_active"]


@pytest.mark.asyncio
async def test_rollback_to_a_superseded_version_still_works(ws_g_context):
    _conn, _pool, user, adapter = ws_g_context
    conn = _FilteringConn(tenant_id=user.tenant_id)
    pool = FakePool(conn)
    await _activate_two_versions(conn, pool, user, adapter)

    result = await _rollback(user, pool, adapter, target=1, key="bh-rb-ok")

    assert result["to_version"] == 1 and result["from_version"] == 2


@pytest.mark.asyncio
async def test_rollback_response_says_it_applied_current_state_and_simulated(ws_g_context):
    _conn, _pool, user, _adapter = ws_g_context
    conn = _FilteringConn(tenant_id=user.tenant_id)
    pool = FakePool(conn)
    adapter = RuntimePolicyAdapter(RuntimeAdapterConfig(mode="simulate"))
    await _activate_two_versions(conn, pool, user, adapter)

    result = await _rollback(user, pool, adapter, target=1, key="bh-rb-sim")

    assert result["status"] == "rolled_back"
    assert result["simulated"] is True
    assert result["applied_from"] == "current_database_state"
