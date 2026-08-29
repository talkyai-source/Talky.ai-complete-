from __future__ import annotations

import asyncio
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

    async def decr(self, key: str) -> int:
        # Redis DECR on a missing key treats it as 0 and stores -1.
        value = int(self.values.get(key, "0")) - 1
        self.values[key] = str(value)
        return value

    async def expire(self, _key: str, _seconds: int) -> bool:
        return True

    async def get(self, key: str) -> Optional[str]:
        return self.values.get(key)

    async def setex(self, key: str, _seconds: int, value: str) -> bool:
        self.values[key] = value
        return True


class RacyRedis(FakeRedis):
    """Models real Redis concurrency: every command is atomic on the server,
    but the client awaits between commands, so another coroutine can run in
    the gap. A GET-then-SETEX read-modify-write therefore loses concurrent
    updates; a single DECR cannot."""

    async def incr(self, key: str) -> int:
        await asyncio.sleep(0)
        return await FakeRedis.incr(self, key)

    async def decr(self, key: str) -> int:
        await asyncio.sleep(0)
        return await FakeRedis.decr(self, key)

    async def get(self, key: str) -> Optional[str]:
        await asyncio.sleep(0)
        return self.values.get(key)

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        await asyncio.sleep(0)
        return await FakeRedis.setex(self, key, seconds, value)


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


@dataclass
class _Call:
    id: UUID
    tenant_id: str
    direction: str
    billing_status: str


class FakeConn:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.policies: List[_Policy] = []
        self.leases: List[_Lease] = []
        self.calls: List[_Call] = []
        self.events: List[Dict[str, Any]] = []

    def _has_unresolved_inbound_call(self, lease: _Lease) -> bool:
        return any(
            call.id == lease.call_id
            and call.tenant_id == lease.tenant_id
            and call.direction == "inbound"
            and call.billing_status in {"reserved", "held"}
            for call in self.calls
        )

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

        if (
            "COUNT(*) FILTER" in normalized
            and "FROM tenant_telephony_concurrency_leases" in normalized
        ):
            tenant_id = args[0]
            active_calls = 0
            active_transfers = 0
            for lease in self.leases:
                if lease.tenant_id != tenant_id:
                    continue
                normally_active = lease.released_at is None and lease.state in {
                    "active",
                    "releasing",
                }
                unresolved_inbound = lease.state in {
                    "expired",
                    "releasing",
                } and self._has_unresolved_inbound_call(lease)
                if lease.lease_kind == "call" and (normally_active or unresolved_inbound):
                    active_calls += 1
                elif lease.lease_kind == "transfer" and normally_active:
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

        if normalized.startswith(
            "UPDATE tenant_telephony_concurrency_leases SET state = 'released'"
        ):
            tenant_id, lease_id, reason, _updated_by = args
            for lease in self.leases:
                if (
                    lease.tenant_id == tenant_id
                    and str(lease.id) == str(lease_id)
                    and lease.released_at is None
                ):
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

        if normalized.startswith(
            "UPDATE tenant_telephony_concurrency_leases SET last_heartbeat_at = NOW()"
        ):
            tenant_id, lease_id, _updated_by = args
            for lease in self.leases:
                if (
                    lease.tenant_id == tenant_id
                    and str(lease.id) == str(lease_id)
                    and lease.released_at is None
                ):
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
        if normalized.startswith(
            "UPDATE tenant_telephony_concurrency_leases SET state = 'expired'"
        ):
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
                if lease.lease_kind == "call" and self._has_unresolved_inbound_call(lease):
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
            lease_ttl_seconds=60,
            heartbeat_grace_seconds=30,
            metadata={"seeded": True},
        )
    )
    redis = FakeRedis()
    limiter = TelephonyConcurrencyLimiter(redis_client=redis)
    return conn, limiter, redis, tenant_id


