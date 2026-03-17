"""
WS-I telephony quota and abuse limiter.

Implements tenant-scoped, Redis-atomic counters using INCR + EXPIRE and
graduated actions (warn, throttle, block). Threshold policies are loaded
from PostgreSQL and events are persisted for auditability.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9:_-]+")
_ALERT_CHANNEL = "telephony:quota_alerts"


class RateLimitAction(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    THROTTLE = "throttle"
    BLOCK = "block"


@dataclass(frozen=True)
class ThresholdPolicy:
    policy_id: Optional[UUID]
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


@dataclass(frozen=True)
class RateLimitDecision:
    action: RateLimitAction
    policy: ThresholdPolicy
    counter_value: int
    threshold_value: Optional[int]
    window_ttl_seconds: int
    block_ttl_seconds: int
    reason: str


@dataclass(frozen=True)
class PolicyStatus:
    policy_id: Optional[str]
    policy_name: str
    policy_scope: str
    metric_key: str
    window_seconds: int
    warn_threshold: int
    throttle_threshold: int
    block_threshold: int
    block_duration_seconds: int
    throttle_retry_seconds: int
    counter_value: int
    window_ttl_seconds: int
    block_ttl_seconds: int
    current_action: RateLimitAction
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_name": self.policy_name,
            "policy_scope": self.policy_scope,
            "metric_key": self.metric_key,
            "window_seconds": self.window_seconds,
            "warn_threshold": self.warn_threshold,
            "throttle_threshold": self.throttle_threshold,
            "block_threshold": self.block_threshold,
            "block_duration_seconds": self.block_duration_seconds,
            "throttle_retry_seconds": self.throttle_retry_seconds,
            "counter_value": self.counter_value,
            "window_ttl_seconds": self.window_ttl_seconds,
            "block_ttl_seconds": self.block_ttl_seconds,
            "current_action": self.current_action.value,
            "metadata": self.metadata,
        }


class TelephonyRateLimiter:
    """
    Tenant-scoped rate limiter for telephony control-plane and runtime actions.
    """

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client

    @staticmethod
    def _default_policy(policy_scope: str, metric_key: str) -> ThresholdPolicy:
        return ThresholdPolicy(
            policy_id=None,
            policy_name="default",
            policy_scope=policy_scope,
            metric_key=metric_key,
            window_seconds=60,
            warn_threshold=20,
            throttle_threshold=30,
            block_threshold=45,
            block_duration_seconds=300,
            throttle_retry_seconds=2,
            metadata={"source": "default"},
        )

    @staticmethod
    def _normalize_key_part(value: str, *, max_len: int = 72) -> str:
        cleaned = _SAFE_KEY_RE.sub("-", value.strip().lower())
        if not cleaned:
            cleaned = "default"
        cleaned = cleaned[:max_len]
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
        return f"{cleaned}:{digest}"

    def _counter_key(self, tenant_id: str, policy_scope: str, metric_key: str, window_bucket: int) -> str:
        tenant_part = self._normalize_key_part(tenant_id, max_len=36)
        scope_part = self._normalize_key_part(policy_scope, max_len=32)
        metric_part = self._normalize_key_part(metric_key, max_len=64)
        return f"telephony:quota:count:{tenant_part}:{scope_part}:{metric_part}:{window_bucket}"

    def _block_key(self, tenant_id: str, policy_scope: str, metric_key: str) -> str:
        tenant_part = self._normalize_key_part(tenant_id, max_len=36)
        scope_part = self._normalize_key_part(policy_scope, max_len=32)
        metric_part = self._normalize_key_part(metric_key, max_len=64)
        return f"telephony:quota:block:{tenant_part}:{scope_part}:{metric_part}"

    async def _load_policy(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        policy_scope: str,
        metric_key: str,
    ) -> ThresholdPolicy:
        row = await conn.fetchrow(
            """
            SELECT
                id,
                policy_name,
                policy_scope,
                metric_key,
                window_seconds,
                warn_threshold,
                throttle_threshold,
                block_threshold,
                block_duration_seconds,
                throttle_retry_seconds,
                metadata
            FROM tenant_telephony_threshold_policies
            WHERE tenant_id = $1
              AND policy_scope = $2
              AND is_active = TRUE
              AND (metric_key = $3 OR metric_key = '*')
            ORDER BY
                CASE WHEN metric_key = $3 THEN 0 ELSE 1 END,
                updated_at DESC
            LIMIT 1
            """,
            tenant_id,
            policy_scope,
            metric_key,
        )
        if not row:
            return self._default_policy(policy_scope, metric_key)

        return ThresholdPolicy(
            policy_id=row["id"],
            policy_name=row["policy_name"],
            policy_scope=row["policy_scope"],
            metric_key=row["metric_key"],
            window_seconds=int(row["window_seconds"]),
            warn_threshold=int(row["warn_threshold"]),
            throttle_threshold=int(row["throttle_threshold"]),
            block_threshold=int(row["block_threshold"]),
            block_duration_seconds=int(row["block_duration_seconds"]),
            throttle_retry_seconds=int(row["throttle_retry_seconds"]),
            metadata=row["metadata"] or {},
        )

    async def _ttl(self, key: str) -> int:
        if not self._redis:
            return 0
        ttl_val = await self._redis.ttl(key)
        if ttl_val is None:
            return 0
        try:
            return max(int(ttl_val), 0)
        except (TypeError, ValueError):
            return 0

    async def _record_event(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        decision: RateLimitDecision,
        request_id: Optional[str],
        created_by: Optional[str],
        details: Optional[Dict[str, Any]],
    ) -> None:
        payload = {
            "reason": decision.reason,
            "policy_name": decision.policy.policy_name,
            "policy_scope": decision.policy.policy_scope,
            "metric_key": decision.policy.metric_key,
            "counter_value": decision.counter_value,
            "window_ttl_seconds": decision.window_ttl_seconds,
            "block_ttl_seconds": decision.block_ttl_seconds,
            "metadata": decision.policy.metadata,
            "details": details or {},
        }
        await conn.execute(
            """
            INSERT INTO tenant_telephony_quota_events (
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
                details,
                created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12)
            """,
            tenant_id,
            decision.policy.policy_id,
            decision.action.value,
            decision.policy.policy_scope,
            decision.policy.metric_key,
            decision.counter_value,
            decision.threshold_value,
            decision.policy.window_seconds,
            decision.block_ttl_seconds,
            request_id,
            json.dumps(payload),
            created_by,
        )

    async def _publish_alert(
        self,
        *,
        tenant_id: str,
        decision: RateLimitDecision,
        request_id: Optional[str],
    ) -> None:
        if not self._redis:
            return
        payload = {
            "type": "telephony_quota_alert",
            "tenant_id": tenant_id,
            "policy_id": str(decision.policy.policy_id) if decision.policy.policy_id else None,
            "policy_name": decision.policy.policy_name,
            "policy_scope": decision.policy.policy_scope,
            "metric_key": decision.policy.metric_key,
            "action": decision.action.value,
            "counter_value": decision.counter_value,
            "threshold_value": decision.threshold_value,
            "window_seconds": decision.policy.window_seconds,
            "window_ttl_seconds": decision.window_ttl_seconds,
            "block_ttl_seconds": decision.block_ttl_seconds,
            "request_id": request_id,
            "ts_epoch": int(time.time()),
        }
        try:
            await self._redis.publish(_ALERT_CHANNEL, json.dumps(payload))
        except Exception:
            logger.warning("Failed to publish telephony quota alert event", exc_info=True)

    async def evaluate(
        self,
        *,
        conn: asyncpg.Connection,
        tenant_id: str,
        policy_scope: str,
        metric_key: str,
        request_id: Optional[str] = None,
        created_by: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> RateLimitDecision:
        policy = await self._load_policy(
            conn,
            tenant_id=tenant_id,
            policy_scope=policy_scope,
            metric_key=metric_key,
        )

        if not self._redis:
            return RateLimitDecision(
                action=RateLimitAction.ALLOW,
                policy=policy,
                counter_value=0,
                threshold_value=None,
                window_ttl_seconds=0,
                block_ttl_seconds=0,
                reason="redis_unavailable",
            )

        block_key = self._block_key(tenant_id, policy_scope, metric_key)
        block_ttl = await self._ttl(block_key)
        if block_ttl > 0:
            blocked_counter_raw = await self._redis.get(block_key)
            blocked_counter = int(blocked_counter_raw or 0)
            decision = RateLimitDecision(
                action=RateLimitAction.BLOCK,
                policy=policy,
                counter_value=blocked_counter,
                threshold_value=policy.block_threshold,
                window_ttl_seconds=0,
                block_ttl_seconds=block_ttl,
                reason="tenant_temporarily_blocked",
            )
            await self._record_event(
                conn,
                tenant_id=tenant_id,
                decision=decision,
                request_id=request_id,
                created_by=created_by,
                details=details,
            )
            await self._publish_alert(tenant_id=tenant_id, decision=decision, request_id=request_id)
            return decision

        bucket = int(time.time()) // policy.window_seconds
        counter_key = self._counter_key(tenant_id, policy_scope, metric_key, bucket)
        counter_value_raw = await self._redis.incr(counter_key)
        counter_value = int(counter_value_raw)
        if counter_value == 1:
            await self._redis.expire(counter_key, policy.window_seconds + 5)

        window_ttl = await self._ttl(counter_key)
        if window_ttl <= 0:
            await self._redis.expire(counter_key, policy.window_seconds + 5)
            window_ttl = policy.window_seconds

        action = RateLimitAction.ALLOW
        threshold_value: Optional[int] = None
        reason = "within_threshold"
        block_ttl_seconds = 0

        if counter_value >= policy.block_threshold:
            action = RateLimitAction.BLOCK
            threshold_value = policy.block_threshold
            reason = "block_threshold_exceeded"
            block_ttl_seconds = policy.block_duration_seconds
            await self._redis.setex(block_key, policy.block_duration_seconds, str(counter_value))
        elif counter_value >= policy.throttle_threshold:
            action = RateLimitAction.THROTTLE
            threshold_value = policy.throttle_threshold
            reason = "throttle_threshold_exceeded"
        elif counter_value >= policy.warn_threshold:
            action = RateLimitAction.WARN
            threshold_value = policy.warn_threshold
            reason = "warn_threshold_reached"

        decision = RateLimitDecision(
            action=action,
            policy=policy,
            counter_value=counter_value,
            threshold_value=threshold_value,
            window_ttl_seconds=window_ttl,
            block_ttl_seconds=block_ttl_seconds,
            reason=reason,
        )

        if action in {RateLimitAction.WARN, RateLimitAction.THROTTLE, RateLimitAction.BLOCK}:
            await self._record_event(
                conn,
                tenant_id=tenant_id,
                decision=decision,
                request_id=request_id,
                created_by=created_by,
                details=details,
            )
            await self._publish_alert(tenant_id=tenant_id, decision=decision, request_id=request_id)

        return decision

    async def get_status(
        self,
        *,
        conn: asyncpg.Connection,
        tenant_id: str,
        policy_scope: str,
        metric_key: Optional[str] = None,
    ) -> List[PolicyStatus]:
        if metric_key:
            rows = await conn.fetch(
                """
                SELECT
                    id,
                    policy_name,
                    policy_scope,
                    metric_key,
                    window_seconds,
                    warn_threshold,
                    throttle_threshold,
                    block_threshold,
                    block_duration_seconds,
                    throttle_retry_seconds,
                    metadata
                FROM tenant_telephony_threshold_policies
                WHERE tenant_id = $1
                  AND policy_scope = $2
                  AND is_active = TRUE
                  AND (metric_key = $3 OR metric_key = '*')
                ORDER BY
                    CASE WHEN metric_key = $3 THEN 0 ELSE 1 END,
                    updated_at DESC
                """,
                tenant_id,
                policy_scope,
                metric_key,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT
                    id,
                    policy_name,
                    policy_scope,
                    metric_key,
                    window_seconds,
                    warn_threshold,
                    throttle_threshold,
                    block_threshold,
                    block_duration_seconds,
                    throttle_retry_seconds,
                    metadata
                FROM tenant_telephony_threshold_policies
                WHERE tenant_id = $1
                  AND policy_scope = $2
                  AND is_active = TRUE
                ORDER BY metric_key, updated_at DESC
                """,
                tenant_id,
                policy_scope,
            )

        policies: List[ThresholdPolicy] = []
        if rows:
            for row in rows:
                policies.append(
                    ThresholdPolicy(
                        policy_id=row["id"],
                        policy_name=row["policy_name"],
                        policy_scope=row["policy_scope"],
                        metric_key=row["metric_key"],
                        window_seconds=int(row["window_seconds"]),
                        warn_threshold=int(row["warn_threshold"]),
                        throttle_threshold=int(row["throttle_threshold"]),
                        block_threshold=int(row["block_threshold"]),
                        block_duration_seconds=int(row["block_duration_seconds"]),
                        throttle_retry_seconds=int(row["throttle_retry_seconds"]),
                        metadata=row["metadata"] or {},
                    )
                )
        elif metric_key:
            policies.append(self._default_policy(policy_scope, metric_key))

        statuses: List[PolicyStatus] = []
        for policy in policies:
            counter_metric_key = (
                metric_key if metric_key and policy.metric_key == "*" else policy.metric_key
            )
            counter_value = 0
            window_ttl = 0
            block_ttl = 0

            if self._redis:
                bucket = int(time.time()) // policy.window_seconds
                counter_key = self._counter_key(tenant_id, policy_scope, counter_metric_key, bucket)
                block_key = self._block_key(tenant_id, policy_scope, counter_metric_key)

                counter_raw = await self._redis.get(counter_key)
                if counter_raw is not None:
                    try:
                        counter_value = int(counter_raw)
                    except (TypeError, ValueError):
                        counter_value = 0
                window_ttl = await self._ttl(counter_key)
                block_ttl = await self._ttl(block_key)

            if block_ttl > 0:
                current_action = RateLimitAction.BLOCK
            elif counter_value >= policy.throttle_threshold:
                current_action = RateLimitAction.THROTTLE
            elif counter_value >= policy.warn_threshold:
                current_action = RateLimitAction.WARN
            else:
                current_action = RateLimitAction.ALLOW

            statuses.append(
                PolicyStatus(
                    policy_id=str(policy.policy_id) if policy.policy_id else None,
                    policy_name=policy.policy_name,
                    policy_scope=policy.policy_scope,
                    metric_key=counter_metric_key,
                    window_seconds=policy.window_seconds,
                    warn_threshold=policy.warn_threshold,
                    throttle_threshold=policy.throttle_threshold,
                    block_threshold=policy.block_threshold,
                    block_duration_seconds=policy.block_duration_seconds,
                    throttle_retry_seconds=policy.throttle_retry_seconds,
                    counter_value=counter_value,
                    window_ttl_seconds=window_ttl,
                    block_ttl_seconds=block_ttl,
                    current_action=current_action,
                    metadata=policy.metadata,
                )
            )

        return statuses
