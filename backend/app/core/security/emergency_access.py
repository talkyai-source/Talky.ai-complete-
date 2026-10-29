"""
Emergency Access Module - Break-glass dual-control access
"""
import hashlib
import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

import redis.asyncio as aioredis
import asyncpg
from pydantic import BaseModel
from fastapi import HTTPException

from app.core.db_utils import acquire_with_tenant
from app.core.security.credential_issuance import lock_authenticated_credential_change
from app.core.security.totp import decrypt_totp_secret, verify_totp_step
from app.core.security.rbac import UserRole, normalize_role

logger = logging.getLogger(__name__)

APPROVAL_ATTEMPT_LIMIT = 5
APPROVAL_ATTEMPT_WINDOW_SECONDS = 300
APPROVAL_ATTEMPT_TIMEOUT_SECONDS = 2.0
# Same fixed-window atomic claim/TTL repair used by password-reset sends.
# The independent Redis claim survives a rejected database transaction.
_CLAIM_APPROVAL_ATTEMPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 or redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return n
"""


def _verified_approver_ids(row):
    approvals = row["approvals"]
    if isinstance(approvals, str):
        approvals = json.loads(approvals)
    return {str(approval["approver_id"]) for approval in (approvals or [])
            if approval.get("verification") == "totp_step_up" and approval.get("method") == "totp"
            and str(approval["approver_id"]) != str(row["requestor_id"])}


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
    method: str
    verification: Optional[str] = None


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

    async def _publish(self, channel, payload):
        """The committed approval/session stays authoritative during Redis loss."""
        try:
            await self.redis.publish(channel, payload)
        except Exception as exc:
            logger.warning("emergency_notification_failed error_type=%s", type(exc).__name__)

    async def _claim_approval_attempt(self, user_id):
        if self.redis is None:
            raise HTTPException(503, "Emergency approval verification is temporarily unavailable")
        try:
            count = await asyncio.wait_for(self.redis.eval(
                _CLAIM_APPROVAL_ATTEMPT, 1, f"emergency:approval-attempts:{user_id}",
                APPROVAL_ATTEMPT_WINDOW_SECONDS,
            ), timeout=APPROVAL_ATTEMPT_TIMEOUT_SECONDS)
            if type(count) is not int or count < 1:
                raise ValueError("Unconfirmed verification-attempt claim")
        except Exception as exc:
            logger.warning("emergency_approval_budget_unavailable error_type=%s", type(exc).__name__)
            raise HTTPException(503, "Emergency approval verification is temporarily unavailable") from exc
        if count > APPROVAL_ATTEMPT_LIMIT:
            raise HTTPException(429, "Too many verification attempts. Try again later.",
                                headers={"Retry-After": str(APPROVAL_ATTEMPT_WINDOW_SECONDS)})

    @asynccontextmanager
    async def _authorized_write(self, auth_request, current_user):
        """Keep the authenticated profile/session fence through the mutation."""
        if auth_request is None or current_user is None:
            raise HTTPException(403, "Authenticated platform administrator required")
        async with acquire_with_tenant(self.db_pool, current_user.tenant_id) as conn:
            locked = await lock_authenticated_credential_change(conn, auth_request, current_user)
            if normalize_role(locked["role"]) != UserRole.PLATFORM_ADMIN:
                raise HTTPException(403, "Platform admin access required")
            yield conn

    async def request(
        self,
        requestor_id: UUID | str,
        scenario: EmergencyScenario,
        justification: str,
        required_access: list[str],
        *, auth_request=None, current_user=None,
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
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        request = EmergencyAccessRequest(
            request_id=request_id,
            created_at=datetime.now(timezone.utc),
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
            actions_taken=[{"action": "requested", "actor_id": str(requestor_uuid),
                            "at": datetime.now(timezone.utc).isoformat()}],
            reviewed_at=None,
            reviewed_by=None,
            review_notes=None,
        )

        async with self._authorized_write(auth_request, current_user) as conn:
            if str(requestor_uuid) != str(current_user.id):
                raise HTTPException(403, "Requestor does not match the authenticated administrator")
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
                # Pass raw Python objects, not pre-dumped JSON strings — the
                # pool's jsonb codec (app.core.db) already encodes via
                # json.dumps on write. Passing an already-serialized string
                # here would get double-encoded into a JSON string scalar.
                [], EmergencyStatus.PENDING.value, expires_at,
                request.actions_taken
            )

        # Notify potential approvers via Redis
        if self.redis:
            await self._publish(
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
        method: str = "totp",
        verification_code: Optional[str] = None,
        *, auth_request=None, current_user=None,
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

        if method != "totp" or not verification_code:
            raise HTTPException(400, "A current TOTP code is required")
        async with self._authorized_write(auth_request, current_user) as conn:
            if str(approver_uuid) != str(current_user.id):
                raise HTTPException(403, "Approver does not match the authenticated administrator")
            await self._claim_approval_attempt(approver_uuid)
            mfa = await conn.fetchrow(
                "SELECT totp_secret_enc, enabled, last_used_at FROM user_mfa WHERE user_id=$1 FOR UPDATE",
                str(approver_uuid),
            )
            if not mfa or not mfa["enabled"]:
                raise HTTPException(400, "Enabled TOTP MFA is required for emergency approval")
            try:
                step = verify_totp_step(decrypt_totp_secret(mfa["totp_secret_enc"]), verification_code,
                                        last_used_at=mfa["last_used_at"])
            except RuntimeError as exc:
                raise HTTPException(503, "MFA verification is temporarily unavailable") from exc
            if step is None:
                raise HTTPException(401, "Invalid or already used TOTP code")
            row = await conn.fetchrow(
                "SELECT * FROM emergency_access_requests WHERE request_id = $1 FOR UPDATE",
                request_uuid
            )

            if not row:
                raise ValueError("Request not found")

            if row["status"] != EmergencyStatus.PENDING.value:
                raise ValueError(f"Request cannot be approved (status: {row['status']})")

            if row["expires_at"] < datetime.now(timezone.utc):
                raise ValueError("Request has expired")

            # Check if approver is the requestor (can't approve own request)
            if row["requestor_id"] == approver_uuid:
                raise ValueError("Cannot approve your own emergency request")

            # Load existing approvals. The pool's jsonb codec (app.core.db)
            # already decodes this to a list; guard covers pools/paths
            # that still hand back a raw JSON string.
            raw_approvals = row["approvals"]
            approvals = raw_approvals if isinstance(raw_approvals, list) else (
                json.loads(raw_approvals) if raw_approvals else []
            )
            # Legacy records did not verify their submitted code. Preserve
            # non-secret audit metadata, but never count those as MFA proof.
            approvals = [{k: v for k, v in approval.items() if k != "verification_code"}
                         for approval in approvals]

            # Check if already approved by this person
            if any(a["approver_id"] == str(approver_uuid) and a.get("verification") == "totp_step_up"
                   for a in approvals):
                raise ValueError("You have already approved this request")

            # Add approval
            approval = {
                "approver_id": str(approver_uuid),
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "method": method,
                "verification": "totp_step_up",
            }
            approvals.append(approval)
            audit_action = [{"action": "approval_verified", "actor_id": str(approver_uuid),
                             "at": approval["approved_at"]}]

            # Check if fully approved
            approvers_required = row["approvers_required"]
            verified_count = len(_verified_approver_ids({**dict(row), "approvals": approvals}))
            is_fully_approved = verified_count >= approvers_required
            await conn.execute("UPDATE user_mfa SET last_used_at=$2 WHERE user_id=$1", str(approver_uuid), step)

            if is_fully_approved:
                # Calculate session expiration
                scenario = EmergencyScenario(row["scenario"])
                session_ttl_hours = self.SESSION_TTL.get(scenario, 4)
                new_expires_at = datetime.now(timezone.utc) + timedelta(hours=session_ttl_hours)

                await conn.execute(
                    """
                    UPDATE emergency_access_requests
                    SET approvals = $1, status = $2, approved_at = $3, expires_at = $4,
                        actions_taken = actions_taken || $6::jsonb
                    WHERE request_id = $5
                    """,
                    approvals, EmergencyStatus.APPROVED.value,
                    datetime.now(timezone.utc), new_expires_at, request_uuid, audit_action
                )
            else:
                await conn.execute(
                    "UPDATE emergency_access_requests SET approvals = $1, actions_taken = actions_taken || $3::jsonb WHERE request_id = $2",
                    approvals, request_uuid, audit_action
                )

        # Notify requestor if fully approved
        if is_fully_approved and self.redis:
            await self._publish(
                f"emergency:user:{row['requestor_id']}",
                json.dumps({
                    "type": "request_approved",
                    "request_id": str(request_uuid),
                    "approvals_received": verified_count,
                    "session_ready": True
                })
            )

        return {
            "request_id": request_uuid,
            "approved": True,
            "fully_approved": is_fully_approved,
            "approvals_received": verified_count,
            "approvals_required": approvers_required,
        }

    async def deny(
        self,
        request_id: UUID | str,
        denied_by: UUID | str,
        reason: str,
        *, auth_request=None, current_user=None,
    ) -> dict:
        """Deny an emergency access request"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id
        denier_uuid = UUID(denied_by) if isinstance(denied_by, str) else denied_by

        async with self._authorized_write(auth_request, current_user) as conn:
            if str(denier_uuid) != str(current_user.id):
                raise HTTPException(403, "Denier does not match the authenticated administrator")
            updated = await conn.execute(
                """
                UPDATE emergency_access_requests
                SET status = $1, reviewed_by = $2, reviewed_at = $3, review_notes = $4,
                    actions_taken = actions_taken || $6::jsonb
                WHERE request_id = $5 AND status IN ('pending', 'approved')
                """,
                EmergencyStatus.DENIED.value, denier_uuid, datetime.now(timezone.utc),
                reason, request_uuid, [{"action": "denied", "actor_id": str(denier_uuid),
                                       "at": datetime.now(timezone.utc).isoformat()}],
            )
            if updated != "UPDATE 1":
                raise ValueError("Request is not pending or approved")

        return {"request_id": request_uuid, "denied": True}

    async def create_session(
        self,
        request_id: UUID | str,
        *, auth_request=None, current_user=None,
    ) -> EmergencySession:
        """
        Create an emergency access session after approval.

        Args:
            request_id: Approved request

        Returns:
            EmergencySession with token
        """
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id

        async with self._authorized_write(auth_request, current_user) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM emergency_access_requests WHERE request_id = $1 FOR UPDATE",
                request_uuid
            )

            if not row:
                raise ValueError("Request not found")

            if str(row["requestor_id"]) != str(current_user.id):
                raise HTTPException(403, "Only the requestor can create the session")

            if row["status"] != EmergencyStatus.APPROVED.value:
                raise ValueError("Request not approved")

            if row["expires_at"] < datetime.now(timezone.utc):
                raise ValueError("Approval has expired")

            if row["session_created_at"]:
                raise ValueError("Session already created")

            if len(_verified_approver_ids(row)) < row["approvers_required"]:
                raise ValueError("Verified approvals are required; create a new request for legacy approvals")

            # Generate session token
            session_token = secrets.token_urlsafe(64)
            token_hash = hashlib.sha256(session_token.encode()).hexdigest()

            # Update request
            await conn.execute(
                """
                UPDATE emergency_access_requests
                SET session_created_at = $1, session_token_hash = $2, status = $3,
                    actions_taken = actions_taken || $5::jsonb
                WHERE request_id = $4
                """,
                datetime.now(timezone.utc), token_hash, EmergencyStatus.USED.value,
                request_uuid, [{"action": "session_created", "actor_id": str(current_user.id),
                                "at": datetime.now(timezone.utc).isoformat()}],
            )

        # Publish emergency access used alert
        if self.redis:
            await self._publish(
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
            created_at=datetime.now(timezone.utc),
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

        if not row or len(_verified_approver_ids(row)) < row["approvers_required"]:
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
        *, auth_request=None, current_user=None,
    ) -> bool:
        """Terminate an emergency session early"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id
        terminator_uuid = UUID(terminated_by) if isinstance(terminated_by, str) else terminated_by

        async with self._authorized_write(auth_request, current_user) as conn:
            if str(terminator_uuid) != str(current_user.id):
                raise HTTPException(403, "Terminator does not match the authenticated administrator")
            updated = await conn.execute(
                """
                UPDATE emergency_access_requests
                SET session_terminated_at = $1, actions_taken = actions_taken || $2::jsonb
                WHERE request_id = $3 AND status = 'used' AND session_terminated_at IS NULL
                """,
                datetime.now(timezone.utc),
                [{
                    "action": "terminated",
                    "by": str(terminator_uuid) if terminator_uuid else "system",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "reason": reason
                }],
                request_uuid
            )
            if updated != "UPDATE 1":
                raise ValueError("No active emergency session was terminated")

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
            "at": datetime.now(timezone.utc).isoformat(),
        }

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE emergency_access_requests
                SET actions_taken = actions_taken || $1::jsonb
                WHERE request_id = $2
                """,
                [action_entry], request_uuid
            )

        return True

    async def complete_review(
        self,
        request_id: UUID | str,
        reviewed_by: UUID | str,
        notes: str,
        *, auth_request=None, current_user=None,
    ) -> bool:
        """Complete post-incident review of emergency access"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id
        reviewer_uuid = UUID(reviewed_by) if isinstance(reviewed_by, str) else reviewed_by

        async with self._authorized_write(auth_request, current_user) as conn:
            if str(reviewer_uuid) != str(current_user.id):
                raise HTTPException(403, "Reviewer does not match the authenticated administrator")
            updated = await conn.execute(
                """
                UPDATE emergency_access_requests
                SET reviewed_at = $1, reviewed_by = $2, review_notes = $3,
                    actions_taken = actions_taken || $5::jsonb
                WHERE request_id = $4
                """,
                datetime.now(timezone.utc), reviewer_uuid, notes, request_uuid,
                [{"action": "review_completed", "actor_id": str(reviewer_uuid),
                  "at": datetime.now(timezone.utc).isoformat()}],
            )
            if updated != "UPDATE 1":
                raise ValueError("Request not found")

        return True

    async def get_pending_requests(
        self,
        *, auth_request=None, current_user=None,
    ) -> list[EmergencyAccessRequest]:
        """Get all pending emergency access requests"""
        return await self.list_requests(status=EmergencyStatus.PENDING, auth_request=auth_request, current_user=current_user)

    async def list_requests(self, *, status: EmergencyStatus | None = None, limit=50, offset=0,
                            auth_request=None, current_user=None):
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise ValueError("Invalid request page")
        async with self._authorized_write(auth_request, current_user) as conn:
            rows = await conn.fetch("""
                SELECT * FROM (
                    SELECT *, CASE WHEN status IN ('pending', 'approved') AND expires_at <= NOW()
                        THEN 'expired' ELSE status END AS effective_status
                    FROM emergency_access_requests
                ) requests WHERE ($1::text IS NULL OR effective_status=$1)
                ORDER BY created_at DESC, request_id DESC LIMIT $2 OFFSET $3
            """, status.value if status is not None else None, limit, offset)
        return [self._row_to_model(row) for row in rows]

    async def get_request(
        self,
        request_id: UUID | str,
        *, auth_request=None, current_user=None,
    ) -> Optional[EmergencyAccessRequest]:
        """Get a specific emergency access request"""
        request_uuid = UUID(request_id) if isinstance(request_id, str) else request_id

        async with self._authorized_write(auth_request, current_user) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM emergency_access_requests WHERE request_id = $1",
                request_uuid
            )

        return self._row_to_model(row) if row else None

    def _row_to_model(self, row: asyncpg.Record) -> EmergencyAccessRequest:
        """Convert database row to EmergencyAccessRequest"""
        # Expiry is derived consistently for both detail and paginated listing.
        # A rejected mutation rolls back; it must not pretend to persist expiry.
        status = row["status"]
        if status in {EmergencyStatus.PENDING.value, EmergencyStatus.APPROVED.value} and row["expires_at"] <= datetime.now(timezone.utc):
            status = EmergencyStatus.EXPIRED.value
        raw_approvals = row["approvals"]
        approvals_data = raw_approvals if isinstance(raw_approvals, list) else (
            json.loads(raw_approvals) if raw_approvals else []
        )
        approvals = [
            ApprovalInfo(
                approver_id=a["approver_id"],
                approved_at=a["approved_at"],
                method=a["method"],
                verification=a.get("verification")
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
            status=EmergencyStatus(status),
            approved_at=row["approved_at"],
            expires_at=row["expires_at"],
            session_created_at=row["session_created_at"],
            session_terminated_at=row["session_terminated_at"],
            session_token_hash=row["session_token_hash"],
            actions_taken=(
                row["actions_taken"] if isinstance(row["actions_taken"], list)
                else (json.loads(row["actions_taken"]) if row["actions_taken"] else [])
            ),
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