@pytest.mark.asyncio
async def test_unsafe_policy_window_fails_closed_before_lease_acquisition():
    tenant_id = str(uuid4())
    conn = FakeConn(tenant_id=tenant_id)
    conn.policies.append(
        _Policy(
            id=uuid4(),
            tenant_id=tenant_id,
            policy_name="unsafe-window",
            max_active_calls=10,
            max_transfer_inflight=2,
            lease_ttl_seconds=10,
            heartbeat_grace_seconds=5,
            metadata={},
        )
    )

    with pytest.raises(RuntimeError, match="at least 90 seconds"):
        await TelephonyConcurrencyLimiter().acquire_lease(
            conn,
            tenant_id=tenant_id,
            call_id=str(uuid4()),
            talklee_call_id="unsafe",
            lease_kind=LeaseKind.CALL,
        )
    assert conn.leases == []


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
@pytest.mark.parametrize("billing_status", ["reserved", "held"])
async def test_stale_unresolved_inbound_call_blocks_until_confirmed_release(
    limiter_ctx,
    billing_status,
):
    conn, limiter, redis, tenant_id = limiter_ctx
    call_id = uuid4()
    decision = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(call_id),
        talklee_call_id="tlk_unresolved",
        lease_kind=LeaseKind.CALL,
    )
    assert decision.accepted is True
    assert decision.lease_id is not None
    conn.calls.append(
        _Call(
            id=call_id,
            tenant_id=tenant_id,
            direction="inbound",
            billing_status=billing_status,
        )
    )
    lease = next(item for item in conn.leases if item.id == decision.lease_id)
    lease.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    # Heartbeat expiry is not PBX absence proof. The reaper retains the lease,
    # so a replacement call cannot exceed this tenant's configured capacity.
    assert await limiter.expire_stale_leases(conn, tenant_id=tenant_id) == 0
    assert lease.state == "active"
    assert lease.released_at is None
    assert _redis_count(redis, tenant_id) == 1

    blocked = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_replacement_blocked",
        lease_kind=LeaseKind.CALL,
    )
    assert blocked.accepted is False
    assert blocked.reason == "max_active_calls_reached"

    # The proof-aware finalization path explicitly releases the original
    # lease after confirming termination. Only then may a replacement enter.
    assert (
        await limiter.release_lease(
            conn,
            tenant_id=tenant_id,
            lease_id=decision.lease_id,
            reason="confirmed_call_termination",
        )
        is True
    )
    replacement = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_replacement_allowed",
        lease_kind=LeaseKind.CALL,
    )
    assert replacement.accepted is True


@pytest.mark.asyncio
async def test_legacy_expired_lease_with_unresolved_inbound_call_still_counts(
    limiter_ctx,
):
    conn, limiter, _redis, tenant_id = limiter_ctx
    call_id = uuid4()
    decision = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(call_id),
        talklee_call_id="tlk_legacy_expired",
        lease_kind=LeaseKind.CALL,
    )
    conn.calls.append(
        _Call(
            id=call_id,
            tenant_id=tenant_id,
            direction="inbound",
            billing_status="reserved",
        )
    )
    lease = next(item for item in conn.leases if item.id == decision.lease_id)
    lease.state = "expired"
    lease.released_at = datetime.now(timezone.utc)
    lease.release_reason = "lease_ttl_expired"

    blocked = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_blocked_by_legacy_expiry",
        lease_kind=LeaseKind.CALL,
    )
    assert blocked.accepted is False
    assert blocked.active_calls == 1


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


# ---------------------------------------------------------------------------
# FIX #12 — self-healing stale-lease reap
#
# Before this fix, _active_counts counted every lease with
# state IN ('active', 'releasing') AND released_at IS NULL, with no
# TTL/heartbeat filter. acquire_lease never expired stale rows itself, and
# expire_stale_leases (which DOES do the TTL+grace reap) was only reachable
# via a manual admin endpoint. A crashed lease holder — one that acquired a
# lease and then died without calling release_lease or heartbeat_lease —
# would sit in the active count FOREVER, permanently shrinking that
# tenant's concurrency capacity. These tests pin the orphan case:
# acquire_lease and get_status call expire_stale_leases before counting. The
# reserved/held inbound-call case is intentionally covered separately above,
# because heartbeat expiry is not proof that its PBX channel is absent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_lease_self_heals_stale_lease_before_counting(limiter_ctx):
    conn, limiter, _redis, tenant_id = limiter_ctx  # policy: max_active_calls=1

    crashed = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_crashed",
        lease_kind=LeaseKind.CALL,
    )
    assert crashed.accepted is True

    # Simulate the holder crashing: it never releases and never heartbeats
    # again. ttl_with_grace for the seeded policy is 30 + 5 = 35s.
    for lease in conn.leases:
        if lease.id == crashed.lease_id:
            lease.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    # Without the fix this would be rejected: max_active_calls=1 and the
    # crashed lease is still counted as active.
    second = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_after_crash",
        lease_kind=LeaseKind.CALL,
    )
    assert second.accepted is True
    assert second.reason == "lease_acquired"

    # The crashed lease was actually reaped (not just skipped in counting).
    crashed_row = next(lease for lease in conn.leases if lease.id == crashed.lease_id)
    assert crashed_row.state == "expired"
    assert crashed_row.release_reason == "lease_ttl_expired"


