"""
Emergency Access Module - Break-glass dual-control access
"""
import json
import secrets
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

import aioredis
import asyncpg
from pydantic import BaseModel


class EmergencyScenario(str, Enum):
    """Emergency access scenarios"""
    SECURITY_INCIDENT = "security_incident"
    PLATFORM_ADMIN_LOCKOUT = "platform_admin_lockout"
    COMPLIANCE_INVESTIGATION = "compliance_investigation"
    DISASTER_RECOVERY = "disaster_recovery"


class EmergencyStatus(str, Enum):
    """Emergency request status"""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    USED = "used"
    CANCELLED = "cancelled"


class ApprovalInfo(BaseModel):
    """Approval information"""
    approver_id: UUID
    approved_at: datetime
    method: str  # sms, email, totp, in_person
    verification_code: Optional[str]


class EmergencyAccessRequest(BaseModel):
    """Emergency access request model"""
    request_id: UUID
    created_at: datetime
    requestor_id: UUID
    scenario: EmergencyScenario
    justification: str
    requested_access: list[str]
    approvers_required: int
    approvals: list[ApprovalInfo]
    status: EmergencyStatus
    approved_at: Optional[datetime]
    expires_at: datetime
    session_created_at: Optional[datetime]
    session_terminated_at: Optional[datetime]
    session_token_hash: Optional[str]
    actions_taken: list[dict]
    reviewed_at: Optional[datetime]
    reviewed_by: Optional[UUID]
    review_notes: Optional[str]


class EmergencySession(BaseModel):
    """Emergency session"""
    session_token: str
    request_id: UUID
    requestor_id: UUID
    created_at: datetime
    expires_at: datetime
    permissions: list[str]


