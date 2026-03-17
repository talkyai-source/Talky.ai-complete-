from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import pytest

from app.domain.services.telephony_concurrency_limiter import (
    LeaseKind,
    TelephonyConcurrencyLimiter,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: Dict[str, str] = {}

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, _key: str, _seconds: int) -> bool:
        return True

    async def get(self, key: str) -> Optional[str]:
        return self.values.get(key)

    async def setex(self, key: str, _seconds: int, value: str) -> bool:
        self.values[key] = value
        return True


@dataclass
class _Policy:
    id: UUID
    tenant_id: str
    policy_name: str
    max_active_calls: int
    max_transfer_inflight: int
    lease_ttl_seconds: int
    heartbeat_grace_seconds: int
    metadata: Dict[str, Any]
    is_active: bool = True
    updated_at: datetime = datetime.now(timezone.utc)


@dataclass
class _Lease:
    id: UUID
    tenant_id: str
    policy_id: Optional[UUID]
    call_id: UUID
    talklee_call_id: str
    lease_kind: str
    state: str
    acquired_at: datetime
    last_heartbeat_at: datetime
    released_at: Optional[datetime]
    release_reason: Optional[str]


class FakeConn:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.policies: List[_Policy] = []
        self.leases: List[_Lease] = []
        self.events: List[Dict[str, Any]] = []

    async def execute(self, query: str, *args):
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            return "SELECT 1"
        if normalized.startswith("INSERT INTO tenant_telephony_concurrency_events"):
            (
                tenant_id,
                policy_id,
                lease_id,
                event_type,
                lease_kind,
                call_id,
                talklee_call_id,
                details_json,
                request_id,
                created_by,
            ) = args
            self.events.append(
                {
                    "tenant_id": tenant_id,
                    "policy_id": str(policy_id) if policy_id else None,
                    "lease_id": str(lease_id) if lease_id else None,
                    "event_type": event_type,
                    "lease_kind": lease_kind,
                    "call_id": call_id,
                    "talklee_call_id": talklee_call_id,
                    "details": details_json,
                    "request_id": request_id,
                    "created_by": created_by,
                }
            )
            return "INSERT 1"
        raise AssertionError(f"Unexpected execute query: {normalized}")

    async def fetchrow(self, query: str, *args):
        normalized = " ".join(query.split())
        if "FROM tenant_telephony_concurrency_policies" in normalized:
            tenant_id = args[0]
            rows = [p for p in self.policies if p.tenant_id == tenant_id and p.is_active]
            if not rows:
                return None
            rows.sort(key=lambda p: p.updated_at, reverse=True)
            policy = rows[0]
            return {
                "id": policy.id,
                "policy_name": policy.policy_name,
                "max_active_calls": policy.max_active_calls,
                "max_transfer_inflight": policy.max_transfer_inflight,
                "lease_ttl_seconds": policy.lease_ttl_seconds,
                "heartbeat_grace_seconds": policy.heartbeat_grace_seconds,
                "metadata": policy.metadata,
            }

        if "COUNT(*) FILTER" in normalized and "FROM tenant_telephony_concurrency_leases" in normalized:
            tenant_id = args[0]
            active_calls = 0
            active_transfers = 0
            for lease in self.leases:
                if lease.tenant_id != tenant_id:
                    continue
                if lease.released_at is not None:
                    continue
                if lease.state not in {"active", "releasing"}:
                    continue
                if lease.lease_kind == "call":
                    active_calls += 1
                elif lease.lease_kind == "transfer":
                    active_transfers += 1
            return {"active_calls": active_calls, "active_transfers": active_transfers}

        if normalized.startswith("INSERT INTO tenant_telephony_concurrency_leases"):
            (
                tenant_id,
                policy_id,
                call_id,
                talklee_call_id,
                lease_kind,
                _metadata_json,
                _created_by,
                _updated_by,
            ) = args
            for lease in self.leases:
                if (
                    lease.tenant_id == tenant_id
                    and str(lease.call_id) == str(call_id)
                    and lease.lease_kind == lease_kind
                    and lease.released_at is None
                    and lease.state in {"active", "releasing"}
                ):
                    lease.last_heartbeat_at = datetime.now(timezone.utc)
                    return {"id": lease.id}
            lease_id = uuid4()
            self.leases.append(
                _Lease(
                    id=lease_id,
                    tenant_id=tenant_id,
                    policy_id=policy_id,
                    call_id=UUID(str(call_id)),
                    talklee_call_id=str(talklee_call_id),
                    lease_kind=str(lease_kind),
                    state="active",
                    acquired_at=datetime.now(timezone.utc),
                    last_heartbeat_at=datetime.now(timezone.utc),
                    released_at=None,
                    release_reason=None,
                )
            )
            return {"id": lease_id}

        if normalized.startswith("UPDATE tenant_telephony_concurrency_leases SET state = 'released'"):
            tenant_id, lease_id, reason, _updated_by = args
            for lease in self.leases:
                if lease.tenant_id == tenant_id and str(lease.id) == str(lease_id) and lease.released_at is None:
                    lease.state = "released"
                    lease.released_at = datetime.now(timezone.utc)
                    lease.release_reason = str(reason)
                    return {
                        "id": lease.id,
                        "policy_id": lease.policy_id,
                        "call_id": lease.call_id,
                        "talklee_call_id": lease.talklee_call_id,
                        "lease_kind": lease.lease_kind,
                    }
            return None

        if normalized.startswith("UPDATE tenant_telephony_concurrency_leases SET last_heartbeat_at = NOW()"):
            tenant_id, lease_id, _updated_by = args
            for lease in self.leases:
                if lease.tenant_id == tenant_id and str(lease.id) == str(lease_id) and lease.released_at is None:
                    lease.last_heartbeat_at = datetime.now(timezone.utc)
                    return {
                        "id": lease.id,
                        "policy_id": lease.policy_id,
                        "call_id": lease.call_id,
                        "talklee_call_id": lease.talklee_call_id,
                        "lease_kind": lease.lease_kind,
                    }
            return None

        raise AssertionError(f"Unexpected fetchrow query: {normalized}")

    async def fetch(self, query: str, *args):
        normalized = " ".join(query.split())
        if normalized.startswith("UPDATE tenant_telephony_concurrency_leases SET state = 'expired'"):
            tenant_id, ttl_with_grace, _updated_by = args
            threshold = datetime.now(timezone.utc) - timedelta(seconds=int(ttl_with_grace))
            expired_rows = []
            for lease in self.leases:
                if lease.tenant_id != tenant_id:
                    continue
                if lease.released_at is not None:
                    continue
                if lease.state not in {"active", "releasing"}:
                    continue
                if lease.last_heartbeat_at >= threshold:
                    continue
                lease.state = "expired"
                lease.released_at = datetime.now(timezone.utc)
                lease.release_reason = "lease_ttl_expired"
                expired_rows.append(
                    {
                        "id": lease.id,
                        "policy_id": lease.policy_id,
                        "call_id": lease.call_id,
                        "talklee_call_id": lease.talklee_call_id,
                        "lease_kind": lease.lease_kind,
                    }
                )
            return expired_rows
        raise AssertionError(f"Unexpected fetch query: {normalized}")


