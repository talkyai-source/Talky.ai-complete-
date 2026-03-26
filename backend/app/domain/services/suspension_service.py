"""
Suspension Service - Formalized suspension workflow with instant block propagation
"""
import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

import redis.asyncio as aioredis
import asyncpg
from pydantic import BaseModel


class SuspensionType(str, Enum):
    """Types of suspension"""
    TEMPORARY = "TEMPORARY"      # Auto-restored after duration (password breach, suspicious login)
    ADMIN = "ADMIN"              # Manual review required
    BILLING = "BILLING"          # Payment failure - auto-restored after payment
    ABUSE = "ABUSE"              # Spam, fraud, ToS violation - manual review
    COMPLIANCE = "COMPLIANCE"    # Legal hold, regulatory request
    EMERGENCY = "EMERGENCY"      # Critical security incident


class TargetType(str, Enum):
    """Target types for suspension"""
    USER = "user"
    TENANT = "tenant"
    PARTNER = "partner"


class SuspensionStatus(str, Enum):
    """Suspension status"""
    ACTIVE = "active"
    SCHEDULED_RESTORE = "scheduled_restore"
    PENDING_REVIEW = "pending_review"
    RESTORED = "restored"
    APPEALED = "appealed"


class PropagationService(str, Enum):
    """Services that receive suspension propagation"""
    API_GATEWAY = "api_gateway"
    SESSION_MANAGER = "session_manager"
    CALL_GUARD = "call_guard"
    WEBHOOK_QUEUE = "webhook_queue"
    BACKGROUND_WORKER = "background_worker"
    CDN_WAF = "cdn_waf"


ALL_SERVICES = [
    PropagationService.API_GATEWAY,
    PropagationService.SESSION_MANAGER,
    PropagationService.CALL_GUARD,
    PropagationService.WEBHOOK_QUEUE,
    PropagationService.BACKGROUND_WORKER,
]


class SuspensionEvent(BaseModel):
    """Suspension event model"""
    suspension_id: UUID
    created_at: datetime
    target_type: TargetType
    target_id: UUID
    suspension_type: SuspensionType
    reason_category: str
    reason_description: str
    evidence: Optional[dict]
    suspended_at: datetime
    suspended_until: Optional[datetime]
    restored_at: Optional[datetime]
    suspended_by: Optional[UUID]
    restored_by: Optional[UUID]
    restore_reason: Optional[str]
    is_active: bool
    propagated_services: list[str]
    propagation_confirmed_at: Optional[datetime]
    appeal_submitted_at: Optional[datetime]
    appeal_reason: Optional[str]
    appeal_reviewed_by: Optional[UUID]
    appeal_decision: Optional[str]
    appeal_response: Optional[str]
    audit_log_id: Optional[UUID]


class SuspensionResult(BaseModel):
    """Result of suspension operation"""
    suspension_id: UUID
    target_type: TargetType
    target_id: UUID
    status: str
    propagated_to: list[str]
    propagation_failed: list[str]


class SuspensionStatusResponse(BaseModel):
    """Status check response"""
    target_type: TargetType
    target_id: UUID
    status: SuspensionStatus
    is_suspended: bool
    active_suspension: Optional[SuspensionEvent]
    suspension_history: list[SuspensionEvent]