class EmergencyAccess:
    """
    Break-glass emergency access with dual-control approval.

    Features:
    - Dual-control approval (requires 2+ approvers)
    - Time-limited sessions
    - Full audit trail of all actions
    - Post-incident review workflow
    """

    # Required approvers by scenario
    APPROVER_REQUIREMENTS = {
        EmergencyScenario.SECURITY_INCIDENT: 2,
        EmergencyScenario.PLATFORM_ADMIN_LOCKOUT: 2,
        EmergencyScenario.COMPLIANCE_INVESTIGATION: 1,
        EmergencyScenario.DISASTER_RECOVERY: 2,
    }

    # Session TTL by scenario (hours)
    SESSION_TTL = {
        EmergencyScenario.SECURITY_INCIDENT: 4,
        EmergencyScenario.PLATFORM_ADMIN_LOCKOUT: 4,
        EmergencyScenario.COMPLIANCE_INVESTIGATION: 24,
        EmergencyScenario.DISASTER_RECOVERY: 72,
    }

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis_client: Optional[aioredis.Redis] = None,
    ):
        self.db_pool = db_pool
        self.redis = redis_client

    async def request(
        self,
        requestor_id: UUID | str,
        scenario: EmergencyScenario,
        justification: str,
        required_access: list[str],
    ) -> EmergencyAccessRequest:
        """
        Request emergency break-glass access.

        Args:
            requestor_id: User requesting access
            scenario: Type of emergency
            justification: Detailed justification
            required_access: List of required permissions

        Returns:
            EmergencyAccessRequest
        """
        requestor_uuid = UUID(requestor_id) if isinstance(requestor_id, str) else requestor_id
        request_id = uuid4()

        approvers_required = self.APPROVER_REQUIREMENTS.get(scenario, 2)

        # Calculate expiration (approvals must happen within 1 hour)
        expires_at = datetime.utcnow() + timedelta(hours=1)

        request = EmergencyAccessRequest(
            request_id=request_id,
            created_at=datetime.utcnow(),
            requestor_id=requestor_uuid,
            scenario=scenario,
            justification=justification,
            requested_access=required_access,
            approvers_required=approvers_required,
            approvals=[],
            status=EmergencyStatus.PENDING,
            approved_at=None,
            expires_at=expires_at,
            session_created_at=None,
            session_terminated_at=None,
            session_token_hash=None,
            actions_taken=[],
            reviewed_at=None,
            reviewed_by=None,
            review_notes=None,
        )

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO emergency_access_requests (
                    request_id, created_at, requestor_id, scenario, justification,
                    requested_access, approvers_required, approvals, status,
                    expires_at, actions_taken
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                request_id, request.created_at, requestor_uuid, scenario.value,
                justification, required_access, approvers_required,
                json.dumps([]), EmergencyStatus.PENDING.value, expires_at,
                json.dumps([])
            )

        # Notify potential approvers via Redis
        if self.redis:
            await self.redis.publish(
                "emergency:requests",
                json.dumps({
                    "type": "request_created",
                    "request_id": str(request_id),
                    "requestor_id": str(requestor_uuid),
                    "scenario": scenario.value,
                    "urgency": "high" if scenario == EmergencyScenario.SECURITY_INCIDENT else "medium"
                })
            )

        return request

    async def approve(
        self,
        request_id: UUID | str,
        approver_id: UUID | str,
        method: str = "totp",  # sms, email, totp, in_person
        verification_code: Optional[str] = None,
    ) -> dict:
        """
        Approve an emergency access request.

        Args:
            request_id: Request being approved
            approver_id: Admin providing approval
            method: Verification method used
            verification_code: Optional verification code

        Returns:
            Approval result with current status
        """
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id
        approver_uuid = UUID(approver_id) if isinstance(approver_id, str) else approver_id

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM emergency_access_requests WHERE request_id = $1",
                request_uuid
            )

            if not row:
                raise ValueError("Request not found")

            if row["status"] not in [EmergencyStatus.PENDING.value, EmergencyStatus.APPROVED.value]:
                raise ValueError(f"Request cannot be approved (status: {row['status']})")

            if row["expires_at"] < datetime.utcnow():
                await conn.execute(
                    "UPDATE emergency_access_requests SET status = $1 WHERE request_id = $2",
                    EmergencyStatus.EXPIRED.value, request_uuid
                )
                raise ValueError("Request has expired")

            # Check if approver is the requestor (can't approve own request)
            if row["requestor_id"] == approver_uuid:
                raise ValueError("Cannot approve your own emergency request")

            # Load existing approvals
            approvals = json.loads(row["approvals"]) if row["approvals"] else []

            # Check if already approved by this person
            if any(a["approver_id"] == str(approver_uuid) for a in approvals):
                raise ValueError("You have already approved this request")

            # Add approval
            approval = {
                "approver_id": str(approver_uuid),
                "approved_at": datetime.utcnow().isoformat(),
                "method": method,
                "verification_code": verification_code,
            }
            approvals.append(approval)

            # Check if fully approved
            approvers_required = row["approvers_required"]
            is_fully_approved = len(approvals) >= approvers_required

            if is_fully_approved:
                # Calculate session expiration
                scenario = EmergencyScenario(row["scenario"])
                session_ttl_hours = self.SESSION_TTL.get(scenario, 4)
                new_expires_at = datetime.utcnow() + timedelta(hours=session_ttl_hours)

                await conn.execute(
                    """
                    UPDATE emergency_access_requests
                    SET approvals = $1, status = $2, approved_at = $3, expires_at = $4
                    WHERE request_id = $5
                    """,
                    json.dumps(approvals), EmergencyStatus.APPROVED.value,
                    datetime.utcnow(), new_expires_at, request_uuid
                )
            else:
                await conn.execute(
                    "UPDATE emergency_access_requests SET approvals = $1 WHERE request_id = $2",
                    json.dumps(approvals), request_uuid
                )

        # Notify requestor if fully approved
        if is_fully_approved and self.redis:
            await self.redis.publish(
                f"emergency:user:{row['requestor_id']}",
                json.dumps({
                    "type": "request_approved",
                    "request_id": str(request_uuid),
                    "approvals_received": len(approvals),
                    "session_ready": True
                })
            )

        return {
            "request_id": request_uuid,
            "approved": True,
            "fully_approved": is_fully_approved,
            "approvals_received": len(approvals),
            "approvals_required": approvers_required,
        }

    async def deny(
        self,
        request_id: UUID | str,
        denied_by: UUID | str,
        reason: str,
    ) -> dict:
        """Deny an emergency access request"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id
        denier_uuid = UUID(denied_by) if isinstance(denied_by, str) else denied_by

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE emergency_access_requests
                SET status = $1, reviewed_by = $2, reviewed_at = $3, review_notes = $4
                WHERE request_id = $5
                """,
                EmergencyStatus.DENIED.value, denier_uuid, datetime.utcnow(),
                reason, request_uuid
            )

        return {"request_id": request_uuid, "denied": True}

    async def create_session(
        self,
        request_id: UUID | str,
    ) -> EmergencySession:
        """
        Create an emergency access session after approval.

        Args:
            request_id: Approved request

        Returns:
            EmergencySession with token
        """
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM emergency_access_requests WHERE request_id = $1",
                request_uuid
            )

            if not row:
                raise ValueError("Request not found")

            if row["status"] != EmergencyStatus.APPROVED.value:
                raise ValueError("Request not approved")

            if row["expires_at"] < datetime.utcnow():
                raise ValueError("Approval has expired")

            if row["session_created_at"]:
                raise ValueError("Session already created")

            # Generate session token
            session_token = secrets.token_urlsafe(64)
            token_hash = hashlib.sha256(session_token.encode()).hexdigest()

            # Update request
            await conn.execute(
                """
                UPDATE emergency_access_requests
                SET session_created_at = $1, session_token_hash = $2, status = $3
                WHERE request_id = $4
                """,
                datetime.utcnow(), token_hash, EmergencyStatus.USED.value,
                request_uuid
            )

        # Publish emergency access used alert
        if self.redis:
            await self.redis.publish(
                "security:alerts",
                json.dumps({
                    "type": "emergency_access_used",
                    "request_id": str(request_uuid),
                    "requestor_id": str(row["requestor_id"]),
                    "scenario": row["scenario"],
                    "severity": "critical"
                })
            )

        return EmergencySession(
            session_token=session_token,
            request_id=request_uuid,
            requestor_id=row["requestor_id"],
            created_at=datetime.utcnow(),
            expires_at=row["expires_at"],
            permissions=row["requested_access"],
        )

    async def validate_session(
        self,
        session_token: str,
    ) -> Optional[EmergencySession]:
        """Validate an emergency session token"""
        token_hash = hashlib.sha256(session_token.encode()).hexdigest()

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM emergency_access_requests
                WHERE session_token_hash = $1
                AND status = $2
                AND expires_at > NOW()
                AND session_terminated_at IS NULL
                """,
                token_hash, EmergencyStatus.USED.value
            )

        if not row:
            return None

        return EmergencySession(
            session_token=session_token,
            request_id=row["request_id"],
            requestor_id=row["requestor_id"],
            created_at=row["session_created_at"],
            expires_at=row["expires_at"],
            permissions=row["requested_access"],
        )

    async def terminate_session(
        self,
        request_id: UUID | str,
        terminated_by: Optional[UUID | str] = None,
        reason: str = "Session completed",
    ) -> bool:
        """Terminate an emergency session early"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id
        terminator_uuid = UUID(terminated_by) if isinstance(terminated_by, str) else terminated_by

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE emergency_access_requests
                SET session_terminated_at = $1, actions_taken = actions_taken || $2::jsonb
                WHERE request_id = $3
                """,
                datetime.utcnow(),
                json.dumps([{
                    "action": "terminated",
                    "by": str(terminator_uuid) if terminator_uuid else "system",
                    "at": datetime.utcnow().isoformat(),
                    "reason": reason
                }]),
                request_uuid
            )

        return True

    async def log_action(
        self,
        request_id: UUID | str,
        action: str,
        details: dict,
    ) -> bool:
        """Log an action taken during emergency session"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id

        action_entry = {
            "action": action,
            "details": details,
            "at": datetime.utcnow().isoformat(),
        }

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE emergency_access_requests
                SET actions_taken = actions_taken || $1::jsonb
                WHERE request_id = $2
                """,
                json.dumps([action_entry]), request_uuid
            )

        return True

    async def complete_review(
        self,
        request_id: UUID | str,
        reviewed_by: UUID | str,
        notes: str,
    ) -> bool:
        """Complete post-incident review of emergency access"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id
        reviewer_uuid = UUID(reviewed_by) if isinstance(reviewed_by, str) else reviewed_by

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE emergency_access_requests
                SET reviewed_at = $1, reviewed_by = $2, review_notes = $3
                WHERE request_id = $4
                """,
                datetime.utcnow(), reviewer_uuid, notes, request_uuid
            )

        return True

    async def get_pending_requests(
        self,
    ) -> list[EmergencyAccessRequest]:
        """Get all pending emergency access requests"""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM emergency_access_requests
                WHERE status = $1 AND expires_at > NOW()
                ORDER BY created_at DESC
                """,
                EmergencyStatus.PENDING.value
            )

        return [self._row_to_model(row) for row in rows]

    async def get_request(
        self,
        request_id: UUID | str,
    ) -> Optional[EmergencyAccessRequest]:
        """Get a specific emergency access request"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM emergency_access_requests WHERE request_id = $1",
                request_uuid
            )

        return self._row_to_model(row) if row else None

    def _row_to_model(self, row: asyncpg.Record) -> EmergencyAccessRequest:
        """Convert database row to EmergencyAccessRequest"""
        approvals_data = json.loads(row["approvals"]) if row["approvals"] else []
        approvals = [
            ApprovalInfo(
                approver_id=a["approver_id"],
                approved_at=a["approved_at"],
                method=a["method"],
                verification_code=a.get("verification_code")
            )
            for a in approvals_data
        ]

        return EmergencyAccessRequest(
            request_id=row["request_id"],
            created_at=row["created_at"],
            requestor_id=row["requestor_id"],
            scenario=EmergencyScenario(row["scenario"]),
            justification=row["justification"],
            requested_access=row["requested_access"],
            approvers_required=row["approvers_required"],
            approvals=approvals,
            status=EmergencyStatus(row["status"]),
            approved_at=row["approved_at"],
            expires_at=row["expires_at"],
            session_created_at=row["session_created_at"],
            session_terminated_at=row["session_terminated_at"],
            session_token_hash=row["session_token_hash"],
            actions_taken=json.loads(row["actions_taken"]) if row["actions_taken"] else [],
            reviewed_at=row["reviewed_at"],
            reviewed_by=row["reviewed_by"],
            review_notes=row["review_notes"],
        )


# Convenience function for dependency injection
async def get_emergency_access(
    db_pool: asyncpg.Pool,
    redis_client: Optional[aioredis.Redis] = None
) -> EmergencyAccess:
    """Factory function for creating emergency access service"""
    return EmergencyAccess(db_pool, redis_client)