@pytest.fixture
def limiter_ctx():
    tenant_id = str(uuid4())
    conn = FakeConn(tenant_id=tenant_id)
    conn.policies.append(
        _Policy(
            id=uuid4(),
            tenant_id=tenant_id,
            policy_name="runtime-default",
            max_active_calls=1,
            max_transfer_inflight=1,
            lease_ttl_seconds=30,
            heartbeat_grace_seconds=5,
            metadata={"seeded": True},
        )
    )
    redis = FakeRedis()
    limiter = TelephonyConcurrencyLimiter(redis_client=redis)
    return conn, limiter, redis, tenant_id


@pytest.mark.asyncio
async def test_acquire_rejects_when_active_call_limit_reached(limiter_ctx):
    conn, limiter, _redis, tenant_id = limiter_ctx
    first = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_a",
        lease_kind=LeaseKind.CALL,
    )
    second = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_b",
        lease_kind=LeaseKind.CALL,
    )
    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "max_active_calls_reached"


@pytest.mark.asyncio
async def test_transfer_limit_is_enforced(limiter_ctx):
    conn, limiter, _redis, tenant_id = limiter_ctx
    a = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_t1",
        lease_kind=LeaseKind.TRANSFER,
    )
    b = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_t2",
        lease_kind=LeaseKind.TRANSFER,
    )
    assert a.accepted is True
    assert b.accepted is False
    assert b.reason == "max_transfer_inflight_reached"


@pytest.mark.asyncio
async def test_release_allows_new_lease(limiter_ctx):
    conn, limiter, _redis, tenant_id = limiter_ctx
    first = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_rel_1",
        lease_kind=LeaseKind.CALL,
    )
    assert first.accepted is True
    assert first.lease_id is not None

    released = await limiter.release_lease(
        conn,
        tenant_id=tenant_id,
        lease_id=first.lease_id,
        reason="transfer_completed",
    )
    assert released is True

    second = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_rel_2",
        lease_kind=LeaseKind.CALL,
    )
    assert second.accepted is True


@pytest.mark.asyncio
async def test_heartbeat_and_expire_flow(limiter_ctx):
    conn, limiter, _redis, tenant_id = limiter_ctx
    decision = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_hb_1",
        lease_kind=LeaseKind.CALL,
    )
    assert decision.accepted is True
    assert decision.lease_id is not None

    hb = await limiter.heartbeat_lease(
        conn,
        tenant_id=tenant_id,
        lease_id=decision.lease_id,
    )
    assert hb is True

    # Force stale heartbeat and expire.
    for lease in conn.leases:
        if lease.id == decision.lease_id:
            lease.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    expired = await limiter.expire_stale_leases(conn, tenant_id=tenant_id)
    assert expired == 1


@pytest.mark.asyncio
async def test_status_returns_policy_and_counts(limiter_ctx):
    conn, limiter, _redis, tenant_id = limiter_ctx
    await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_status_1",
        lease_kind=LeaseKind.CALL,
    )
    status = await limiter.get_status(conn, tenant_id=tenant_id)
    assert status["tenant_id"] == tenant_id
    assert status["active_calls"] == 1
    assert status["max_active_calls"] == 1