@pytest.mark.asyncio
async def test_acquire_lease_still_rejects_when_holder_is_genuinely_alive(limiter_ctx):
    """Guard against the self-heal being too aggressive: a lease that IS
    heartbeating within the TTL+grace window must still count and still
    enforce the cap."""
    conn, limiter, _redis, tenant_id = limiter_ctx  # max_active_calls=1

    alive = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_alive",
        lease_kind=LeaseKind.CALL,
    )
    assert alive.accepted is True

    second = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_should_reject",
        lease_kind=LeaseKind.CALL,
    )
    assert second.accepted is False
    assert second.reason == "max_active_calls_reached"

    alive_row = next(lease for lease in conn.leases if lease.id == alive.lease_id)
    assert alive_row.state == "active"


@pytest.mark.asyncio
async def test_get_status_self_heals_stale_lease(limiter_ctx):
    conn, limiter, _redis, tenant_id = limiter_ctx

    crashed = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_status_crashed",
        lease_kind=LeaseKind.CALL,
    )
    assert crashed.accepted is True

    for lease in conn.leases:
        if lease.id == crashed.lease_id:
            lease.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    status = await limiter.get_status(conn, tenant_id=tenant_id)

    # Without the fix this would report 1 — a crashed holder inflating the
    # tenant's reported active-call count forever.
    assert status["active_calls"] == 0


# ---------------------------------------------------------------------------
# Redis mirror over-count
#
# The Redis key telephony:concurrency:active:{tenant}:{kind} mirrors the DB
# active-lease count. Two defects made it drift UPWARDS and never recover:
#
#   (1) acquire_lease incremented unconditionally, but its INSERT is
#       ON CONFLICT DO UPDATE — a re-acquire of a call that already holds a
#       lease creates no row and leaves the DB count unchanged, yet still
#       bumped Redis. Only release/expire decrement, once per lease row, so
#       the surplus was permanent.
#   (2) _redis_decrement was a GET / SETEX read-modify-write, so two
#       concurrent decrements both read N and both wrote N-1 — one lost.
# ---------------------------------------------------------------------------


def _redis_count(redis: FakeRedis, tenant_id: str, kind: LeaseKind = LeaseKind.CALL) -> int:
    return int(redis.values.get(f"telephony:concurrency:active:{tenant_id}:{kind.value}", "0"))


def _ctx(max_active_calls: int = 1, redis: Optional[FakeRedis] = None):
    tenant_id = str(uuid4())
    conn = FakeConn(tenant_id=tenant_id)
    conn.policies.append(
        _Policy(
            id=uuid4(),
            tenant_id=tenant_id,
            policy_name="runtime-default",
            max_active_calls=max_active_calls,
            max_transfer_inflight=2,
            lease_ttl_seconds=60,
            heartbeat_grace_seconds=30,
            metadata={},
        )
    )
    redis = redis or FakeRedis()
    return conn, TelephonyConcurrencyLimiter(redis_client=redis), redis, tenant_id


@pytest.mark.asyncio
async def test_reacquiring_same_call_does_not_double_count_in_redis():
    """Defect (1): the ON CONFLICT DO UPDATE path returns the EXISTING lease.
    No lease was created, so Redis must not be incremented.

    (The cap is 2 here because the limit check runs before the INSERT: at
    max_active_calls=1 a re-acquire is rejected outright and never reaches the
    conflict path at all.)"""
    conn, limiter, redis, tenant_id = _ctx(max_active_calls=2)
    call_id = str(uuid4())

    first = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=call_id,
        talklee_call_id="tlk_dup",
        lease_kind=LeaseKind.CALL,
    )
    assert first.accepted is True
    assert _redis_count(redis, tenant_id) == 1

    # Same call re-acquires three more times (retry / duplicate webhook).
    for _ in range(3):
        again = await limiter.acquire_lease(
            conn,
            tenant_id=tenant_id,
            call_id=call_id,
            talklee_call_id="tlk_dup",
            lease_kind=LeaseKind.CALL,
        )
        assert again.accepted is True
        assert again.lease_id == first.lease_id  # existing lease, not a new one

    # Exactly one lease row exists, so the mirror must read 1 (was 4).
    assert len([le for le in conn.leases if le.released_at is None]) == 1
    assert _redis_count(redis, tenant_id) == 1
    assert first.active_calls == 1


