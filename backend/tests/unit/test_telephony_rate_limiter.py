from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pytest

from app.domain.services.telephony_rate_limiter import (
    RateLimitAction,
    TelephonyRateLimiter,
)


class FakeRedis:
    def __init__(self) -> None:
        self._values: Dict[str, str] = {}
        self._expires: Dict[str, float] = {}
        self.published: List[tuple[str, str]] = []

    def _expired(self, key: str) -> bool:
        expiry = self._expires.get(key)
        if expiry is None:
            return False
        if time.time() >= expiry:
            self._values.pop(key, None)
            self._expires.pop(key, None)
            return True
        return False

    async def incr(self, key: str) -> int:
        self._expired(key)
        value = int(self._values.get(key, "0")) + 1
        self._values[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self._values:
            return False
        self._expires[key] = time.time() + max(seconds, 0)
        return True

    async def ttl(self, key: str) -> int:
        if self._expired(key):
            return -2
        if key not in self._values:
            return -2
        expiry = self._expires.get(key)
        if expiry is None:
            return -1
        return max(int(expiry - time.time()), 0)

    async def get(self, key: str) -> Optional[str]:
        if self._expired(key):
            return None
        return self._values.get(key)

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        self._values[key] = value
        self._expires[key] = time.time() + max(seconds, 0)
        return True

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1


@dataclass
class _Policy:
    id: str
    tenant_id: str
    policy_name: str
    policy_scope: str
    metric_key: str
    window_seconds: int
    warn_threshold: int
    throttle_threshold: int
    block_threshold: int
    block_duration_seconds: int
    throttle_retry_seconds: int
    metadata: Dict[str, Any]
    is_active: bool = True


class FakeConn:
    def __init__(self) -> None:
        self.policies: List[_Policy] = []
        self.events: List[Dict[str, Any]] = []

    async def fetchrow(self, query: str, *args):
        normalized = " ".join(query.split())
        if "FROM tenant_telephony_threshold_policies" in normalized:
            tenant_id, scope, metric_key = args
            candidates = [
                policy
                for policy in self.policies
                if policy.tenant_id == tenant_id
                and policy.policy_scope == scope
                and policy.is_active
                and policy.metric_key in {metric_key, "*"}
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda p: (0 if p.metric_key == metric_key else 1, p.policy_name))
            policy = candidates[0]
            return {
                "id": policy.id,
                "policy_name": policy.policy_name,
                "policy_scope": policy.policy_scope,
                "metric_key": policy.metric_key,
                "window_seconds": policy.window_seconds,
                "warn_threshold": policy.warn_threshold,
                "throttle_threshold": policy.throttle_threshold,
                "block_threshold": policy.block_threshold,
                "block_duration_seconds": policy.block_duration_seconds,
                "throttle_retry_seconds": policy.throttle_retry_seconds,
                "metadata": policy.metadata,
            }
        raise AssertionError(f"Unexpected fetchrow query: {normalized}")

    async def fetch(self, query: str, *args):
        normalized = " ".join(query.split())
        if "FROM tenant_telephony_threshold_policies" in normalized:
            tenant_id = args[0]
            scope = args[1]
            metric_key = args[2] if len(args) > 2 else None
            rows = [
                {
                    "id": policy.id,
                    "policy_name": policy.policy_name,
                    "policy_scope": policy.policy_scope,
                    "metric_key": policy.metric_key,
                    "window_seconds": policy.window_seconds,
                    "warn_threshold": policy.warn_threshold,
                    "throttle_threshold": policy.throttle_threshold,
                    "block_threshold": policy.block_threshold,
                    "block_duration_seconds": policy.block_duration_seconds,
                    "throttle_retry_seconds": policy.throttle_retry_seconds,
                    "metadata": policy.metadata,
                }
                for policy in self.policies
                if policy.tenant_id == tenant_id
                and policy.policy_scope == scope
                and policy.is_active
                and (metric_key is None or policy.metric_key in {metric_key, "*"})
            ]
            rows.sort(key=lambda row: row["metric_key"])
            return rows
        raise AssertionError(f"Unexpected fetch query: {normalized}")

    async def execute(self, query: str, *args):
        normalized = " ".join(query.split())
        if normalized.startswith("INSERT INTO tenant_telephony_quota_events"):
            (
                tenant_id,
                policy_id,
                event_type,
                policy_scope,
                metric_key,
                counter_value,
                threshold_value,
                window_seconds,
                block_ttl_seconds,
                request_id,
                details_json,
                created_by,
            ) = args
            self.events.append(
                {
                    "tenant_id": tenant_id,
                    "policy_id": str(policy_id) if policy_id else None,
                    "event_type": event_type,
                    "policy_scope": policy_scope,
                    "metric_key": metric_key,
                    "counter_value": int(counter_value),
                    "threshold_value": int(threshold_value) if threshold_value is not None else None,
                    "window_seconds": int(window_seconds),
                    "block_ttl_seconds": int(block_ttl_seconds),
                    "request_id": request_id,
                    "details": json.loads(details_json),
                    "created_by": created_by,
                }
            )
            return "INSERT 1"
        raise AssertionError(f"Unexpected execute query: {normalized}")


@pytest.fixture
def limiter_ctx():
    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    conn = FakeConn()
    conn.policies.extend(
        [
            _Policy(
                id=str(uuid4()),
                tenant_id=tenant_a,
                policy_name="api-default",
                policy_scope="api_mutation",
                metric_key="*",
                window_seconds=60,
                warn_threshold=2,
                throttle_threshold=3,
                block_threshold=4,
                block_duration_seconds=120,
                throttle_retry_seconds=2,
                metadata={"tier": "default"},
            ),
            _Policy(
                id=str(uuid4()),
                tenant_id=tenant_b,
                policy_name="api-default",
                policy_scope="api_mutation",
                metric_key="*",
                window_seconds=60,
                warn_threshold=2,
                throttle_threshold=3,
                block_threshold=4,
                block_duration_seconds=120,
                throttle_retry_seconds=2,
                metadata={"tier": "default"},
            ),
        ]
    )
    redis = FakeRedis()
    limiter = TelephonyRateLimiter(redis_client=redis)
    return conn, limiter, redis, tenant_a, tenant_b


@pytest.mark.asyncio
async def test_limiter_progression_warn_throttle_block(limiter_ctx):
    conn, limiter, redis, tenant_a, _tenant_b = limiter_ctx
    metric = "sip_trunks:create"

    first = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
        request_id="req-1",
        created_by=str(uuid4()),
    )
    second = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
        request_id="req-2",
        created_by=str(uuid4()),
    )
    third = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
        request_id="req-3",
        created_by=str(uuid4()),
    )
    fourth = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
        request_id="req-4",
        created_by=str(uuid4()),
    )
    blocked = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
        request_id="req-5",
        created_by=str(uuid4()),
    )

    assert first.action == RateLimitAction.ALLOW
    assert second.action == RateLimitAction.WARN
    assert third.action == RateLimitAction.THROTTLE
    assert fourth.action == RateLimitAction.BLOCK
    assert blocked.action == RateLimitAction.BLOCK
    assert blocked.block_ttl_seconds > 0
    assert len(conn.events) == 4
    assert len(redis.published) == 4


