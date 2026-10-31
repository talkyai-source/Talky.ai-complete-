"""
Suspension Service - Formalized suspension workflow with instant block propagation
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

import redis.asyncio as aioredis
import asyncpg
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.db_utils import acquire_with_tenant
from app.core.security.sessions import revoke_all_user_sessions
from app.core.security.refresh_tokens import revoke_all_user_refresh_tokens
from app.domain.services.audit_logger import AuditLogger, AuditEvent
from app.domain.services.formal_enforcement import (
    EVIDENCE_KEY as _ENFORCEMENT_KEY, capture_cohort, digest as _proof_digest,
    policy_digest as _policy_digest, project_enforcement,
)
from app.core.security.platform_admin_guard import (
    count_active_platform_admins,
    lock_user_administration,
    require_current_platform_admin,
)


# Evidence key under which a tenant/partner suspension records the exact
# tenant_users rows it flipped, so a restore returns only those rows.
_MEMBERS_KEY = "_suspended_tenant_user_ids"
_ACCOUNT_HOLD_KEY = "_formal_account_hold"
_ACCOUNT_STATE_FIELDS = ("status", "suspended_at", "suspended_by", "suspension_reason")
logger = logging.getLogger(__name__)


def _document(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _account_policy(event):
    """Only audited v1 account events can own restoration or an exemption.

    Old account writers accepted arbitrary evidence and never set audit_log_id.
    Their client-supplied reserved keys must not become trusted policy.
    """
    policy = _document(event.get("evidence")).get(_ACCOUNT_HOLD_KEY)
    if (not event.get("audit_log_id") or event.get("policy_audited") is not True or not isinstance(policy, dict)
            or type(policy.get("version")) is not int or policy["version"] != 1
            or type(policy.get("immediate")) is not bool):
        return None
    return policy


def _json_value(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _account_state(row):
    return {field: _json_value(row.get(field)) for field in _ACCOUNT_STATE_FIELDS}


def _hold_applies(event, tenant):
    tenant_id = str(tenant["id"])
    if event["target_type"] == "tenant":
        return str(event["target_id"]) == tenant_id
    if event["target_type"] != "partner":
        return False
    if str(event["target_id"]) == str(tenant.get("white_label_partner_id")):
        return True
    policy = _account_policy(event)
    return bool(policy and tenant_id in policy.get("affected_tenant_ids", []))


def _utcnow() -> datetime:
    """Aware UTC now: the suspension columns are TIMESTAMPTZ (asyncpg returns aware)."""
    return datetime.now(timezone.utc)


def validate_duration_hours(value, *, now=None):
    """Reject unrepresentable durations before a mutation transaction starts."""
    if value is None:
        return value
    if type(value) is not int or value <= 0:
        raise ValueError("Suspension duration must be a positive whole number of hours.")
    try:
        (now or _utcnow()) + timedelta(hours=value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Suspension duration exceeds the supported date range.") from exc
    return value


def _expiry(suspension_type, duration_hours, now):
    try:
        validate_duration_hours(duration_hours, now=now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return (now + timedelta(hours=duration_hours)
            if duration_hours is not None and suspension_type in {SuspensionType.TEMPORARY, SuspensionType.BILLING}
            else None)


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

    @field_validator("evidence", mode="before")
    @classmethod
    def _parse_evidence(cls, value):
        return _document(value) if value is not None else None

    @field_validator("propagated_services", mode="before")
    @classmethod
    def _null_means_none_propagated(cls, v):
        # The column is nullable and the INSERTs never set it, so new rows carry NULL.
        return [] if v is None else v


class SuspensionResult(BaseModel):
    """Result of suspension operation"""
    suspension_id: UUID
    target_type: TargetType
    target_id: UUID
    status: str
    propagated_to: list[str]
    propagation_failed: list[str]
    propagation_pending: list[str] = Field(default_factory=list)
    audit_event_id: Optional[UUID] = None
    revoked_sessions: Optional[int] = None
    revoked_refresh_tokens: Optional[int] = None
    invalidated_mfa_challenges: Optional[int] = None
    invalidated_passkey_challenges: Optional[int] = None
    changed: bool = True
    effective_status: Optional[str] = None
    remaining_hold_ids: list[UUID] = Field(default_factory=list)
    restoration_warnings: list[str] = Field(default_factory=list)


class SuspensionStatusResponse(BaseModel):
    """Status check response"""
    target_type: TargetType
    target_id: UUID
    status: SuspensionStatus
    is_suspended: bool
    active_suspension: Optional[SuspensionEvent]
    suspension_history: list[SuspensionEvent]
    effective_status: Optional[str] = None
    remaining_hold_ids: list[UUID] = Field(default_factory=list)
    history_complete: bool = True
    history_limit: int = 200


class SuspensionService:
    """
    Durable formal holds with separately reported downstream enforcement.

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
        propagation_timeout_seconds: int = 30,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.db_pool = db_pool
        self.redis = redis_client
        self.propagation_timeout = propagation_timeout_seconds
        self._audit_logger = audit_logger

    def _required_audit(self):
        if self._audit_logger is None:
            self._audit_logger = AuditLogger(self.db_pool)
        return self._audit_logger

    async def _propagate_committed_user_change(self, suspension_id, user_id, action, event):
        """Report delivery separately from the already committed user change.

        Hint publication failure must not turn a durable suspension,
        restoration or granted appeal into an apparent mutation failure. A
        publish acknowledgement alone is not confirmation from each service.
        """
        try:
            propagation = await self._propagate_to_services(
                suspension_id=suspension_id, target_type=TargetType.USER,
                target_id=user_id, action=action,
            )
        except Exception as exc:
            logger.warning("Committed user suspension propagation unavailable: %s", type(exc).__name__)
            propagation = {"success": [], "failed": ["assistant_session_check"], "pending": []}
        if self.redis:
            try:
                await asyncio.wait_for(self.redis.publish("suspension:events", json.dumps(event)), timeout=1.0)
            except Exception as exc:
                logger.warning("Committed user suspension notification unavailable: %s", type(exc).__name__)
                propagation["failed"] = [*propagation["failed"], "suspension_events"]
        return propagation

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

        suspended_until = _expiry(suspension_type, duration_hours, _utcnow())

        # Platform suspension starts from a user id before tenant discovery.
        audit = self._required_audit()
        async with acquire_with_tenant(self.db_pool, None) as conn:
            await audit.lock_transaction(conn)
            await lock_user_administration(conn)
            if admin_uuid is not None:
                await require_current_platform_admin(conn, admin_uuid)
            target = await conn.fetchrow(
                "SELECT id,role,tenant_id,is_active FROM user_profiles WHERE id=$1 FOR UPDATE", user_uuid
            )
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")
            if admin_uuid == user_uuid:
                raise HTTPException(status_code=400, detail="You cannot suspend your own account")
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM suspension_events "
                "WHERE target_type='user' AND target_id=$1 AND is_active=TRUE)", user_uuid
            ):
                raise HTTPException(status_code=409, detail="This user already has an active formal suspension. Review the existing suspension.")
            if not target["is_active"]:
                raise HTTPException(status_code=409, detail="This account is already inactive. Review its existing block before creating a formal suspension.")
            if target["role"] == "platform_admin" and not await count_active_platform_admins(
                conn, exclude_id=user_uuid
            ):
                raise HTTPException(
                    status_code=400, detail="Cannot remove the last active platform admin"
                )
            async with conn.transaction():
                # Create suspension event
                suspension_id = uuid4()
                cutoff = await conn.fetchval("SELECT clock_timestamp()")
                suspended_until = _expiry(suspension_type, duration_hours, cutoff)
                enforcement = await capture_cohort(conn, suspension_id=suspension_id,
                    target_type="user",target_id=user_uuid,cutoff=cutoff,
                    tenant_ids=[target["tenant_id"]] if target["tenant_id"] else [],subject_ids=[user_uuid])
                saved_evidence = {k:v for k,v in (evidence or {}).items() if k != _ENFORCEMENT_KEY}
                saved_evidence[_ENFORCEMENT_KEY] = enforcement
                await conn.execute(
                    """
                    INSERT INTO suspension_events (
                        suspension_id, target_type, target_id, suspension_type,
                        reason_category, reason_description, evidence,
                        suspended_at, suspended_until, suspended_by, is_active
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $11, $8, $9, $10)
                    """,
                    suspension_id, TargetType.USER.value, user_uuid, suspension_type.value,
                    reason_category, reason_description, json.dumps(saved_evidence),
                    suspended_until, admin_uuid, True, cutoff
                )

                # Update user profile
                await conn.execute(
                    "UPDATE user_profiles SET is_active = FALSE, updated_at = NOW() WHERE id = $1",
                    user_uuid
                )
                # The profile FOR UPDATE above is also every auth issuer's
                # first row lock. Revoke before commit so restored access can
                # never reactivate credentials from before this security hold.
                revoked_sessions = await revoke_all_user_sessions(conn, str(user_uuid), reason="user_suspended")
                revoked_refresh_tokens = await revoke_all_user_refresh_tokens(conn, str(user_uuid), reason="admin")
                mfa_result = await conn.execute(
                    "UPDATE mfa_challenges SET used=TRUE,used_at=clock_timestamp() WHERE user_id=$1 AND used=FALSE",
                    user_uuid,
                )
                passkey_result = await conn.execute(
                    """UPDATE webauthn_challenges SET used=TRUE,used_at=clock_timestamp()
                       WHERE user_id=$1 AND used=FALSE""", user_uuid,
                )
                revoked = {"revoked_sessions": revoked_sessions, "revoked_refresh_tokens": revoked_refresh_tokens,
                           "invalidated_mfa_challenges": int(mfa_result.split()[-1]),
                           "invalidated_passkey_challenges": int(passkey_result.split()[-1])}
                audit_event_id = await audit.log_in_transaction(
                    conn, event_type=AuditEvent.USER_SUSPENDED, action="user_suspended",
                    actor_id=admin_uuid, actor_type="user" if admin_uuid else "system",
                    actor_role="platform_admin" if admin_uuid else None,
                    tenant_id=target["tenant_id"], resource_type="user", resource_id=user_uuid,
                    before_state={"is_active": target["is_active"]}, after_state={"is_active": False, **revoked},
                    metadata={"suspension_id": str(suspension_id), "suspension_type": suspension_type.value,
                              "reason_category": reason_category, "duration_hours": duration_hours,
                              "formal_enforcement_version":1,"formal_enforcement_sha256":_proof_digest(enforcement)},
                )
                enforcement["original_suspend_audit_id"] = str(audit_event_id)
                await conn.execute("UPDATE suspension_events SET audit_log_id=$2,evidence=$3 WHERE suspension_id=$1",
                                   suspension_id, audit_event_id,json.dumps(saved_evidence))

        propagation_result = await self._propagate_committed_user_change(
            suspension_id, user_uuid, "suspend", {
                "type": "user_suspended", "suspension_id": str(suspension_id),
                "user_id": str(user_uuid), "suspension_type": suspension_type.value,
                "notify_user": notify_user,
            },
        )

        return SuspensionResult(
            suspension_id=suspension_id,
            target_type=TargetType.USER,
            target_id=user_uuid,
            status="suspended",
            audit_event_id=audit_event_id,
            **revoked,
            propagated_to=propagation_result["success"],
            propagation_failed=propagation_result["failed"],
            propagation_pending=propagation_result.get("pending", []),
        )

    async def _account_events(self, conn):
        return [dict(row) for row in await conn.fetch(
            "SELECT e.*,a.metadata->'formal_account_hold_version'='1'::jsonb AS policy_audited "
            "FROM suspension_events e LEFT JOIN audit_logs a ON a.event_id=e.audit_log_id "
            "WHERE e.target_type IN ('tenant','partner') AND e.is_active=TRUE "
            "ORDER BY e.suspended_at,e.suspension_id"
        )]

    async def _account_profiles(self, conn, tenant_ids):
        # Issuers take the profile fence before credentials. Take every affected
        # profile in UUID order before membership/session writes, so an issuance
        # already under way either commits before revocation or sees the hold.
        return await conn.fetch(
            """SELECT up.id FROM user_profiles up
               WHERE EXISTS(SELECT 1 FROM tenant_users tu WHERE tu.user_id=up.id
                            AND tu.tenant_id=ANY($1::uuid[]))
               ORDER BY up.id FOR UPDATE OF up""", tenant_ids,
        )

    async def _hold_account_row(self, conn, table, row, inherited, now, actor, reason):
        current = _account_state(row)
        for candidate in inherited:
            if candidate and candidate.get("held") == current:
                return json.loads(json.dumps(candidate))
        if row["status"] != "active":
            # A prior independent restriction has no restoration permission.
            return {"prior": current, "held": current}
        # Only internal table constants reach this statement.
        saved = await conn.fetchrow(
            f"""UPDATE {table} SET status='suspended',suspended_at=$2,suspended_by=$3,
                suspension_reason=$4,updated_at=clock_timestamp() WHERE id=$1
                RETURNING status,suspended_at,suspended_by,suspension_reason""",
            row["id"], now, actor, reason,
        )
        return {"prior": current, "held": _account_state(saved)}

    async def _hold_account_members(self, conn, tenant_ids, active):
        inherited = {}
        for event in active:
            policy = _account_policy(event)
            if policy:
                for member in policy.get("member_state", []):
                    inherited.setdefault(member["id"], []).append(member)
        rows = await conn.fetch(
            "SELECT id,tenant_id,status,updated_at FROM tenant_users "
            "WHERE tenant_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", tenant_ids,
        )
        saved = []
        for row in rows:
            if row["status"] == "active":
                version = await conn.fetchval(
                    "UPDATE tenant_users SET status='suspended',updated_at=clock_timestamp() WHERE id=$1 "
                    "RETURNING updated_at", row["id"],
                )
                saved.append({"id": str(row["id"]), "tenant_id": str(row["tenant_id"]),
                              "prior_status": "active", "held_updated_at": _json_value(version)})
            elif row["status"] == "suspended":
                for member in inherited.get(str(row["id"]), []):
                    if (member.get("tenant_id") == str(row["tenant_id"])
                            and member.get("held_updated_at") == _json_value(row["updated_at"])):
                        saved.append(dict(member))
                        break
        return saved

    async def _effective_account(self, conn, target_type, target_id):
        table = "tenants" if target_type == TargetType.TENANT else "white_label_partners"
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1", target_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Account not found")
        active = await self._account_events(conn)
        if target_type == TargetType.TENANT:
            holds = [e["suspension_id"] for e in active if _hold_applies(e, row)]
            parent_status = None
            if row["white_label_partner_id"]:
                parent_status = await conn.fetchval(
                    "SELECT status FROM white_label_partners WHERE id=$1", row["white_label_partner_id"]
                )
            effective = "suspended" if holds or (parent_status is not None and parent_status != "active") else row["status"]
        else:
            holds = [e["suspension_id"] for e in active if e["target_type"] == "partner" and e["target_id"] == target_id]
            effective = "suspended" if holds else row["status"]
        return effective or "unknown", holds

    async def _account_change_hint(self, result, action):
        # Durable state and audit already committed. No unsupported queue/bus is
        # required, and a Redis acknowledgement is never a consumer receipt.
        result.propagation_pending = ["telephony_policy_check"]
        if self.redis:
            try:
                await asyncio.wait_for(self.redis.publish("suspension:events", json.dumps({
                    "type": f"{result.target_type.value}_{action}",
                    "suspension_id": str(result.suspension_id),
                    "target_id": str(result.target_id),
                })), timeout=1.0)
            except Exception as exc:
                logger.warning("Committed account hold hint unavailable: %s", type(exc).__name__)
                result.propagation_failed = ["suspension_events"]
        return result

    async def _suspend_account(self, target_type, target_id, suspension_type, reason_category,
                               reason_description, evidence, actor, duration_hours, immediate):
        target_id = UUID(str(target_id))
        actor = UUID(str(actor)) if actor is not None else None
        if actor is None:
            raise HTTPException(status_code=400, detail="A current platform administrator is required.")
        _expiry(suspension_type, duration_hours, _utcnow())
        audit = self._required_audit()
        request_policy = {"suspension_type": suspension_type.value, "reason_category": reason_category,
                          "reason_description": reason_description, "duration_hours": duration_hours,
                          "immediate": bool(immediate),
                          "evidence": {k: v for k, v in (evidence or {}).items()
                                       if k not in {_ACCOUNT_HOLD_KEY, _MEMBERS_KEY, _ENFORCEMENT_KEY}}}
        async with acquire_with_tenant(self.db_pool, None) as conn:
            await audit.lock_transaction(conn)
            await lock_user_administration(conn)
            await require_current_platform_admin(conn, actor)
            table = "tenants" if target_type == TargetType.TENANT else "white_label_partners"
            target = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1 FOR NO KEY UPDATE", target_id)
            if target is None:
                raise HTTPException(status_code=404, detail="Account not found")
            active = await self._account_events(conn)
            same_target = [e for e in active if e["target_type"] == target_type.value and e["target_id"] == target_id]
            if same_target:
                if len(same_target) != 1:
                    raise HTTPException(status_code=409, detail="Multiple active holds require operator review.")
                existing = same_target[0]
                policy = _account_policy(existing)
                if not policy or policy.get("request") != request_policy:
                    raise HTTPException(status_code=409, detail="An active hold has different policy. Review it before changing the restriction.")
                effective, holds = await self._effective_account(conn, target_type, target_id)
                result = SuspensionResult(
                    suspension_id=existing["suspension_id"], target_type=target_type, target_id=target_id,
                    status="suspended", changed=False, audit_event_id=existing["audit_log_id"],
                    effective_status=effective, remaining_hold_ids=holds, propagated_to=[], propagation_failed=[],
                    revoked_sessions=policy.get("revoked_sessions"),
                )
            else:
                if target_type == TargetType.TENANT:
                    tenants = [target]
                else:
                    tenants = await conn.fetch(
                        "SELECT * FROM tenants WHERE white_label_partner_id=$1 ORDER BY id FOR NO KEY UPDATE", target_id
                    )
                tenant_ids = [row["id"] for row in tenants]
                profiles = await self._account_profiles(conn, tenant_ids)
                now = await conn.fetchval("SELECT clock_timestamp()")
                suspended_until = _expiry(suspension_type, duration_hours, now)
                policy = {"version": 1, "immediate": bool(immediate),
                          "affected_tenant_ids": [str(v) for v in tenant_ids],
                          "tenant_state": {}, "partner_state": None, "member_state": [], "request": request_policy}
                for row in tenants:
                    inherited = [
                        p.get("tenant_state", {}).get(str(row["id"]))
                        for event in active if _hold_applies(event, row) and (p := _account_policy(event))
                    ]
                    policy["tenant_state"][str(row["id"])] = await self._hold_account_row(
                        conn, "tenants", row, inherited, now, actor, reason_description,
                    )
                if target_type == TargetType.PARTNER:
                    policy["partner_state"] = await self._hold_account_row(
                        conn, "white_label_partners", target, [], now, actor, reason_description,
                    )
                policy["member_state"] = await self._hold_account_members(conn, tenant_ids, active)
                suspension_id = uuid4()
                enforcement = await capture_cohort(conn,suspension_id=suspension_id,
                    target_type=target_type.value,target_id=target_id,cutoff=now,
                    tenant_ids=tenant_ids,subject_ids=[row["id"] for row in profiles])
                revoked = 0
                if immediate:
                    for profile in profiles:
                        revoked += await revoke_all_user_sessions(
                            conn, str(profile["id"]), reason=f"{target_type.value}_suspended"
                        )
                policy["revoked_sessions"] = revoked
                enforcement["policy_sha256"] = _policy_digest(policy)
                # Caller evidence cannot replace server-owned restoration/policy.
                saved_evidence = {k: v for k, v in (evidence or {}).items()
                                  if k not in {_ACCOUNT_HOLD_KEY, _MEMBERS_KEY, _ENFORCEMENT_KEY}}
                saved_evidence[_ACCOUNT_HOLD_KEY] = policy
                saved_evidence[_MEMBERS_KEY] = [m["id"] for m in policy["member_state"]]
                saved_evidence[_ENFORCEMENT_KEY] = enforcement
                await conn.execute(
                    """INSERT INTO suspension_events(suspension_id,target_type,target_id,suspension_type,
                       reason_category,reason_description,evidence,suspended_at,suspended_until,suspended_by,is_active)
                       VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE)""",
                    suspension_id,target_type.value,target_id,suspension_type.value,reason_category,
                    reason_description,json.dumps(saved_evidence),now,suspended_until,actor,
                )
                audit_id = await audit.log_in_transaction(
                    conn,event_type=AuditEvent.TENANT_SUSPENDED,action=f"{target_type.value}_suspended",
                    actor_id=actor,actor_role="platform_admin",
                    tenant_id=target_id if target_type == TargetType.TENANT else target["tenant_id"],
                    resource_type=target_type.value,resource_id=target_id,
                    before_state=_account_state(target),
                    after_state={"suspension_id":str(suspension_id),"revoked_sessions":revoked,"immediate":immediate},
                    metadata={"formal_account_hold_version":1,"suspension_id":str(suspension_id),
                              "formal_enforcement_version":1,"formal_enforcement_sha256":_proof_digest(enforcement)},
                )
                enforcement["original_suspend_audit_id"] = str(audit_id)
                await conn.execute("UPDATE suspension_events SET audit_log_id=$2,evidence=$3 WHERE suspension_id=$1",
                                   suspension_id,audit_id,json.dumps(saved_evidence))
                effective, holds = await self._effective_account(conn, target_type, target_id)
                result = SuspensionResult(
                    suspension_id=suspension_id,target_type=target_type,target_id=target_id,status="suspended",
                    audit_event_id=audit_id,revoked_sessions=revoked,effective_status=effective,
                    remaining_hold_ids=holds,propagated_to=[],propagation_failed=[],
                )
        return await self._account_change_hint(result, "suspended")

    async def suspend_tenant(self, tenant_id, suspension_type, reason_category, reason_description,
                             evidence=None, suspended_by=None, duration_hours=None, immediate=True, notify_users=True):
        return await self._suspend_account(TargetType.TENANT, tenant_id, suspension_type, reason_category,
            reason_description, evidence, suspended_by, duration_hours, immediate)

    async def suspend_partner(self, partner_id, suspension_type, reason_category, reason_description,
                              evidence=None, suspended_by=None, duration_hours=None):
        return await self._suspend_account(TargetType.PARTNER, partner_id, suspension_type, reason_category,
            reason_description, evidence, suspended_by, duration_hours, True)

    async def restore_user(
        self,
        user_id: UUID | str,
        restored_by: Optional[UUID | str],
        reason: str,
        *,
        expected_suspension_id: Optional[UUID | str] = None,
        granted_appeal_response: Optional[str] = None,
    ) -> SuspensionResult:
        """Restore a suspended user"""
        user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
        admin_uuid = UUID(restored_by) if isinstance(restored_by, str) else restored_by
        if admin_uuid is None and expected_suspension_id is None:
            raise HTTPException(status_code=400, detail="Automatic restoration requires the exact expired hold.")

        # Platform restoration is keyed by user id before tenant discovery.
        audit = self._required_audit()
        async with acquire_with_tenant(self.db_pool, None) as conn:
            await audit.lock_transaction(conn)
            await lock_user_administration(conn)
            if admin_uuid is not None:
                await require_current_platform_admin(conn, admin_uuid)
            target = await conn.fetchrow(
                "SELECT id,tenant_id,is_active FROM user_profiles WHERE id=$1 FOR UPDATE", user_uuid
            )
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")
            async with conn.transaction():
                # Get active suspension
                active_suspensions = await conn.fetch(
                    """
                    SELECT suspension_id, suspension_type, suspended_until FROM suspension_events
                    WHERE target_type = $1 AND target_id = $2 AND is_active = TRUE
                    ORDER BY suspended_at DESC LIMIT 2 FOR UPDATE
                    """,
                    TargetType.USER.value, user_uuid
                )

                if not active_suspensions:
                    raise HTTPException(status_code=409, detail="No active formal suspension exists for this user. Review the account's block status.")
                if len(active_suspensions) != 1:
                    raise HTTPException(status_code=409, detail="This user has multiple active formal suspensions. Operator review is required before any hold can be restored.")

                suspension_id = active_suspensions[0]["suspension_id"]
                if expected_suspension_id is not None and str(suspension_id) != str(expected_suspension_id):
                    raise HTTPException(status_code=409, detail="The original suspension is no longer the active hold. Review the current suspension before restoring it.")
                if admin_uuid is None:
                    event = active_suspensions[0]
                    now = await conn.fetchval("SELECT clock_timestamp()")
                    if (
                        event["suspension_type"] not in {v.value for v in self.AUTO_RESTORE_TYPES}
                        or event["suspended_until"] is None or event["suspended_until"] > now
                    ):
                        raise HTTPException(status_code=409, detail="The exact hold is not eligible for automatic restoration.")
                if granted_appeal_response is not None:
                    appeal_decision = await conn.fetchval(
                        "SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1", suspension_id,
                    )
                    if appeal_decision not in {None, "pending"}:
                        raise HTTPException(status_code=409, detail="This appeal has already been reviewed; its decision cannot be replaced.")
                    await conn.execute(
                        "UPDATE suspension_events SET appeal_reviewed_by=$1,appeal_decision='granted',appeal_response=$2 WHERE suspension_id=$3",
                        admin_uuid, granted_appeal_response, suspension_id,
                    )

                # Update suspension event
                await conn.execute(
                    """
                    UPDATE suspension_events
                    SET is_active = FALSE, restored_at = clock_timestamp(), restored_by = $1, restore_reason = $2
                    WHERE suspension_id = $3
                    """,
                    admin_uuid, reason, suspension_id
                )

                # Restore user
                await conn.execute(
                    "UPDATE user_profiles SET is_active = TRUE, updated_at = NOW() WHERE id = $1",
                    user_uuid
                )
                audit_event_id = await audit.log_in_transaction(
                    conn, event_type=AuditEvent.USER_RESTORED, action="user_restored",
                    actor_id=admin_uuid, actor_type="user" if admin_uuid else "system",
                    actor_role="platform_admin" if admin_uuid else None,
                    tenant_id=target["tenant_id"], resource_type="user", resource_id=user_uuid,
                    before_state={"is_active": target["is_active"]}, after_state={"is_active": True},
                    metadata={"suspension_id": str(suspension_id), "restore_reason": reason,
                              "appeal_granted": granted_appeal_response is not None},
                )
                if granted_appeal_response is not None:
                    await conn.execute("UPDATE suspension_events SET audit_log_id=$2 WHERE suspension_id=$1",
                                       suspension_id, audit_event_id)

        propagation_result = await self._propagate_committed_user_change(
            suspension_id, user_uuid, "restore", {
                "type": "user_restored", "suspension_id": str(suspension_id),
                "user_id": str(user_uuid),
            },
        )

        return SuspensionResult(
            suspension_id=suspension_id,
            target_type=TargetType.USER,
            target_id=user_uuid,
            status="restored",
            audit_event_id=audit_event_id,
            propagated_to=propagation_result["success"],
            propagation_failed=propagation_result["failed"],
            propagation_pending=propagation_result.get("pending", []),
        )

    async def _restore_owned_account_row(self, conn, table, row, state, warnings):
        if not state or not isinstance(state.get("prior"), dict) or not isinstance(state.get("held"), dict):
            warnings.append(f"{table}:{row['id']}:ownership_unavailable")
            return False
        prior, held = state["prior"], state["held"]
        if prior == held:
            return True  # The hold did not change this pre-existing restriction.
        if _account_state(row) != held:
            warnings.append(f"{table}:{row['id']}:state_changed")
            return False
        at = datetime.fromisoformat(prior["suspended_at"]) if prior.get("suspended_at") else None
        by = UUID(prior["suspended_by"]) if prior.get("suspended_by") else None
        await conn.execute(
            f"""UPDATE {table} SET status=$2,suspended_at=$3,suspended_by=$4,
                suspension_reason=$5,updated_at=clock_timestamp() WHERE id=$1""",
            row["id"], prior.get("status"), at, by, prior.get("suspension_reason"),
        )
        return True

    async def _restore_account(self, target_type, target_id, actor, reason, *,
                               expected_suspension_id=None, granted_appeal_response=None):
        target_id = UUID(str(target_id))
        actor = UUID(str(actor)) if actor is not None else None
        expected = UUID(str(expected_suspension_id)) if expected_suspension_id else None
        audit = self._required_audit()
        async with acquire_with_tenant(self.db_pool, None) as conn:
            await audit.lock_transaction(conn)
            await lock_user_administration(conn)
            if actor is not None:
                await require_current_platform_admin(conn, actor)
            elif expected is None:
                raise HTTPException(status_code=400, detail="Automatic restoration requires the exact expired hold.")
            table = "tenants" if target_type == TargetType.TENANT else "white_label_partners"
            target = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1 FOR NO KEY UPDATE", target_id)
            if target is None:
                raise HTTPException(status_code=404, detail="Account not found")
            active = await self._account_events(conn)
            same_target = [e for e in active if e["target_type"] == target_type.value and e["target_id"] == target_id]
            if len(same_target) > 1:
                raise HTTPException(status_code=409, detail="Multiple active holds require operator review.")
            event = same_target[0] if same_target else None
            if event is None:
                event = await conn.fetchrow(
                    """SELECT e.*,a.metadata->'formal_account_hold_version'='1'::jsonb AS policy_audited
                       FROM suspension_events e LEFT JOIN audit_logs a ON a.event_id=e.audit_log_id
                       WHERE e.target_type=$1 AND e.target_id=$2
                       AND ($3::uuid IS NULL OR e.suspension_id=$3) ORDER BY e.suspended_at DESC LIMIT 1""",
                    target_type.value,target_id,expected,
                )
                if not event or event["is_active"] or not _account_policy(event):
                    raise HTTPException(status_code=409, detail="No supported restored hold receipt exists. Review the account.")
                if granted_appeal_response is not None and (
                    event["appeal_decision"] != "granted" or event["appeal_response"] != granted_appeal_response
                ):
                    raise HTTPException(status_code=409, detail="This hold was restored by a different decision; review its saved receipt.")
                effective, holds = await self._effective_account(conn,target_type,target_id)
                result = SuspensionResult(
                    suspension_id=event["suspension_id"],target_type=target_type,target_id=target_id,
                    status="restored",changed=False,audit_event_id=event["audit_log_id"],
                    effective_status=effective,remaining_hold_ids=holds,propagated_to=[],propagation_failed=[],
                    restoration_warnings=_account_policy(event).get("restoration_warnings", []),
                )
            else:
                if expected is not None and event["suspension_id"] != expected:
                    raise HTTPException(status_code=409, detail="The selected hold is no longer current. Refresh before restoring.")
                if granted_appeal_response is not None and event["appeal_decision"] not in {None, "pending"}:
                    raise HTTPException(status_code=409, detail="This appeal has already been reviewed; its decision cannot be replaced.")
                policy = _account_policy(event)
                if not policy:
                    raise HTTPException(status_code=409, detail="Historical hold restoration ownership is unavailable; operator reconciliation is required.")
                now = await conn.fetchval("SELECT clock_timestamp()")
                if actor is None and (
                    event["suspension_type"] not in {v.value for v in self.AUTO_RESTORE_TYPES}
                    or event["suspended_until"] is None or event["suspended_until"] > now
                ):
                    raise HTTPException(status_code=409, detail="The exact hold is not eligible for automatic restoration.")
                tenant_ids = [UUID(value) for value in policy.get("affected_tenant_ids", [])]
                tenants = await conn.fetch(
                    "SELECT * FROM tenants WHERE id=ANY($1::uuid[]) ORDER BY id FOR NO KEY UPDATE",tenant_ids,
                )
                await self._account_profiles(conn, tenant_ids)
                remaining = [e for e in active if e["suspension_id"] != event["suspension_id"]]
                warnings = []
                for tenant in tenants:
                    if any(_hold_applies(other, tenant) for other in remaining):
                        warnings.append(f"tenant:{tenant['id']}:another_hold_remains")
                        continue
                    owned = await self._restore_owned_account_row(
                        conn,"tenants",tenant,policy.get("tenant_state", {}).get(str(tenant["id"])),warnings,
                    )
                    if not owned:
                        continue
                    for member in policy.get("member_state", []):
                        if member.get("tenant_id") != str(tenant["id"]):
                            continue
                        # Exact row AND timestamp ownership: removed, manually
                        # edited or reparented memberships never reactivate.
                        await conn.execute(
                            """UPDATE tenant_users SET status=$4,updated_at=clock_timestamp()
                               WHERE id=$1 AND tenant_id=$2 AND status='suspended' AND updated_at=$3""",
                            UUID(member["id"]),tenant["id"],datetime.fromisoformat(member["held_updated_at"]),
                            member["prior_status"],
                        )
                if target_type == TargetType.PARTNER:
                    await self._restore_owned_account_row(conn,"white_label_partners",target,policy.get("partner_state"),warnings)
                if granted_appeal_response is not None:
                    await conn.execute(
                        """UPDATE suspension_events SET appeal_reviewed_by=$1,appeal_decision='granted',
                           appeal_response=$2 WHERE suspension_id=$3""",
                        actor,granted_appeal_response,event["suspension_id"],
                    )
                evidence = _document(event["evidence"])
                policy["restoration_warnings"] = warnings
                evidence[_ACCOUNT_HOLD_KEY] = policy
                await conn.execute(
                    """UPDATE suspension_events SET is_active=FALSE,restored_at=$2,restored_by=$3,
                       restore_reason=$4,evidence=$5 WHERE suspension_id=$1""",
                    event["suspension_id"],now,actor,reason,json.dumps(evidence),
                )
                effective, holds = await self._effective_account(conn,target_type,target_id)
                audit_id = await audit.log_in_transaction(
                    conn,event_type=AuditEvent.TENANT_RESTORED,action=f"{target_type.value}_restored",
                    actor_id=actor,actor_type="user" if actor else "system",
                    actor_role="platform_admin" if actor else None,
                    tenant_id=target_id if target_type == TargetType.TENANT else target["tenant_id"],
                    resource_type=target_type.value,resource_id=target_id,
                    before_state={"suspension_id":str(event["suspension_id"]),"is_active":True},
                    after_state={"is_active":False,"effective_status":effective,
                                 "remaining_hold_ids":[str(v) for v in holds],"restoration_warnings":warnings},
                    metadata={"formal_account_hold_version":1,"suspension_id":str(event["suspension_id"]),
                              "restore_reason":reason,"appeal_granted":granted_appeal_response is not None},
                )
                await conn.execute("UPDATE suspension_events SET audit_log_id=$2 WHERE suspension_id=$1",
                                   event["suspension_id"],audit_id)
                result = SuspensionResult(
                    suspension_id=event["suspension_id"],target_type=target_type,target_id=target_id,status="restored",
                    audit_event_id=audit_id,effective_status=effective,remaining_hold_ids=holds,
                    restoration_warnings=warnings,propagated_to=[],propagation_failed=[],
                )
        return await self._account_change_hint(result,"restored")

    async def restore_tenant(self, tenant_id, restored_by, reason, *,
                             expected_suspension_id=None, granted_appeal_response=None):
        return await self._restore_account(TargetType.TENANT,tenant_id,restored_by,reason,
            expected_suspension_id=expected_suspension_id,granted_appeal_response=granted_appeal_response)

    async def get_status(
        self,
        user_id: Optional[UUID | str] = None,
        tenant_id: Optional[UUID | str] = None,
        partner_id: Optional[UUID | str] = None,
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
        elif partner_id:
            target_type = TargetType.PARTNER
            target_uuid = UUID(str(partner_id))
        else:
            raise ValueError("Must provide user_id, tenant_id or partner_id")

        async with acquire_with_tenant(self.db_pool, None) as conn:
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            table = {TargetType.USER:"user_profiles",TargetType.TENANT:"tenants",TargetType.PARTNER:"white_label_partners"}[target_type]
            target_row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1",target_uuid)
            if target_row is None:
                raise HTTPException(status_code=404,detail="Suspension target not found")
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
                ORDER BY suspended_at DESC, suspension_id DESC LIMIT 201
                """,
                target_type.value, target_uuid
            )
            if target_type in {TargetType.TENANT,TargetType.PARTNER}:
                effective_status, remaining_hold_ids = await self._effective_account(conn, target_type, target_uuid)
            else:
                from app.core.security.principal import load_current_principal, PrincipalUnavailable
                from app.domain.services.telephony.call_authority import read_call_authority_rows

                effective_status = "inactive"
                if target_row["is_active"]:
                    try:
                        await load_current_principal(conn,target_uuid)
                        effective_status = "active"
                    except PrincipalUnavailable:
                        effective_status = "restricted"
                # Current restrictions are independent of the bounded history
                # page and the original home tenant captured by an older hold.
                remaining = {row["suspension_id"] for row in await conn.fetch(
                    "SELECT suspension_id FROM suspension_events WHERE target_type='user' AND target_id=$1 AND is_active",
                    target_uuid,
                )}
                if target_row.get("role") != "platform_admin" and target_row.get("tenant_id"):
                    policies = await read_call_authority_rows(conn,[str(target_row["tenant_id"])])
                    for row in policies.values():
                        holds = row["active_holds"]
                        holds = json.loads(holds) if isinstance(holds,str) else holds
                        remaining.update(UUID(str(item["suspension_id"])) for item in holds)
                remaining_hold_ids = sorted(remaining,key=str)

        active_suspension = SuspensionEvent(**dict(active_row)) if active_row else None
        history = [SuspensionEvent(**dict(row)) for row in history_rows[:200]]

        if active_suspension:
            if active_suspension.suspended_until and active_suspension.suspended_until > _utcnow():
                status = SuspensionStatus.SCHEDULED_RESTORE
            elif active_suspension.appeal_submitted_at and active_suspension.appeal_decision == "pending":
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
            is_suspended=is_suspended or bool(remaining_hold_ids) or (
                effective_status is not None and effective_status != "active"),
            effective_status=effective_status,
            remaining_hold_ids=remaining_hold_ids,
            active_suspension=active_suspension,
            suspension_history=history,history_complete=len(history_rows)<=200,history_limit=200,
        )

    async def restore_partner(self, partner_id, restored_by, reason, *,
                              expected_suspension_id=None, granted_appeal_response=None):
        return await self._restore_account(TargetType.PARTNER,partner_id,restored_by,reason,
            expected_suspension_id=expected_suspension_id,granted_appeal_response=granted_appeal_response)

    async def submit_appeal(
        self,
        suspension_id: UUID | str,
        appeal_reason: str,
        *,
        submitted_by: UUID | str,
    ) -> dict:
        """Record one administrator-operated appeal and its durable receipt."""
        suspension_uuid, actor = UUID(str(suspension_id)), UUID(str(submitted_by))
        if not isinstance(appeal_reason, str) or not appeal_reason.strip() or len(appeal_reason) > 4000:
            raise HTTPException(status_code=422, detail="Appeal reason must contain 1 to 4000 characters.")
        audit = self._required_audit()
        async with acquire_with_tenant(self.db_pool, None) as conn:
            await audit.lock_transaction(conn)
            await lock_user_administration(conn)
            await require_current_platform_admin(conn, actor)
            event = await conn.fetchrow(
                "SELECT e.*,a.action AS audit_action,a.metadata AS audit_metadata, "
                "a.metadata->'formal_account_hold_version'='1'::jsonb AS policy_audited "
                "FROM suspension_events e LEFT JOIN audit_logs a ON a.event_id=e.audit_log_id "
                "WHERE e.suspension_id=$1 FOR UPDATE OF e", suspension_uuid,
            )
            if event is None:
                raise HTTPException(status_code=404, detail="Suspension not found.")
            if not event["is_active"]:
                raise HTTPException(status_code=409, detail="The selected formal hold is no longer active.")
            tables = {"user": "user_profiles", "tenant": "tenants", "partner": "white_label_partners"}
            table = tables.get(event["target_type"])
            if table is None:
                raise HTTPException(status_code=409, detail="Unsupported suspension target.")
            tenant_column = "id" if event["target_type"] == "tenant" else "tenant_id"
            target = await conn.fetchrow(
                f"SELECT id,{tenant_column} AS audit_tenant_id FROM {table} WHERE id=$1 FOR KEY SHARE",
                event["target_id"],
            )
            if target is None:
                raise HTTPException(status_code=409, detail="The suspension target no longer exists.")
            if event["appeal_decision"] == "pending":
                if (event["appeal_reason"] != appeal_reason or event.get("audit_action") != "appeal_submitted"
                        or _document(event.get("audit_metadata")).get("suspension_id") != str(suspension_uuid)):
                    raise HTTPException(status_code=409, detail="An existing appeal requires review; its submission cannot be replaced.")
                return {"suspension_id":suspension_uuid,"appeal_submitted":True,"status":"pending_review",
                        "audit_event_id":event["audit_log_id"],"changed":False,
                        "appeal_submitted_at":event["appeal_submitted_at"],
                        "hint_status":"not_attempted","notification_delivery_status":"unverified"}
            if event["appeal_decision"] is not None or event["appeal_submitted_at"] is not None:
                raise HTTPException(status_code=409, detail="This appeal has already been reviewed or requires reconciliation; a retry cannot reopen it.")
            submitted_at = await conn.fetchval("SELECT clock_timestamp()")
            await conn.execute(
                "UPDATE suspension_events SET appeal_submitted_at=$2,appeal_reason=$3,appeal_decision='pending' "
                "WHERE suspension_id=$1", suspension_uuid,submitted_at,appeal_reason,
            )
            metadata = {"suspension_id":str(suspension_uuid),"appeal_submitted_at":submitted_at.isoformat()}
            if _account_policy(event):
                metadata["formal_account_hold_version"] = 1
            audit_id = await audit.log_in_transaction(
                conn,event_type=AuditEvent.USER_UPDATED,action="appeal_submitted",actor_id=actor,
                actor_role="platform_admin",tenant_id=target["audit_tenant_id"],resource_type="suspension",
                resource_id=suspension_uuid,metadata=metadata,
            )
            await conn.execute("UPDATE suspension_events SET audit_log_id=$2 WHERE suspension_id=$1",
                               suspension_uuid,audit_id)

        # An optional publication is only a hint, never proof of notification.
        hint_status = "unavailable"
        if self.redis:
            try:
                await asyncio.wait_for(self.redis.publish("suspension:appeals", json.dumps({
                    "type":"appeal_submitted","suspension_id":str(suspension_uuid),
                    "audit_event_id":str(audit_id),
                })), timeout=1.0)
                hint_status = "published"
            except Exception:
                hint_status = "failed"
                logger.warning("Appeal committed; optional publication failed", extra={"suspension_id":str(suspension_uuid)})
        return {"suspension_id":suspension_uuid,"appeal_submitted":True,"status":"pending_review",
                "audit_event_id":audit_id,"changed":True,"appeal_submitted_at":submitted_at,
                "hint_status":hint_status,"notification_delivery_status":"unverified"}

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

        if decision not in {"granted", "denied"}:
            raise HTTPException(status_code=422, detail="Appeal decision must be granted or denied.")
        async with acquire_with_tenant(self.db_pool, None) as conn:
            original = await conn.fetchrow(
                "SELECT target_type,target_id FROM suspension_events WHERE suspension_id=$1", suspension_uuid,
            )
        if original is None:
            raise HTTPException(status_code=404, detail="Suspension not found")
        if original["target_type"] in {"tenant", "partner"} or decision == "denied":
            if decision == "granted":
                restore = self.restore_tenant if original["target_type"] == "tenant" else self.restore_partner
                restored = await restore(
                    original["target_id"], admin_uuid, "Appeal granted",
                    expected_suspension_id=suspension_uuid, granted_appeal_response=response,
                )
                return {"decision":decision,"response":response,"restored":True,**restored.model_dump()}
            audit = self._required_audit()
            async with acquire_with_tenant(self.db_pool, None) as conn:
                await audit.lock_transaction(conn)
                await lock_user_administration(conn)
                await require_current_platform_admin(conn, admin_uuid)
                event = await conn.fetchrow(
                    "SELECT e.*,a.action AS audit_action,a.metadata AS audit_metadata, "
                    "a.metadata->'formal_account_hold_version'='1'::jsonb AS policy_audited "
                    "FROM suspension_events e LEFT JOIN audit_logs a ON a.event_id=e.audit_log_id "
                    "WHERE e.suspension_id=$1 FOR UPDATE OF e", suspension_uuid,
                )
                if not event or not event["is_active"]:
                    raise HTTPException(status_code=409, detail="The selected formal hold is no longer active.")
                if (event["appeal_decision"] == "denied" and event["appeal_response"] == response
                        and event.get("audit_action") == "appeal_reviewed"
                        and _document(event.get("audit_metadata")).get("suspension_id") == str(suspension_uuid)):
                    return {"suspension_id":suspension_uuid,"decision":decision,"response":response,
                            "audit_event_id":event["audit_log_id"],"changed":False}
                if event["appeal_decision"] not in {None, "pending"}:
                    raise HTTPException(status_code=409, detail="This appeal has already been reviewed; its decision cannot be replaced.")
                await conn.execute(
                    "UPDATE suspension_events SET appeal_reviewed_by=$1,appeal_decision=$2,appeal_response=$3 WHERE suspension_id=$4",
                    admin_uuid,decision,response,suspension_uuid,
                )
                metadata = {"decision":decision,"suspension_id":str(suspension_uuid)}
                if _account_policy(event):
                    metadata["formal_account_hold_version"] = 1
                audit_id = await audit.log_in_transaction(
                    conn,event_type=AuditEvent.USER_UPDATED,action="appeal_reviewed",
                    actor_id=admin_uuid,actor_role="platform_admin",resource_type="suspension",
                    resource_id=suspension_uuid,metadata=metadata,
                )
                await conn.execute("UPDATE suspension_events SET audit_log_id=$2 WHERE suspension_id=$1",
                                   suspension_uuid,audit_id)
            return {"suspension_id":suspension_uuid,"decision":decision,"response":response,
                    "audit_event_id":audit_id,"changed":True}

        if decision == "granted":
            async with acquire_with_tenant(self.db_pool, None) as conn:
                original = await conn.fetchrow(
                    "SELECT target_type,target_id FROM suspension_events WHERE suspension_id=$1",
                    suspension_uuid,
                )
            if original and original["target_type"] == TargetType.USER.value:
                restored = await self.restore_user(
                    original["target_id"], reviewed_by, "Appeal granted",
                    expected_suspension_id=suspension_uuid, granted_appeal_response=response,
                )
                return {"suspension_id": suspension_uuid, "decision": decision,
                        "response": response, "restored": True,
                        "audit_event_id": restored.audit_event_id,
                        "propagated_to": restored.propagated_to,
                        "propagation_failed": restored.propagation_failed,
                        "propagation_pending": restored.propagation_pending}

        raise HTTPException(status_code=422, detail="Unsupported suspension target.")

    async def _propagate_to_services(self, suspension_id, target_type, target_id, action):
        """Legacy internal port: pending enforcement is not a delivery receipt.

        The historical queue has no installed schema or consumer. Current user
        credentials are revoked transactionally; existing socket owners recheck
        authority. Do not invent PSTN ownership for an individual staff user.
        """
        pending = "assistant_session_check" if target_type == TargetType.USER else "telephony_policy_check"
        return {"success": [], "failed": [], "pending": [pending]}

    async def get_propagation_status(self, suspension_id):
        return await project_enforcement(self.db_pool,suspension_id)

    async def process_auto_restores(
        self, *, batch_limit: int = 50, time_budget_seconds: float = 15.0,
        item_timeout_seconds: float = 3.0, read_timeout_seconds: float = 3.0,
    ) -> list[dict]:
        """One fair, bounded expiry pass; exact-event transactions decide effects.

        A lost response or timeout cannot prove rollback. Such attempts stay
        unknown; a later pass reads durable active events instead of replaying
        a saved mutation. The cursor is local scheduling state, not authority.
        """
        if self.db_pool is None:
            return []
        limit = max(1, min(200, int(batch_limit)))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.001, min(60.0, time_budget_seconds))
        cursor = getattr(self, "_auto_restore_cursor", None)
        query = """
                SELECT suspension_id, target_type, target_id
                FROM suspension_events
                WHERE is_active = TRUE
                AND suspended_until IS NOT NULL
                AND suspended_until <= clock_timestamp()
                AND suspension_type IN ('TEMPORARY', 'BILLING')
                AND ($1::uuid IS NULL OR suspension_id > $1::uuid)
                ORDER BY suspension_id LIMIT $2
                """
        read_budget = max(0.001, min(read_timeout_seconds, deadline - loop.time()))
        async with asyncio.timeout(read_budget):
            async with acquire_with_tenant(self.db_pool, None, timeout=read_budget) as conn:
                rows = await conn.fetch(query, cursor, limit)
                if not rows and cursor is not None:
                    self._auto_restore_cursor = None
                    rows = await conn.fetch(query, None, limit)

        restored = []
        for row in rows:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            self._auto_restore_cursor = row["suspension_id"]
            receipt = {
                "suspension_id": str(row["suspension_id"]),
                "target_type": row["target_type"], "target_id": str(row["target_id"]),
            }
            try:
                restore = {
                    TargetType.USER.value: self.restore_user,
                    TargetType.TENANT.value: self.restore_tenant,
                    TargetType.PARTNER.value: self.restore_partner,
                }.get(row["target_type"])
                if restore is None:
                    raise HTTPException(409, detail="Unsupported suspension target requires operator review.")
                async with asyncio.timeout(max(0.001, min(item_timeout_seconds, remaining))):
                    result = await restore(
                        row["target_id"], None, "Auto-restored after temporary suspension period",
                        expected_suspension_id=row["suspension_id"],
                    )
                receipt.update(restored=True, outcome="restored",
                               audit_event_id=str(result.audit_event_id) if result.audit_event_id else None)
            except HTTPException as exc:
                # Locked eligibility/ownership rejection proves this attempt
                # did not restore the hold; it is not success or a retry order.
                receipt.update(restored=False, outcome="not_restored", status_code=exc.status_code)
            except Exception as exc:
                receipt.update(restored=None, outcome="unknown", error_type=type(exc).__name__)
            restored.append(receipt)
        if len(restored) == len(rows) and len(rows) < limit:
            self._auto_restore_cursor = None
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