class SuspensionService:
    """
    Formalized suspension workflow with instant block propagation.

    Features:
    - Multi-target suspension (user, tenant, partner)
    - Event-driven propagation via Redis pub/sub
    - Appeal workflow
    - Automatic restoration for temporary suspensions
    - Full audit integration
    """

    # Suspension types that auto-restore
    AUTO_RESTORE_TYPES = {SuspensionType.TEMPORARY, SuspensionType.BILLING}

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis_client: Optional[aioredis.Redis] = None,
        propagation_timeout_seconds: int = 30
    ):
        self.db_pool = db_pool
        self.redis = redis_client
        self.propagation_timeout = propagation_timeout_seconds

    async def suspend_user(
        self,
        user_id: UUID | str,
        suspension_type: SuspensionType,
        reason_category: str,
        reason_description: str,
        evidence: Optional[dict] = None,
        suspended_by: Optional[UUID | str] = None,
        duration_hours: Optional[int] = None,
        notify_user: bool = True,
    ) -> SuspensionResult:
        """
        Suspend a user account.

        Args:
            user_id: User to suspend
            suspension_type: Type of suspension
            reason_category: Category code for the reason
            reason_description: Human-readable reason
            evidence: Supporting evidence (JSON)
            suspended_by: Admin who performed suspension (None for system)
            duration_hours: Duration before auto-restore (None = indefinite)
            notify_user: Whether to notify the user

        Returns:
            SuspensionResult with propagation status
        """
        user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
        admin_uuid = UUID(suspended_by) if isinstance(suspended_by, str) else suspended_by

        suspended_until = None
        if duration_hours and suspension_type in self.AUTO_RESTORE_TYPES:
            suspended_until = datetime.utcnow() + timedelta(hours=duration_hours)

        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                # Create suspension event
                suspension_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO suspension_events (
                        suspension_id, target_type, target_id, suspension_type,
                        reason_category, reason_description, evidence,
                        suspended_at, suspended_until, suspended_by, is_active
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    suspension_id, TargetType.USER.value, user_uuid, suspension_type.value,
                    reason_category, reason_description, json.dumps(evidence) if evidence else None,
                    datetime.utcnow(), suspended_until, admin_uuid, True
                )

                # Update user profile
                await conn.execute(
                    "UPDATE user_profiles SET is_active = FALSE, updated_at = NOW() WHERE id = $1",
                    user_uuid
                )

        # Propagate to services
        propagation_result = await self._propagate_to_services(
            suspension_id=suspension_id,
            target_type=TargetType.USER,
            target_id=user_uuid,
            action="suspend"
        )

        # Publish event for real-time notification
        if self.redis:
            await self.redis.publish(
                "suspension:events",
                json.dumps({
                    "type": "user_suspended",
                    "suspension_id": str(suspension_id),
                    "user_id": str(user_uuid),
                    "suspension_type": suspension_type.value,
                    "notify_user": notify_user
                })
            )

        return SuspensionResult(
            suspension_id=suspension_id,
            target_type=TargetType.USER,
            target_id=user_uuid,
            status="suspended",
            propagated_to=propagation_result["success"],
            propagation_failed=propagation_result["failed"]
        )

    async def suspend_tenant(
        self,
        tenant_id: UUID | str,
        suspension_type: SuspensionType,
        reason_category: str,
        reason_description: str,
        evidence: Optional[dict] = None,
        suspended_by: Optional[UUID | str] = None,
        duration_hours: Optional[int] = None,
        immediate: bool = True,
        notify_users: bool = True,
    ) -> SuspensionResult:
        """
        Suspend a tenant and all its users.

        Args:
            tenant_id: Tenant to suspend
            suspension_type: Type of suspension
            reason_category: Category code
            reason_description: Human-readable reason
            evidence: Supporting evidence
            suspended_by: Admin who performed suspension
            duration_hours: Duration before auto-restore
            immediate: Whether to terminate active sessions immediately
            notify_users: Whether to notify tenant users

        Returns:
            SuspensionResult with propagation status
        """
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        admin_uuid = UUID(suspended_by) if isinstance(suspended_by, str) else suspended_by

        suspended_until = None
        if duration_hours and suspension_type in self.AUTO_RESTORE_TYPES:
            suspended_until = datetime.utcnow() + timedelta(hours=duration_hours)

        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                # Create suspension event
                suspension_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO suspension_events (
                        suspension_id, target_type, target_id, suspension_type,
                        reason_category, reason_description, evidence,
                        suspended_at, suspended_until, suspended_by, is_active
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    suspension_id, TargetType.TENANT.value, tenant_uuid, suspension_type.value,
                    reason_category, reason_description, json.dumps(evidence) if evidence else None,
                    datetime.utcnow(), suspended_until, admin_uuid, True
                )

                # Update tenant status
                await conn.execute(
                    "UPDATE tenant_users SET status = 'suspended', updated_at = NOW() WHERE tenant_id = $1",
                    tenant_uuid
                )

                # Terminate active sessions if immediate
                if immediate:
                    await conn.execute(
                        """
                        UPDATE security_sessions
                        SET terminated_at = NOW(), termination_reason = 'tenant_suspended'
                        WHERE tenant_id = $1 AND terminated_at IS NULL
                        """,
                        tenant_uuid
                    )

        # Propagate to all services
        propagation_result = await self._propagate_to_services(
            suspension_id=suspension_id,
            target_type=TargetType.TENANT,
            target_id=tenant_uuid,
            action="suspend"
        )

        # Publish event
        if self.redis:
            await self.redis.publish(
                "suspension:events",
                json.dumps({
                    "type": "tenant_suspended",
                    "suspension_id": str(suspension_id),
                    "tenant_id": str(tenant_uuid),
                    "suspension_type": suspension_type.value,
                    "immediate": immediate,
                    "notify_users": notify_users
                })
            )

        return SuspensionResult(
            suspension_id=suspension_id,
            target_type=TargetType.TENANT,
            target_id=tenant_uuid,
            status="suspended",
            propagated_to=propagation_result["success"],
            propagation_failed=propagation_result["failed"]
        )

    async def suspend_partner(
        self,
        partner_id: UUID | str,
        suspension_type: SuspensionType,
        reason_category: str,
        reason_description: str,
        evidence: Optional[dict] = None,
        suspended_by: Optional[UUID | str] = None,
        duration_hours: Optional[int] = None,
    ) -> SuspensionResult:
        """
        Suspend a partner and all associated tenants.

        Args:
            partner_id: Partner to suspend
            suspension_type: Type of suspension
            reason_category: Category code
            reason_description: Human-readable reason
            evidence: Supporting evidence
            suspended_by: Admin who performed suspension
            duration_hours: Duration before auto-restore

        Returns:
            SuspensionResult with propagation status
        """
        partner_uuid = UUID(partner_id) if isinstance(partner_id, str) else partner_id
        admin_uuid = UUID(suspended_by) if isinstance(suspended_by, str) else suspended_by

        suspended_until = None
        if duration_hours and suspension_type in self.AUTO_RESTORE_TYPES:
            suspended_until = datetime.utcnow() + timedelta(hours=duration_hours)

        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                # Create suspension event
                suspension_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO suspension_events (
                        suspension_id, target_type, target_id, suspension_type,
                        reason_category, reason_description, evidence,
                        suspended_at, suspended_until, suspended_by, is_active
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    suspension_id, TargetType.PARTNER.value, partner_uuid, suspension_type.value,
                    reason_category, reason_description, json.dumps(evidence) if evidence else None,
                    datetime.utcnow(), suspended_until, admin_uuid, True
                )

                # Get all partner tenants
                tenant_rows = await conn.fetch(
                    "SELECT id FROM tenants WHERE partner_id = $1",
                    partner_uuid
                )
                tenant_ids = [row["id"] for row in tenant_rows]

                # Suspend all partner tenants
                for tid in tenant_ids:
                    await conn.execute(
                        "UPDATE tenant_users SET status = 'suspended' WHERE tenant_id = $1",
                        tid
                    )

                    await conn.execute(
                        """
                        UPDATE security_sessions
                        SET terminated_at = NOW(), termination_reason = 'partner_suspended'
                        WHERE tenant_id = $1 AND terminated_at IS NULL
                        """,
                        tid
                    )

        # Propagate
        propagation_result = await self._propagate_to_services(
            suspension_id=suspension_id,
            target_type=TargetType.PARTNER,
            target_id=partner_uuid,
            action="suspend"
        )

        # Publish event
        if self.redis:
            await self.redis.publish(
                "suspension:events",
                json.dumps({
                    "type": "partner_suspended",
                    "suspension_id": str(suspension_id),
                    "partner_id": str(partner_uuid),
                    "affected_tenants": [str(t) for t in tenant_ids],
                    "suspension_type": suspension_type.value
                })
            )

        return SuspensionResult(
            suspension_id=suspension_id,
            target_type=TargetType.PARTNER,
            target_id=partner_uuid,
            status="suspended",
            propagated_to=propagation_result["success"],
            propagation_failed=propagation_result["failed"]
        )

    async def restore_user(
        self,
        user_id: UUID | str,
        restored_by: UUID | str,
        reason: str,
    ) -> SuspensionResult:
        """Restore a suspended user"""
        user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
        admin_uuid = UUID(restored_by) if isinstance(restored_by, str) else restored_by

        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                # Get active suspension
                suspension = await conn.fetchrow(
                    """
                    SELECT suspension_id FROM suspension_events
                    WHERE target_type = $1 AND target_id = $2 AND is_active = TRUE
                    ORDER BY suspended_at DESC LIMIT 1
                    """,
                    TargetType.USER.value, user_uuid
                )

                if not suspension:
                    raise ValueError("No active suspension found for user")

                suspension_id = suspension["suspension_id"]

                # Update suspension event
                await conn.execute(
                    """
                    UPDATE suspension_events
                    SET is_active = FALSE, restored_at = $1, restored_by = $2, restore_reason = $3
                    WHERE suspension_id = $4
                    """,
                    datetime.utcnow(), admin_uuid, reason, suspension_id
                )

                # Restore user
                await conn.execute(
                    "UPDATE user_profiles SET is_active = TRUE, updated_at = NOW() WHERE id = $1",
                    user_uuid
                )

        # Propagate restoration
        propagation_result = await self._propagate_to_services(
            suspension_id=suspension_id,
            target_type=TargetType.USER,
            target_id=user_uuid,
            action="restore"
        )

        # Publish event
        if self.redis:
            await self.redis.publish(
                "suspension:events",
                json.dumps({
                    "type": "user_restored",
                    "suspension_id": str(suspension_id),
                    "user_id": str(user_uuid)
                })
            )

        return SuspensionResult(
            suspension_id=suspension_id,
            target_type=TargetType.USER,
            target_id=user_uuid,
            status="restored",
            propagated_to=propagation_result["success"],
            propagation_failed=propagation_result["failed"]
        )

    async def restore_tenant(
        self,
        tenant_id: UUID | str,
        restored_by: UUID | str,
        reason: str,
    ) -> SuspensionResult:
        """Restore a suspended tenant"""
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        admin_uuid = UUID(restored_by) if isinstance(restored_by, str) else restored_by

        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                # Get active suspension
                suspension = await conn.fetchrow(
                    """
                    SELECT suspension_id FROM suspension_events
                    WHERE target_type = $1 AND target_id = $2 AND is_active = TRUE
                    ORDER BY suspended_at DESC LIMIT 1
                    """,
                    TargetType.TENANT.value, tenant_uuid
                )

                if not suspension:
                    raise ValueError("No active suspension found for tenant")

                suspension_id = suspension["suspension_id"]

                # Update suspension event
                await conn.execute(
                    """
                    UPDATE suspension_events
                    SET is_active = FALSE, restored_at = $1, restored_by = $2, restore_reason = $3
                    WHERE suspension_id = $4
                    """,
                    datetime.utcnow(), admin_uuid, reason, suspension_id
                )

                # Restore tenant users
                await conn.execute(
                    "UPDATE tenant_users SET status = 'active', updated_at = NOW() WHERE tenant_id = $1",
                    tenant_uuid
                )

        # Propagate restoration
        propagation_result = await self._propagate_to_services(
            suspension_id=suspension_id,
            target_type=TargetType.TENANT,
            target_id=tenant_uuid,
            action="restore"
        )

        # Publish event
        if self.redis:
            await self.redis.publish(
                "suspension:events",
                json.dumps({
                    "type": "tenant_restored",
                    "suspension_id": str(suspension_id),
                    "tenant_id": str(tenant_uuid)
                })
            )

        return SuspensionResult(
            suspension_id=suspension_id,
            target_type=TargetType.TENANT,
            target_id=tenant_uuid,
            status="restored",
            propagated_to=propagation_result["success"],
            propagation_failed=propagation_result["failed"]
        )

    async def get_status(
        self,
        user_id: Optional[UUID | str] = None,
        tenant_id: Optional[UUID | str] = None,
    ) -> SuspensionStatusResponse:
        """
        Check suspension status for a user or tenant.

        Args:
            user_id: User to check
            tenant_id: Tenant to check

        Returns:
            SuspensionStatusResponse with current status and history
        """
        if user_id:
            target_type = TargetType.USER
            target_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
        elif tenant_id:
            target_type = TargetType.TENANT
            target_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        else:
            raise ValueError("Must provide user_id or tenant_id")

        async with self.db_pool.acquire() as conn:
            # Get active suspension
            active_row = await conn.fetchrow(
                """
                SELECT * FROM suspension_events
                WHERE target_type = $1 AND target_id = $2 AND is_active = TRUE
                ORDER BY suspended_at DESC LIMIT 1
                """,
                target_type.value, target_uuid
            )

            # Get history
            history_rows = await conn.fetch(
                """
                SELECT * FROM suspension_events
                WHERE target_type = $1 AND target_id = $2
                ORDER BY suspended_at DESC
                """,
                target_type.value, target_uuid
            )

        active_suspension = SuspensionEvent(**dict(active_row)) if active_row else None
        history = [SuspensionEvent(**dict(row)) for row in history_rows]

        if active_suspension:
            if active_suspension.suspended_until and active_suspension.suspended_until > datetime.utcnow():
                status = SuspensionStatus.SCHEDULED_RESTORE
            elif active_suspension.appeal_submitted_at and not active_suspension.appeal_decision:
                status = SuspensionStatus.APPEALED
            else:
                status = SuspensionStatus.ACTIVE
            is_suspended = True
        else:
            status = SuspensionStatus.RESTORED
            is_suspended = False

        return SuspensionStatusResponse(
            target_type=target_type,
            target_id=target_uuid,
            status=status,
            is_suspended=is_suspended,
            active_suspension=active_suspension,
            suspension_history=history
        )

    async def submit_appeal(
        self,
        suspension_id: UUID | str,
        appeal_reason: str,
    ) -> dict:
        """
        Submit an appeal for a suspension.

        Args:
            suspension_id: Suspension being appealed
            appeal_reason: Reason for appeal

        Returns:
            Appeal submission result
        """
        suspension_uuid = UUID(suspension_id) if isinstance(suspension_id, str) else suspension_id

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE suspension_events
                SET appeal_submitted_at = $1, appeal_reason = $2, appeal_decision = 'pending'
                WHERE suspension_id = $3 AND is_active = TRUE
                """,
                datetime.utcnow(), appeal_reason, suspension_uuid
            )

            if result == "UPDATE 0":
                raise ValueError("Suspension not found or already resolved")

        # Publish appeal event
        if self.redis:
            await self.redis.publish(
                "suspension:appeals",
                json.dumps({
                    "type": "appeal_submitted",
                    "suspension_id": str(suspension_uuid),
                    "reason": appeal_reason
                })
            )

        return {
            "suspension_id": suspension_uuid,
            "appeal_submitted": True,
            "status": "pending_review"
        }

    async def review_appeal(
        self,
        suspension_id: UUID | str,
        reviewed_by: UUID | str,
        decision: str,  # granted, denied
        response: str,
    ) -> dict:
        """
        Review and decide on a suspension appeal.

        Args:
            suspension_id: Suspension being reviewed
            reviewed_by: Admin reviewing the appeal
            decision: 'granted' or 'denied'
            response: Response to the appellant

        Returns:
            Review result
        """
        suspension_uuid = UUID(suspension_id) if isinstance(suspension_id, str) else suspension_id
        admin_uuid = UUID(reviewed_by) if isinstance(reviewed_by, str) else reviewed_by

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE suspension_events
                SET appeal_reviewed_by = $1, appeal_decision = $2, appeal_response = $3
                WHERE suspension_id = $4
                """,
                admin_uuid, decision, response, suspension_uuid
            )

        # If granted, auto-restore
        if decision == "granted":
            suspension = await conn.fetchrow(
                "SELECT target_type, target_id FROM suspension_events WHERE suspension_id = $1",
                suspension_uuid
            )
            if suspension:
                if suspension["target_type"] == TargetType.USER.value:
                    await self.restore_user(suspension["target_id"], reviewed_by, "Appeal granted")
                elif suspension["target_type"] == TargetType.TENANT.value:
                    await self.restore_tenant(suspension["target_id"], reviewed_by, "Appeal granted")

        return {
            "suspension_id": suspension_uuid,
            "decision": decision,
            "response": response
        }

    async def _propagate_to_services(
        self,
        suspension_id: UUID,
        target_type: TargetType,
        target_id: UUID,
        action: str,  # suspend or restore
    ) -> dict:
        """
        Propagate suspension to all registered services.

        Uses both database queue and Redis pub/sub for reliability.
        """
        success = []
        failed = []

        async with self.db_pool.acquire() as conn:
            for service in ALL_SERVICES:
                queue_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO suspension_propagation_queue (
                        queue_id, suspension_id, target_type, target_id, action,
                        service_name, status, next_attempt_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    queue_id, suspension_id, target_type.value, target_id, action,
                    service.value, "pending", datetime.utcnow()
                )

        # Also publish via Redis for real-time propagation
        if self.redis:
            message = {
                "suspension_id": str(suspension_id),
                "target_type": target_type.value,
                "target_id": str(target_id),
                "action": action,
                "timestamp": datetime.utcnow().isoformat()
            }
            await self.redis.publish("suspension:propagate", json.dumps(message))
            # Assume all services will receive via Redis
            success = [s.value for s in ALL_SERVICES]
        else:
            # Without Redis, mark as pending for background processor
            success = []
            failed = [s.value for s in ALL_SERVICES]

        return {"success": success, "failed": failed}

    async def process_auto_restores(self) -> list[dict]:
        """
        Process automatic restorations for temporary suspensions.

        Should be called by a scheduled job (e.g., every minute).

        Returns:
            List of restored targets
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT suspension_id, target_type, target_id
                FROM suspension_events
                WHERE is_active = TRUE
                AND suspended_until IS NOT NULL
                AND suspended_until <= NOW()
                AND suspension_type IN ('TEMPORARY', 'BILLING')
                """
            )

        restored = []
        for row in rows:
            try:
                if row["target_type"] == TargetType.USER.value:
                    await self.restore_user(
                        row["target_id"],
                        None,  # System restoration
                        "Auto-restored after temporary suspension period"
                    )
                elif row["target_type"] == TargetType.TENANT.value:
                    await self.restore_tenant(
                        row["target_id"],
                        None,
                        "Auto-restored after temporary suspension period"
                    )

                restored.append({
                    "suspension_id": str(row["suspension_id"]),
                    "target_type": row["target_type"],
                    "target_id": str(row["target_id"]),
                    "restored": True
                })
            except Exception as e:
                restored.append({
                    "suspension_id": str(row["suspension_id"]),
                    "target_type": row["target_type"],
                    "target_id": str(row["target_id"]),
                    "restored": False,
                    "error": str(e)
                })

        return restored

    async def bulk_suspend_by_criteria(
        self,
        criteria: dict,
        suspension_type: SuspensionType,
        reason_category: str,
        reason_description: str,
        suspended_by: UUID | str,
    ) -> dict:
        """
        Bulk suspend users/tenants matching criteria.

        Criteria examples:
        - {"tenant_id": "uuid", "last_login_before": "2024-01-01"}
        - {"abuse_score_above": 0.8}
        - {"inactive_for_days": 90}

        Args:
            criteria: Filter criteria
            suspension_type: Type of suspension
            reason_category: Category code
            reason_description: Human-readable reason
            suspended_by: Admin performing action

        Returns:
            Summary of suspensions performed
        """
        # This is a placeholder for bulk operations
        # Actual implementation would build dynamic SQL based on criteria
        raise NotImplementedError("Bulk suspend by criteria not yet implemented")


# Convenience function for dependency injection
async def get_suspension_service(
    db_pool: asyncpg.Pool,
    redis_client: Optional[aioredis.Redis] = None
) -> SuspensionService:
    """Factory function for creating suspension service"""
    return SuspensionService(db_pool, redis_client)
