"""
Day 9 tenant telephony concurrency limiter.

Provides PostgreSQL-backed lease acquisition for active call and transfer
concurrency controls with tenant-scoped advisory locking.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class LeaseKind(str, Enum):
    CALL = "call"
    TRANSFER = "transfer"


@dataclass(frozen=True)
class ConcurrencyPolicy:
    policy_id: Optional[UUID]
    policy_name: str
    max_active_calls: int
    max_transfer_inflight: int
    lease_ttl_seconds: int
    heartbeat_grace_seconds: int
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class LeaseDecision:
    accepted: bool
    lease_id: Optional[UUID]
    lease_kind: LeaseKind
    reason: str
    active_calls: int
    active_transfers: int
    policy: ConcurrencyPolicy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "lease_id": str(self.lease_id) if self.lease_id else None,
            "lease_kind": self.lease_kind.value,
            "reason": self.reason,
            "active_calls": self.active_calls,
            "active_transfers": self.active_transfers,
            "policy": {
                "policy_id": str(self.policy.policy_id) if self.policy.policy_id else None,
                "policy_name": self.policy.policy_name,
                "max_active_calls": self.policy.max_active_calls,
                "max_transfer_inflight": self.policy.max_transfer_inflight,
                "lease_ttl_seconds": self.policy.lease_ttl_seconds,
                "heartbeat_grace_seconds": self.policy.heartbeat_grace_seconds,
                "metadata": self.policy.metadata,
            },
        }


class TelephonyConcurrencyLimiter:
    """
    PostgreSQL-backed tenant lease manager for Day 9 transfer and call limits.
    """

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client

    @staticmethod
    def _default_policy() -> ConcurrencyPolicy:
        return ConcurrencyPolicy(
            policy_id=None,
            policy_name="runtime-default",
            max_active_calls=10,
            max_transfer_inflight=2,
            lease_ttl_seconds=120,
            heartbeat_grace_seconds=30,
            metadata={"source": "default"},
        )

    @staticmethod
    def _lock_key(tenant_id: str) -> int:
        digest = hashlib.sha256(f"tenant:{tenant_id}".encode("utf-8")).digest()[:8]
        return int.from_bytes(digest, byteorder="big", signed=True)

    async def _load_policy(self, conn: asyncpg.Connection, *, tenant_id: str) -> ConcurrencyPolicy:
        row = await conn.fetchrow(
            """
            SELECT
                id,
                policy_name,
                max_active_calls,
                max_transfer_inflight,
                lease_ttl_seconds,
                heartbeat_grace_seconds,
                metadata
            FROM tenant_telephony_concurrency_policies
            WHERE tenant_id = $1
              AND is_active = TRUE
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            tenant_id,
        )
        if not row:
            return self._default_policy()
        return ConcurrencyPolicy(
            policy_id=row["id"],
            policy_name=row["policy_name"],
            max_active_calls=int(row["max_active_calls"]),
            max_transfer_inflight=int(row["max_transfer_inflight"]),
            lease_ttl_seconds=int(row["lease_ttl_seconds"]),
            heartbeat_grace_seconds=int(row["heartbeat_grace_seconds"]),
            metadata=row["metadata"] or {},
        )

    async def _active_counts(self, conn: asyncpg.Connection, *, tenant_id: str) -> tuple[int, int]:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE state IN ('active', 'releasing')
                      AND released_at IS NULL
                      AND lease_kind = 'call'
                ) AS active_calls,
                COUNT(*) FILTER (
                    WHERE state IN ('active', 'releasing')
                      AND released_at IS NULL
                      AND lease_kind = 'transfer'
                ) AS active_transfers
            FROM tenant_telephony_concurrency_leases
            WHERE tenant_id = $1
            """,
            tenant_id,
        )
        if not row:
            return 0, 0
        return int(row["active_calls"] or 0), int(row["active_transfers"] or 0)

    async def _record_event(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        policy_id: Optional[UUID],
        lease_id: Optional[UUID],
        event_type: str,
        lease_kind: LeaseKind,
        call_id: Optional[str],
        talklee_call_id: Optional[str],
        request_id: Optional[str],
        created_by: Optional[str],
        details: Dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO tenant_telephony_concurrency_events (
                tenant_id,
                policy_id,
                lease_id,
                event_type,
                lease_kind,
                call_id,
                talklee_call_id,
                details,
                request_id,
                created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            """,
            tenant_id,
            policy_id,
            lease_id,
            event_type,
            lease_kind.value,
            call_id,
            talklee_call_id,
            json.dumps(details),
            request_id,
            created_by,
        )

    async def _redis_increment(self, tenant_id: str, lease_kind: LeaseKind) -> None:
        if not self._redis:
            return
        key = f"telephony:concurrency:active:{tenant_id}:{lease_kind.value}"
        try:
            await self._redis.incr(key)
            await self._redis.expire(key, 300)
        except Exception:
            logger.warning("Failed to increment redis concurrency key", exc_info=True)

    async def _redis_decrement(self, tenant_id: str, lease_kind: LeaseKind) -> None:
        if not self._redis:
            return
        key = f"telephony:concurrency:active:{tenant_id}:{lease_kind.value}"
        try:
            value = await self._redis.get(key)
            current = int(value or "0")
            if current <= 1:
                await self._redis.setex(key, 60, "0")
            else:
                await self._redis.setex(key, 300, str(current - 1))
        except Exception:
            logger.warning("Failed to decrement redis concurrency key", exc_info=True)

    async def acquire_lease(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        call_id: str,
        talklee_call_id: str,
        lease_kind: LeaseKind | str,
        request_id: Optional[str] = None,
        created_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LeaseDecision:
        if isinstance(lease_kind, LeaseKind):
            kind = lease_kind
        else:
            kind = LeaseKind(str(lease_kind).strip().lower())
        policy = await self._load_policy(conn, tenant_id=tenant_id)

        await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", self._lock_key(tenant_id))
        active_calls, active_transfers = await self._active_counts(conn, tenant_id=tenant_id)

        if kind == LeaseKind.CALL and active_calls >= policy.max_active_calls:
            decision = LeaseDecision(
                accepted=False,
                lease_id=None,
                lease_kind=kind,
                reason="max_active_calls_reached",
                active_calls=active_calls,
                active_transfers=active_transfers,
                policy=policy,
            )
            await self._record_event(
                conn,
                tenant_id=tenant_id,
                policy_id=policy.policy_id,
                lease_id=None,
                event_type="reject",
                lease_kind=kind,
                call_id=call_id,
                talklee_call_id=talklee_call_id,
                request_id=request_id,
                created_by=created_by,
                details=decision.to_dict(),
            )
            return decision

        if kind == LeaseKind.TRANSFER and active_transfers >= policy.max_transfer_inflight:
            decision = LeaseDecision(
                accepted=False,
                lease_id=None,
                lease_kind=kind,
                reason="max_transfer_inflight_reached",
                active_calls=active_calls,
                active_transfers=active_transfers,
                policy=policy,
            )
            await self._record_event(
                conn,
                tenant_id=tenant_id,
                policy_id=policy.policy_id,
                lease_id=None,
                event_type="reject",
                lease_kind=kind,
                call_id=call_id,
                talklee_call_id=talklee_call_id,
                request_id=request_id,
                created_by=created_by,
                details=decision.to_dict(),
            )
            return decision

        lease_row = await conn.fetchrow(
            """
            INSERT INTO tenant_telephony_concurrency_leases (
                tenant_id,
                policy_id,
                call_id,
                talklee_call_id,
                lease_kind,
                state,
                metadata,
                created_by,
                updated_by
            )
            VALUES ($1, $2, $3, $4, $5, 'active', $6::jsonb, $7, $8)
            ON CONFLICT (tenant_id, call_id, lease_kind)
                WHERE released_at IS NULL AND state IN ('active', 'releasing')
            DO UPDATE
            SET last_heartbeat_at = NOW(),
                updated_at = NOW(),
                updated_by = EXCLUDED.updated_by
            RETURNING id
            """,
            tenant_id,
            policy.policy_id,
            call_id,
            talklee_call_id,
            kind.value,
            json.dumps(metadata or {}),
            created_by,
            created_by,
        )
        lease_id: UUID = lease_row["id"]
        active_calls_post, active_transfers_post = await self._active_counts(conn, tenant_id=tenant_id)

        decision = LeaseDecision(
            accepted=True,
            lease_id=lease_id,
            lease_kind=kind,
            reason="lease_acquired",
            active_calls=active_calls_post,
            active_transfers=active_transfers_post,
            policy=policy,
        )
        await self._record_event(
            conn,
            tenant_id=tenant_id,
            policy_id=policy.policy_id,
            lease_id=lease_id,
            event_type="acquire",
            lease_kind=kind,
            call_id=call_id,
            talklee_call_id=talklee_call_id,
            request_id=request_id,
            created_by=created_by,
            details=decision.to_dict(),
        )
        await self._redis_increment(tenant_id, kind)
        return decision

    async def release_lease(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        lease_id: UUID | str,
        reason: str,
        request_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> bool:
        row = await conn.fetchrow(
            """
            UPDATE tenant_telephony_concurrency_leases
            SET state = 'released',
                released_at = NOW(),
                release_reason = $3,
                updated_by = $4,
                updated_at = NOW()
            WHERE tenant_id = $1
              AND id = $2
              AND released_at IS NULL
            RETURNING id, policy_id, call_id, talklee_call_id, lease_kind
            """,
            tenant_id,
            lease_id,
            reason[:64],
            created_by,
        )
        if not row:
            return False

        kind = LeaseKind(row["lease_kind"])
        await self._record_event(
            conn,
            tenant_id=tenant_id,
            policy_id=row["policy_id"],
            lease_id=row["id"],
            event_type="release",
            lease_kind=kind,
            call_id=str(row["call_id"]) if row["call_id"] else None,
            talklee_call_id=row["talklee_call_id"],
            request_id=request_id,
            created_by=created_by,
            details={"reason": reason},
        )
        await self._redis_decrement(tenant_id, kind)
        return True

    async def heartbeat_lease(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        lease_id: UUID | str,
        request_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> bool:
        row = await conn.fetchrow(
            """
            UPDATE tenant_telephony_concurrency_leases
            SET last_heartbeat_at = NOW(),
                updated_by = $3,
                updated_at = NOW()
            WHERE tenant_id = $1
              AND id = $2
              AND released_at IS NULL
            RETURNING id, policy_id, call_id, talklee_call_id, lease_kind
            """,
            tenant_id,
            lease_id,
            created_by,
        )
        if not row:
            return False

        await self._record_event(
            conn,
            tenant_id=tenant_id,
            policy_id=row["policy_id"],
            lease_id=row["id"],
            event_type="heartbeat",
            lease_kind=LeaseKind(row["lease_kind"]),
            call_id=str(row["call_id"]) if row["call_id"] else None,
            talklee_call_id=row["talklee_call_id"],
            request_id=request_id,
            created_by=created_by,
            details={"status": "ok"},
        )
        return True

    async def expire_stale_leases(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        request_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> int:
        policy = await self._load_policy(conn, tenant_id=tenant_id)
        ttl_with_grace = max(policy.lease_ttl_seconds + policy.heartbeat_grace_seconds, 1)

        rows = await conn.fetch(
            """
            UPDATE tenant_telephony_concurrency_leases
            SET state = 'expired',
                released_at = NOW(),
                release_reason = 'lease_ttl_expired',
                updated_by = $3,
                updated_at = NOW()
            WHERE tenant_id = $1
              AND released_at IS NULL
              AND state IN ('active', 'releasing')
              AND last_heartbeat_at < NOW() - ($2::int * INTERVAL '1 second')
            RETURNING id, policy_id, call_id, talklee_call_id, lease_kind
            """,
            tenant_id,
            ttl_with_grace,
            created_by,
        )

        for row in rows:
            await self._record_event(
                conn,
                tenant_id=tenant_id,
                policy_id=row["policy_id"],
                lease_id=row["id"],
                event_type="expire",
                lease_kind=LeaseKind(row["lease_kind"]),
                call_id=str(row["call_id"]) if row["call_id"] else None,
                talklee_call_id=row["talklee_call_id"],
                request_id=request_id,
                created_by=created_by,
                details={"ttl_with_grace_seconds": ttl_with_grace},
            )
            await self._redis_decrement(tenant_id, LeaseKind(row["lease_kind"]))

        return len(rows)

    async def get_status(self, conn: asyncpg.Connection, *, tenant_id: str) -> Dict[str, Any]:
        policy = await self._load_policy(conn, tenant_id=tenant_id)
        active_calls, active_transfers = await self._active_counts(conn, tenant_id=tenant_id)
        return {
            "tenant_id": tenant_id,
            "active_calls": active_calls,
            "active_transfers": active_transfers,
            "max_active_calls": policy.max_active_calls,
            "max_transfer_inflight": policy.max_transfer_inflight,
            "lease_ttl_seconds": policy.lease_ttl_seconds,
            "heartbeat_grace_seconds": policy.heartbeat_grace_seconds,
            "policy_name": policy.policy_name,
            "policy_id": str(policy.policy_id) if policy.policy_id else None,
            "metadata": policy.metadata,
        }