@pytest.mark.asyncio
async def test_limiter_is_tenant_scoped(limiter_ctx):
    conn, limiter, _redis, tenant_a, tenant_b = limiter_ctx
    metric = "route_policies:update"

    # Tenant A reaches throttle.
    for _ in range(3):
        await limiter.evaluate(
            conn=conn,
            tenant_id=tenant_a,
            policy_scope="api_mutation",
            metric_key=metric,
        )

    # Tenant B remains independent at first request.
    decision_b = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_b,
        policy_scope="api_mutation",
        metric_key=metric,
    )
    assert decision_b.counter_value == 1
    assert decision_b.action == RateLimitAction.ALLOW


@pytest.mark.asyncio
async def test_limiter_status_returns_policy_metrics(limiter_ctx):
    conn, limiter, _redis, tenant_a, _tenant_b = limiter_ctx
    metric = "codec_policies:create"

    await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
    )
    await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
    )

    statuses = await limiter.get_status(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key=metric,
    )
    assert len(statuses) >= 1
    assert statuses[0].counter_value >= 2
    assert statuses[0].current_action in {
        RateLimitAction.WARN,
        RateLimitAction.THROTTLE,
        RateLimitAction.BLOCK,
        RateLimitAction.ALLOW,
    }


@pytest.mark.asyncio
async def test_limiter_allows_when_redis_unavailable(limiter_ctx):
    conn, _limiter, _redis, tenant_a, _tenant_b = limiter_ctx
    limiter = TelephonyRateLimiter(redis_client=None)

    decision = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_a,
        policy_scope="api_mutation",
        metric_key="sip_trunks:update",
    )

    assert decision.action == RateLimitAction.ALLOW
    assert decision.reason == "redis_unavailable"