@pytest.mark.asyncio
async def test_redis_count_returns_to_zero_after_reacquire_then_release():
    """The end state that matters: after the single lease is released the
    mirror is 0, not stuck at the number of acquire attempts."""
    conn, limiter, redis, tenant_id = _ctx(max_active_calls=2)
    call_id = str(uuid4())

    for _ in range(3):
        decision = await limiter.acquire_lease(
            conn,
            tenant_id=tenant_id,
            call_id=call_id,
            talklee_call_id="tlk_dup_rel",
            lease_kind=LeaseKind.CALL,
        )
        assert decision.accepted is True

    assert (
        await limiter.release_lease(
            conn, tenant_id=tenant_id, lease_id=decision.lease_id, reason="completed"
        )
        is True
    )

    assert _redis_count(redis, tenant_id) == 0


@pytest.mark.asyncio
async def test_rejected_acquire_does_not_touch_redis(limiter_ctx):
    conn, limiter, redis, tenant_id = limiter_ctx  # max_active_calls=1

    await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_first",
        lease_kind=LeaseKind.CALL,
    )
    rejected = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_rejected",
        lease_kind=LeaseKind.CALL,
    )
    assert rejected.accepted is False
    assert _redis_count(redis, tenant_id) == 1


@pytest.mark.asyncio
async def test_distinct_calls_still_increment_independently():
    """Guard against the fix being too aggressive: genuinely new leases must
    still be counted."""
    conn, limiter, redis, tenant_id = _ctx(max_active_calls=3)

    for i in range(3):
        decision = await limiter.acquire_lease(
            conn,
            tenant_id=tenant_id,
            call_id=str(uuid4()),
            talklee_call_id=f"tlk_{i}",
            lease_kind=LeaseKind.CALL,
        )
        assert decision.accepted is True

    assert _redis_count(redis, tenant_id) == 3

    # A transfer lease counts on its own key, not the call one.
    await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_transfer",
        lease_kind=LeaseKind.TRANSFER,
    )
    assert _redis_count(redis, tenant_id) == 3
    assert _redis_count(redis, tenant_id, LeaseKind.TRANSFER) == 1


@pytest.mark.asyncio
async def test_concurrent_releases_do_not_lose_a_decrement():
    """Defect (2): with the old GET / SETEX read-modify-write both coroutines
    read 2 and both wrote 1, leaving the mirror one above the truth forever."""
    conn, limiter, redis, tenant_id = _ctx(max_active_calls=2, redis=RacyRedis())

    first = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_race_a",
        lease_kind=LeaseKind.CALL,
    )
    second = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_race_b",
        lease_kind=LeaseKind.CALL,
    )
    assert _redis_count(redis, tenant_id) == 2

    await asyncio.gather(
        limiter.release_lease(conn, tenant_id=tenant_id, lease_id=first.lease_id, reason="done"),
        limiter.release_lease(conn, tenant_id=tenant_id, lease_id=second.lease_id, reason="done"),
    )

    assert _redis_count(redis, tenant_id) == 0


@pytest.mark.asyncio
async def test_decrement_clamps_at_zero_on_missing_key():
    """DECR on an expired key stores -1; a negative mirror would swallow the
    next real increment and under-count."""
    _conn, limiter, redis, tenant_id = _ctx()

    await limiter._redis_decrement(tenant_id, LeaseKind.CALL)
    assert _redis_count(redis, tenant_id) == 0

    await limiter._redis_increment(tenant_id, LeaseKind.CALL)
    assert _redis_count(redis, tenant_id) == 1


@pytest.mark.asyncio
async def test_expired_lease_decrements_redis(limiter_ctx):
    """The stale-lease reaper gives the slot back on the mirror too."""
    conn, limiter, redis, tenant_id = limiter_ctx

    decision = await limiter.acquire_lease(
        conn,
        tenant_id=tenant_id,
        call_id=str(uuid4()),
        talklee_call_id="tlk_expire_redis",
        lease_kind=LeaseKind.CALL,
    )
    assert _redis_count(redis, tenant_id) == 1

    for lease in conn.leases:
        if lease.id == decision.lease_id:
            lease.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    assert await limiter.expire_stale_leases(conn, tenant_id=tenant_id) == 1
    assert _redis_count(redis, tenant_id) == 0
