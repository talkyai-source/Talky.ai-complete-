"""
Audit Logger Service - Comprehensive audit logging with tamper-evident properties
"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel


class AuditEvent(str, Enum):
    """Audit event types"""
    # Authentication
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    SESSION_CREATED = "session_created"
    SESSION_TERMINATED = "session_terminated"
    PASSWORD_CHANGED = "password_changed"
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"
    PASSKEY_REGISTERED = "passkey_registered"
    PASSKEY_REMOVED = "passkey_removed"

    # Authorization
    PERMISSION_DENIED = "permission_denied"
    ROLE_ASSIGNED = "role_assigned"
    ROLE_REMOVED = "role_removed"
    PRIVILEGE_ESCALATION = "privilege_escalation"

    # User management
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_SUSPENDED = "user_suspended"
    USER_RESTORED = "user_restored"
    USER_DELETED = "user_deleted"

    # Tenant admin
    TENANT_CREATED = "tenant_created"
    TENANT_UPDATED = "tenant_updated"
    TENANT_SUSPENDED = "tenant_suspended"
    TENANT_RESTORED = "tenant_restored"
    BILLING_UPDATED = "billing_updated"
    LIMITS_CHANGED = "limits_changed"

    # Security
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    SESSION_HIJACKING_DETECTED = "session_hijacking_detected"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"

    # Data access
    RECORD_VIEWED = "record_viewed"
    RECORD_EXPORTED = "record_exported"
    BULK_DOWNLOAD = "bulk_download"
    CROSS_TENANT_ACCESS = "cross_tenant_access"

    # System
    CONFIG_CHANGED = "config_changed"
    SECRET_ROTATED = "secret_rotated"
    SECRET_REVOKED = "secret_revoked"
    KEY_REVOKED = "key_revoked"
    EMERGENCY_ACCESS_REQUESTED = "emergency_access_requested"
    EMERGENCY_ACCESS_APPROVED = "emergency_access_approved"
    EMERGENCY_ACCESS_USED = "emergency_access_used"


class EventCategory(str, Enum):
    """Event categories for retention and routing"""
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    USER_MANAGEMENT = "USER_MANAGEMENT"
    TENANT_ADMIN = "TENANT_ADMIN"
    SECURITY = "SECURITY"
    DATA_ACCESS = "DATA_ACCESS"
    SYSTEM = "SYSTEM"


class Severity(str, Enum):
    """Event severity levels"""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


# Retention periods by category (days)
RETENTION_PERIODS = {
    EventCategory.AUTHENTICATION: 365,
    EventCategory.AUTHORIZATION: 365 * 3,
    EventCategory.USER_MANAGEMENT: 365 * 3,
    EventCategory.TENANT_ADMIN: 365 * 7,
    EventCategory.SECURITY: 365 * 3,
    EventCategory.DATA_ACCESS: 365,
    EventCategory.SYSTEM: 365 * 7,
}

# Event to category mapping
EVENT_CATEGORIES = {
    AuditEvent.LOGIN_SUCCESS: EventCategory.AUTHENTICATION,
    AuditEvent.LOGIN_FAILURE: EventCategory.AUTHENTICATION,
    AuditEvent.LOGOUT: EventCategory.AUTHENTICATION,
    AuditEvent.SESSION_CREATED: EventCategory.AUTHENTICATION,
    AuditEvent.SESSION_TERMINATED: EventCategory.AUTHENTICATION,
    AuditEvent.PASSWORD_CHANGED: EventCategory.AUTHENTICATION,
    AuditEvent.MFA_ENABLED: EventCategory.AUTHENTICATION,
    AuditEvent.MFA_DISABLED: EventCategory.AUTHENTICATION,
    AuditEvent.PASSKEY_REGISTERED: EventCategory.AUTHENTICATION,
    AuditEvent.PASSKEY_REMOVED: EventCategory.AUTHENTICATION,
    AuditEvent.PERMISSION_DENIED: EventCategory.AUTHORIZATION,
    AuditEvent.ROLE_ASSIGNED: EventCategory.AUTHORIZATION,
    AuditEvent.ROLE_REMOVED: EventCategory.AUTHORIZATION,
    AuditEvent.PRIVILEGE_ESCALATION: EventCategory.AUTHORIZATION,
    AuditEvent.USER_CREATED: EventCategory.USER_MANAGEMENT,
    AuditEvent.USER_UPDATED: EventCategory.USER_MANAGEMENT,
    AuditEvent.USER_SUSPENDED: EventCategory.USER_MANAGEMENT,
    AuditEvent.USER_RESTORED: EventCategory.USER_MANAGEMENT,
    AuditEvent.USER_DELETED: EventCategory.USER_MANAGEMENT,
    AuditEvent.TENANT_CREATED: EventCategory.TENANT_ADMIN,
    AuditEvent.TENANT_UPDATED: EventCategory.TENANT_ADMIN,
    AuditEvent.TENANT_SUSPENDED: EventCategory.TENANT_ADMIN,
    AuditEvent.TENANT_RESTORED: EventCategory.TENANT_ADMIN,
    AuditEvent.BILLING_UPDATED: EventCategory.TENANT_ADMIN,
    AuditEvent.LIMITS_CHANGED: EventCategory.TENANT_ADMIN,
    AuditEvent.SUSPICIOUS_ACTIVITY: EventCategory.SECURITY,
    AuditEvent.SESSION_HIJACKING_DETECTED: EventCategory.SECURITY,
    AuditEvent.RATE_LIMIT_EXCEEDED: EventCategory.SECURITY,
    AuditEvent.API_KEY_CREATED: EventCategory.SECURITY,
    AuditEvent.API_KEY_REVOKED: EventCategory.SECURITY,
    AuditEvent.RECORD_VIEWED: EventCategory.DATA_ACCESS,
    AuditEvent.RECORD_EXPORTED: EventCategory.DATA_ACCESS,
    AuditEvent.BULK_DOWNLOAD: EventCategory.DATA_ACCESS,
    AuditEvent.CROSS_TENANT_ACCESS: EventCategory.DATA_ACCESS,
    AuditEvent.CONFIG_CHANGED: EventCategory.SYSTEM,
    AuditEvent.SECRET_ROTATED: EventCategory.SYSTEM,
    AuditEvent.SECRET_REVOKED: EventCategory.SYSTEM,
    AuditEvent.KEY_REVOKED: EventCategory.SYSTEM,
    AuditEvent.EMERGENCY_ACCESS_REQUESTED: EventCategory.SYSTEM,
    AuditEvent.EMERGENCY_ACCESS_APPROVED: EventCategory.SYSTEM,
    AuditEvent.EMERGENCY_ACCESS_USED: EventCategory.SYSTEM,
}

# Event severity mapping
EVENT_SEVERITY = {
    AuditEvent.PRIVILEGE_ESCALATION: Severity.CRITICAL,
    AuditEvent.CROSS_TENANT_ACCESS: Severity.CRITICAL,
    AuditEvent.TENANT_SUSPENDED: Severity.CRITICAL,
    AuditEvent.SESSION_HIJACKING_DETECTED: Severity.CRITICAL,
    AuditEvent.EMERGENCY_ACCESS_REQUESTED: Severity.CRITICAL,
    AuditEvent.EMERGENCY_ACCESS_APPROVED: Severity.CRITICAL,
    AuditEvent.EMERGENCY_ACCESS_USED: Severity.CRITICAL,
    AuditEvent.SUSPICIOUS_ACTIVITY: Severity.HIGH,
    AuditEvent.PERMISSION_DENIED: Severity.WARNING,
    AuditEvent.MFA_DISABLED: Severity.WARNING,
    AuditEvent.USER_SUSPENDED: Severity.WARNING,
    AuditEvent.USER_DELETED: Severity.WARNING,
    AuditEvent.API_KEY_REVOKED: Severity.WARNING,
    AuditEvent.BULK_DOWNLOAD: Severity.WARNING,
    AuditEvent.SECRET_REVOKED: Severity.WARNING,
    AuditEvent.RATE_LIMIT_EXCEEDED: Severity.WARNING,
}


class AuditLogEntry(BaseModel):
    """Audit log entry model"""
    event_id: UUID
    event_time: datetime
    event_type: AuditEvent
    event_category: EventCategory
    severity: Severity
    actor_id: Optional[UUID]
    actor_type: str = "user"
    actor_role: Optional[str]
    tenant_id: Optional[UUID]
    resource_type: Optional[str]
    resource_id: Optional[UUID]
    ip_address: Optional[str]
    user_agent: Optional[str]
    session_id: Optional[UUID]
    device_fingerprint: Optional[str]
    country_code: Optional[str]
    action: str
    description: Optional[str]
    before_state: Optional[dict]
    after_state: Optional[dict]
    metadata: Optional[dict]
    previous_hash: Optional[str]
    entry_hash: str
    signature: Optional[str]
    compliance_tags: list[str] = []
    retention_until: datetime
    created_at: datetime


class AuditLogger:
    """
    Comprehensive audit logging service with tamper-evident properties.

    Features:
    - Immutable audit trail with chain hashing
    - HMAC signatures for verification
    - Configurable retention by category
    - Batch processing for performance
    - Compliance tagging (SOC2, GDPR, etc.)
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        signing_key: Optional[str] = None,
        batch_size: int = 100,
        flush_interval_seconds: int = 5
    ):
        self.db_pool = db_pool
        self.signing_key = signing_key or secrets.token_hex(32)
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self._batch: list[dict] = []
        self._last_flush = datetime.utcnow()

    def _get_category(self, event_type: AuditEvent) -> EventCategory:
        """Get category for event type"""
        return EVENT_CATEGORIES.get(event_type, EventCategory.SYSTEM)

    def _get_severity(self, event_type: AuditEvent) -> Severity:
        """Get severity for event type"""
        return EVENT_SEVERITY.get(event_type, Severity.INFO)

    def _calculate_retention(self, category: EventCategory) -> datetime:
        """Calculate retention date based on category"""
        days = RETENTION_PERIODS.get(category, 365)
        return datetime.utcnow() + timedelta(days=days)

    def _compute_hash(self, entry: dict) -> str:
        """Compute SHA-256 hash of entry content"""
        # Create canonical representation
        content = {
            "event_time": entry["event_time"].isoformat() if isinstance(entry["event_time"], datetime) else entry["event_time"],
            "event_type": entry["event_type"],
            "actor_id": str(entry["actor_id"]) if entry.get("actor_id") else None,
            "tenant_id": str(entry["tenant_id"]) if entry.get("tenant_id") else None,
            "action": entry["action"],
            "before_state": entry.get("before_state"),
            "after_state": entry.get("after_state"),
            "metadata": entry.get("metadata"),
        }
        canonical = json.dumps(content, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _sign_entry(self, entry: dict) -> str:
        """Create HMAC-SHA256 signature for entry"""
        content = f"{entry['event_id']}:{entry['entry_hash']}:{entry['previous_hash'] or ''}"
        return hmac.new(
            self.signing_key.encode(),
            content.encode(),
            hashlib.sha256
        ).hexdigest()

    async def _get_previous_hash(self) -> str:
        """Get hash of most recent audit log entry"""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT entry_hash FROM audit_logs ORDER BY event_time DESC LIMIT 1"
            )
            return row["entry_hash"] if row else "0" * 64

    async def log(
        self,
        event_type: AuditEvent,
        action: str,
        actor_id: Optional[UUID | str] = None,
        tenant_id: Optional[UUID | str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[UUID | str] = None,
        description: Optional[str] = None,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
        metadata: Optional[dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[UUID | str] = None,
        device_fingerprint: Optional[str] = None,
        country_code: Optional[str] = None,
        actor_type: str = "user",
        actor_role: Optional[str] = None,
        compliance_tags: Optional[list[str]] = None,
    ) -> UUID:
        """
        Log a security audit event.

        Args:
            event_type: Type of audit event
            action: Human-readable action description
            actor_id: UUID of the actor (user/system)
            tenant_id: Associated tenant
            resource_type: Type of resource affected
            resource_id: UUID of resource affected
            description: Detailed description
            before_state: State before action (for changes)
            after_state: State after action (for changes)
            metadata: Additional structured data
            ip_address: Client IP address
            user_agent: Client user agent
            session_id: Session UUID
            device_fingerprint: Device fingerprint
            country_code: ISO country code
            actor_type: Type of actor (user, system, api_key)
            actor_role: Role of actor
            compliance_tags: Compliance framework tags (soc2, gdpr, hipaa)

        Returns:
            event_id: UUID of created audit log entry
        """
        event_id = uuid4()
        category = self._get_category(event_type)
        severity = self._get_severity(event_type)
        retention_until = self._calculate_retention(category)

        # Convert string UUIDs to UUID objects
        actor_uuid = UUID(actor_id) if isinstance(actor_id, str) else actor_id
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        resource_uuid = UUID(resource_id) if isinstance(resource_id, str) else resource_id
        session_uuid = UUID(session_id) if isinstance(session_id, str) else session_id

        entry = {
            "event_id": event_id,
            "event_time": datetime.utcnow(),
            "event_type": event_type.value,
            "event_category": category.value,
            "severity": severity.value,
            "actor_id": actor_uuid,
            "actor_type": actor_type,
            "actor_role": actor_role,
            "tenant_id": tenant_uuid,
            "resource_type": resource_type,
            "resource_id": resource_uuid,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "session_id": session_uuid,
            "device_fingerprint": device_fingerprint,
            "country_code": country_code,
            "action": action,
            "description": description,
            "before_state": before_state,
            "after_state": after_state,
            "metadata": metadata,
            "compliance_tags": compliance_tags or [],
            "retention_until": retention_until,
        }

        # Compute chain hash
        entry["previous_hash"] = await self._get_previous_hash()
        entry["entry_hash"] = self._compute_hash(entry)
        entry["signature"] = self._sign_entry({**entry, "event_id": event_id})

        # Add to batch
        self._batch.append(entry)

        # Flush if needed
        if len(self._batch) >= self.batch_size:
            await self.flush()
        elif (datetime.utcnow() - self._last_flush).total_seconds() >= self.flush_interval_seconds:
            await self.flush()

        return event_id

    async def log_admin_action(
        self,
        action: str,
        actor_id: UUID | str,
        target_user_id: Optional[UUID | str] = None,
        tenant_id: Optional[UUID | str] = None,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
        reason: Optional[str] = None,
        event_type: AuditEvent = AuditEvent.USER_UPDATED,
    ) -> UUID:
        """
        Convenience method for logging admin actions with before/after state.

        Args:
            action: Action description (e.g., "role_assigned")
            actor_id: Admin performing the action
            target_user_id: User being modified
            tenant_id: Tenant scope
            before_state: State before change
            after_state: State after change
            reason: Business reason for change
            event_type: Audit event type

        Returns:
            event_id: UUID of created audit log entry
        """
        metadata = {"reason": reason} if reason else {}

        actor_uuid = UUID(actor_id) if isinstance(actor_id, str) else actor_id
        target_uuid = UUID(target_user_id) if isinstance(target_user_id, str) else target_user_id
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

        return await self.log(
            event_type=event_type,
            action=action,
            actor_id=actor_uuid,
            tenant_id=tenant_uuid,
            resource_type="user",
            resource_id=target_uuid,
            description=f"Admin action: {action} performed by {actor_id}",
            before_state=before_state,
            after_state=after_state,
            metadata=metadata,
            compliance_tags=["soc2", "iso27001"],
        )

    async def log_auth_event(
        self,
        event_type: AuditEvent,
        actor_id: Optional[UUID | str] = None,
        tenant_id: Optional[UUID | str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[UUID | str] = None,
        success: bool = True,
        metadata: Optional[dict] = None,
    ) -> UUID:
        """
        Convenience method for logging authentication events.

        Args:
            event_type: Type of auth event
            actor_id: User attempting authentication
            tenant_id: Tenant scope
            ip_address: Client IP
            user_agent: Client user agent
            session_id: Session ID
            success: Whether auth succeeded
            metadata: Additional data (mfa_used, auth_method, etc.)

        Returns:
            event_id: UUID of created audit log entry
        """
        action = "success" if success else "failure"

        actor_uuid = UUID(actor_id) if isinstance(actor_id, str) else actor_id
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        session_uuid = UUID(session_id) if isinstance(session_id, str) else session_id

        return await self.log(
            event_type=event_type,
            action=action,
            actor_id=actor_uuid,
            tenant_id=tenant_uuid,
            resource_type="session",
            resource_id=session_uuid,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_uuid,
            metadata=metadata or {},
            compliance_tags=["soc2"],
        )

    async def flush(self) -> None:
        """Flush batched entries to database"""
        if not self._batch:
            return

        async with self.db_pool.acquire() as conn:
            # Use executemany for efficient batch insert
            await conn.executemany(
                """
                INSERT INTO audit_logs (
                    event_id, event_time, event_type, event_category, severity,
                    actor_id, actor_type, actor_role, tenant_id, resource_type, resource_id,
                    ip_address, user_agent, session_id, device_fingerprint, country_code,
                    action, description, before_state, after_state, metadata,
                    previous_hash, entry_hash, signature, compliance_tags, retention_until
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                    $17, $18, $19, $20, $21, $22, $23, $24, $25, $26
                )
                """,
                [
                    (
                        e["event_id"], e["event_time"], e["event_type"], e["event_category"],
                        e["severity"], e["actor_id"], e["actor_type"], e["actor_role"],
                        e["tenant_id"], e["resource_type"], e["resource_id"], e["ip_address"],
                        e["user_agent"], e["session_id"], e["device_fingerprint"],
                        e["country_code"], e["action"], e["description"], json.dumps(e["before_state"]) if e["before_state"] else None,
                        json.dumps(e["after_state"]) if e["after_state"] else None,
                        json.dumps(e["metadata"]) if e["metadata"] else None,
                        e["previous_hash"], e["entry_hash"], e["signature"],
                        e["compliance_tags"], e["retention_until"]
                    )
                    for e in self._batch
                ]
            )

        self._batch = []
        self._last_flush = datetime.utcnow()

    async def query(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        event_type: Optional[AuditEvent] = None,
        actor_id: Optional[UUID | str] = None,
        tenant_id: Optional[UUID | str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[UUID | str] = None,
        severity: Optional[Severity] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """
        Query audit logs with filters.

        Returns:
            List of matching audit log entries
        """
        conditions = []
        params = []
        param_idx = 1

        if start_date:
            conditions.append(f"event_time >= ${param_idx}")
            params.append(start_date)
            param_idx += 1

        if end_date:
            conditions.append(f"event_time <= ${param_idx}")
            params.append(end_date)
            param_idx += 1

        if event_type:
            conditions.append(f"event_type = ${param_idx}")
            params.append(event_type.value)
            param_idx += 1

        if actor_id:
            actor_uuid = UUID(actor_id) if isinstance(actor_id, str) else actor_id
            conditions.append(f"actor_id = ${param_idx}")
            params.append(actor_uuid)
            param_idx += 1

        if tenant_id:
            tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
            conditions.append(f"tenant_id = ${param_idx}")
            params.append(tenant_uuid)
            param_idx += 1

        if resource_type:
            conditions.append(f"resource_type = ${param_idx}")
            params.append(resource_type)
            param_idx += 1

        if resource_id:
            resource_uuid = UUID(resource_id) if isinstance(resource_id, str) else resource_id
            conditions.append(f"resource_id = ${param_idx}")
            params.append(resource_uuid)
            param_idx += 1

        if severity:
            conditions.append(f"severity = ${param_idx}")
            params.append(severity.value)
            param_idx += 1

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT * FROM audit_logs
            {where_clause}
            ORDER BY event_time DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """
        params.extend([limit, offset])

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [AuditLogEntry(**dict(row)) for row in rows]

    async def verify_chain_integrity(self) -> dict:
        """
        Verify the integrity of the audit log chain.

        Returns:
            dict with verification results
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT event_id, event_time, event_type, actor_id, action, previous_hash, entry_hash FROM audit_logs ORDER BY event_time"
            )

        if not rows:
            return {"valid": True, "entries_checked": 0, "errors": []}

        errors = []
        previous_hash = "0" * 64

        for i, row in enumerate(rows):
            # Check chain continuity
            if row["previous_hash"] != previous_hash:
                errors.append({
                    "index": i,
                    "event_id": str(row["event_id"]),
                    "error": "chain_break",
                    "expected_previous": previous_hash,
                    "actual_previous": row["previous_hash"]
                })

            # Recompute hash to verify content
            content = {
                "event_time": row["event_time"].isoformat(),
                "event_type": row["event_type"],
                "actor_id": str(row["actor_id"]) if row["actor_id"] else None,
                "action": row["action"],
            }
            canonical = json.dumps(content, sort_keys=True, default=str)
            computed_hash = hashlib.sha256(canonical.encode()).hexdigest()

            if computed_hash != row["entry_hash"]:
                errors.append({
                    "index": i,
                    "event_id": str(row["event_id"]),
                    "error": "hash_mismatch",
                    "expected_hash": computed_hash,
                    "actual_hash": row["entry_hash"]
                })

            previous_hash = row["entry_hash"]

        return {
            "valid": len(errors) == 0,
            "entries_checked": len(rows),
            "errors": errors
        }


# Convenience function for dependency injection
async def get_audit_logger(db_pool: asyncpg.Pool) -> AuditLogger:
    """Factory function for creating audit logger"""
    return AuditLogger(db_pool)
