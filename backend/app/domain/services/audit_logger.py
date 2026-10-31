"""
Audit Logger Service - Comprehensive audit logging with tamper-evident properties
"""
import asyncio
import itertools
import logging
import os
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel, field_validator

from app.core.db_utils import acquire_with_tenant
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# The tamper-evident chain has one writer at a time across *every* process:
# talky-api, talky-api-web, the dialer and the workers each run their own
# AuditLogger. A flush holds this transaction-scoped advisory lock while it
# reads the chain tail and inserts, so two flushes can never chain onto the
# same tail. 0x61756469745F6368 is "audit_ch" in ASCII.
_CHAIN_LOCK_KEY = 0x61756469745F6368
# Longest a flush waits for that lock. log() flushes inline on the request
# path, so one stalled writer must not hang every process's audit logging: the
# flush fails, keeps its entries buffered and the next flush retries.
_CHAIN_LOCK_TIMEOUT_MS = 5000


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

    # Support
    SUPPORT_TICKET_CREATED = "support_ticket_created"
    SUPPORT_TICKET_CHANGED = "support_ticket_changed"
    SUPPORT_ARTICLE_CHANGED = "support_article_changed"


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
    AuditEvent.PERMISSION_DENIED: Severity.MEDIUM,
    AuditEvent.MFA_DISABLED: Severity.MEDIUM,
    AuditEvent.USER_SUSPENDED: Severity.MEDIUM,
    AuditEvent.USER_DELETED: Severity.MEDIUM,
    AuditEvent.API_KEY_REVOKED: Severity.MEDIUM,
    AuditEvent.BULK_DOWNLOAD: Severity.MEDIUM,
    AuditEvent.SECRET_REVOKED: Severity.MEDIUM,
    AuditEvent.RATE_LIMIT_EXCEEDED: Severity.MEDIUM,
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

    @field_validator("ip_address", mode="before")
    @classmethod
    def _stringify_inet(cls, value):
        # The ip_address column is Postgres `inet`, which asyncpg returns as an
        # IPv4Address/IPv6Address object. Pydantic v2 won't coerce that into the
        # declared `str`, so audit-log queries 500'd. Stringify it here.
        if value is None or isinstance(value, str):
            return value
        return str(value)

    @field_validator("metadata", "before_state", "after_state", mode="before")
    @classmethod
    def _parse_json_obj(cls, value):
        # These JSON columns come back from asyncpg as raw JSON strings (not
        # decoded dicts), which the `Optional[dict]` fields reject. Parse them.
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return None
        return value


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
        settings = get_settings()
        resolved_signing_key = (
            signing_key
            or os.getenv("AUDIT_LOG_SIGNING_KEY")
            or os.getenv("APP_AUDIT_LOG_SIGNING_KEY")
            or os.getenv("JWT_SECRET")
            or os.getenv("SECRET_KEY")
            or settings.effective_jwt_secret
        )
        environment = (os.getenv("ENVIRONMENT") or settings.environment).strip().lower()
        if not resolved_signing_key:
            if environment == "production":
                raise RuntimeError("AUDIT_LOG_SIGNING_KEY must be configured in production")
            resolved_signing_key = "development-audit-log-signing-key"
        self.signing_key = resolved_signing_key
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self._batch: list[dict] = []
        self._last_flush = datetime.utcnow()
        # Serialises "read the chain tail, append to the batch, flush": without
        # it two concurrent log() calls (or a log() racing a flush) read the
        # same previous_hash and fork the chain.
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._closed = False
        self._last_event_time: Optional[datetime] = None

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

    @staticmethod
    def _canonical_time(value):
        """Naive-UTC ISO text: the form the hash has always been computed over.

        Entries are hashed before the database sees them, but read back as
        timezone-aware datetimes; both must reduce to the same text.
        """
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            return value.isoformat()
        return value

    def _hash_content(self, entry: dict) -> dict:
        """Canonical content an entry hash is computed over."""
        return {
            "event_time": self._canonical_time(entry["event_time"]),
            "event_type": entry["event_type"],
            "actor_id": str(entry["actor_id"]) if entry.get("actor_id") else None,
            "tenant_id": str(entry["tenant_id"]) if entry.get("tenant_id") else None,
            "action": entry["action"],
            "before_state": entry.get("before_state"),
            "after_state": entry.get("after_state"),
            "metadata": entry.get("metadata"),
        }

    def _compute_hash(self, entry: dict) -> str:
        """Compute SHA-256 hash of entry content"""
        canonical = json.dumps(self._hash_content(entry), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _sign_entry(self, entry: dict) -> str:
        """Create HMAC-SHA256 signature for entry"""
        content = f"{entry['event_id']}:{entry['entry_hash']}:{entry['previous_hash'] or ''}"
        return hmac.new(
            self.signing_key.encode(),
            content.encode(),
            hashlib.sha256
        ).hexdigest()

    def _chain(
        self,
        entries: list[dict],
        previous_hash: str,
        previous_time: Optional[datetime],
    ) -> list[dict]:
        """Link ``entries`` onto the chain tail; returns new dicts, inputs untouched.

        Only meaningful under the database chain lock, where (previous_hash,
        previous_time) is the real tail. verify_chain_integrity walks rows in
        event_time order, so event_time must increase along the chain whichever
        process stamped the entry: an entry that is not already later than the
        tail is moved to 1 microsecond after it. The hash text is unchanged, so
        rows stored earlier keep verifying.
        """
        linked = []
        for entry in entries:
            event_time = entry["event_time"]
            if previous_time is not None and event_time <= previous_time:
                event_time = previous_time + timedelta(microseconds=1)
            link = {**entry, "event_time": event_time, "previous_hash": previous_hash}
            link["entry_hash"] = self._compute_hash(link)
            link["signature"] = self._sign_entry(link)
            linked.append(link)
            previous_hash, previous_time = link["entry_hash"], event_time
        return linked

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
            "event_time": None,  # stamped under the lock, see below
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
            # Empty states are stored as NULL, so they must be hashed as None
            # or a stored row can never reproduce its own hash.
            "before_state": before_state or None,
            "after_state": after_state or None,
            "metadata": metadata or None,
            "compliance_tags": compliance_tags or [],
            "retention_until": retention_until,
        }

        # Hashing happens at flush time now. Content that cannot be hashed
        # must be refused here, to the caller: once buffered it would make
        # every later flush raise and no audit row would be written again.
        self._compute_hash(entry)

        async with self._lock:
            # Timezone-aware (a naive datetime would be stored as host-local
            # time) and increasing within this process. This is when the event
            # happened; _flush_locked only moves it later if another process
            # has already chained a later entry.
            now = datetime.now(timezone.utc)
            if self._last_event_time is not None and now <= self._last_event_time:
                now = self._last_event_time + timedelta(microseconds=1)
            self._last_event_time = now
            entry["event_time"] = now

            # No previous_hash / entry_hash / signature yet. The chain tail is
            # only known inside the database chain lock (other processes
            # append to it too), so _flush_locked links the entry.
            self._batch.append(entry)

            # Flush if needed. A failed flush keeps the entries buffered for
            # the timer to retry; it must not fail the caller's request.
            if (
                len(self._batch) >= self.batch_size
                or (datetime.utcnow() - self._last_flush).total_seconds()
                >= self.flush_interval_seconds
            ):
                try:
                    await self._flush_locked()
                except Exception as exc:
                    logger.warning(
                        "audit_logger flush failed, %s entries stay buffered: %s",
                        len(self._batch), exc,
                    )

            self._ensure_flush_task()

        return event_id

    @staticmethod
    async def lock_transaction(conn) -> None:
        """Reserve the existing chain before authority or business row locks.

        Administrative writes that require a durable receipt must acquire this
        first, then their authority lock, then target rows. The background
        flusher also takes the chain lock before audit foreign-key row locks.
        The caller must keep the transaction open through the business write
        and ``log_in_transaction``; commit publishes both or neither.
        """
        if not conn.is_in_transaction():
            raise RuntimeError("Transactional audit requires an open transaction")
        await conn.execute(
            f"SET LOCAL lock_timeout = {_CHAIN_LOCK_TIMEOUT_MS}; "
            f"SELECT pg_advisory_xact_lock({_CHAIN_LOCK_KEY}); "
            "SET LOCAL lock_timeout TO DEFAULT"
        )

    async def log_in_transaction(
        self, conn, *, event_type: AuditEvent, action: str,
        actor_id=None, actor_role=None, tenant_id=None,
        resource_type=None, resource_id=None, description=None,
        before_state=None, after_state=None, metadata=None,
        actor_type="user", compliance_tags=None,
    ) -> UUID:
        """Append an existing-format audit row in the caller's transaction.

        Unlike ``log``, this never buffers or suppresses failures. Its ID is
        durable only after the surrounding transaction commits. Call
        ``lock_transaction`` *before* acquiring authority/target row locks;
        this check also prevents a caller from accidentally forking the chain.
        """
        if not conn.is_in_transaction():
            raise RuntimeError("Transactional audit requires an open transaction")
        owns_lock = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND pid=pg_backend_pid() AND granted AND classid=$1::oid "
            "AND objid=$2::oid AND objsubid=1)",
            _CHAIN_LOCK_KEY >> 32, _CHAIN_LOCK_KEY & 0xFFFFFFFF,
        )
        if not owns_lock:
            raise RuntimeError("Acquire the audit chain lock before business row locks")

        def identifier(value):
            return UUID(str(value)) if value is not None else None

        def document(value):
            # Hash precisely what is stored, including UUID/datetime metadata.
            return json.loads(json.dumps(value, default=str)) if value else None

        category = self._get_category(event_type)
        now = datetime.now(timezone.utc)
        entry = {
            "event_id": uuid4(), "event_time": now,
            "event_type": event_type.value, "event_category": category.value,
            "severity": self._get_severity(event_type).value,
            "actor_id": identifier(actor_id), "actor_type": actor_type,
            "actor_role": actor_role, "tenant_id": identifier(tenant_id),
            "resource_type": resource_type, "resource_id": identifier(resource_id),
            "action": action, "description": description,
            "before_state": document(before_state), "after_state": document(after_state),
            "metadata": document(metadata), "compliance_tags": compliance_tags or [],
            "retention_until": now + timedelta(days=RETENTION_PERIODS.get(category, 365)),
        }
        # This chain is global even when the business transaction is tenant
        # scoped. Read only its tail through the existing service context;
        # restore that context before the tenant-bound INSERT below. A savepoint
        # restores the GUC on SQL errors too, without committing any caller work.
        async with conn.transaction():
            previous_bypass = await conn.fetchval(
                "SELECT current_setting('app.bypass_rls', true)"
            )
            await conn.execute("SELECT set_config('app.bypass_rls', 'on', true)")
            tail = await conn.fetchrow(
                "SELECT entry_hash,event_time FROM audit_logs "
                "ORDER BY event_time DESC,created_at DESC LIMIT 1"
            )
            await conn.execute(
                "SELECT set_config('app.bypass_rls', $1, true)", previous_bypass or ""
            )
        (entry,) = self._chain(
            [entry], tail["entry_hash"] if tail else "0" * 64,
            tail["event_time"] if tail else None,
        )
        columns = (
            "event_id", "event_time", "event_type", "event_category", "severity",
            "actor_id", "actor_type", "actor_role", "tenant_id", "resource_type",
            "resource_id", "action", "description", "before_state", "after_state",
            "metadata", "previous_hash", "entry_hash", "signature", "compliance_tags",
            "retention_until",
        )
        values = [entry[key] for key in columns]
        for index in (13, 14, 15):
            values[index] = json.dumps(values[index]) if values[index] is not None else None
        saved = await conn.fetchval(
            "INSERT INTO audit_logs (" + ",".join(columns) + ") VALUES (" +
            ",".join(f"${i + 1}::text::jsonb" if i in (13, 14, 15) else f"${i + 1}"
                     for i in range(len(columns))) + ") RETURNING event_id",
            *values,
        )
        if saved != entry["event_id"]:
            raise RuntimeError("Audit receipt was not persisted")
        return saved

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

    async def log_security_event(
        self,
        event_type: str,
        severity: str,
        description: str,
        user_id: Optional[UUID | str] = None,
        tenant_id: Optional[UUID | str] = None,
        session_id: Optional[UUID | str] = None,
        detection_source: str = "manual",
        metadata: Optional[dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        evidence: Optional[dict] = None,
        title: Optional[str] = None,
        alert_type: Optional[str] = None,
    ) -> UUID:
        """
        Log a high-priority security event.

        Args:
            event_type: Type of security event (e.g., "failed_login")
            severity: Severity level (CRITICAL, HIGH, MEDIUM, LOW, INFO)
            description: Detailed description
            user_id: Associated user
            tenant_id: Associated tenant
            session_id: Associated session
            detection_source: Source of detection
            metadata: Additional structured data
            ip_address: Client IP address
            user_agent: Client user agent
            evidence: Evidence of the security event

        Returns:
            event_id: UUID of created security event entry
        """
        event_id = uuid4()
        user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        session_uuid = UUID(session_id) if isinstance(session_id, str) else session_id

        # `alert_type` (added in migration 0004) toggles whether this row
        # surfaces on the user-facing Alert Timeline. Pass it for
        # operational alerts (Network / API / Campaign / System); leave
        # NULL for security-only events that should stay in the admin
        # security panel.
        async with acquire_with_tenant(
            self.db_pool, str(tenant_uuid) if tenant_uuid is not None else None
        ) as conn:
            await conn.execute(
                """
                INSERT INTO security_events (
                    event_id, event_type, severity, description,
                    user_id, tenant_id, session_id, detection_source,
                    evidence, title, status, alert_type
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                event_id,
                event_type,
                severity,
                description,
                user_uuid,
                tenant_uuid,
                session_uuid,
                detection_source,
                # Raw dict — the pool's jsonb codec (app.core.db) encodes
                # via json.dumps on write; a pre-dumped string here would
                # be double-encoded into a JSON string scalar.
                evidence if evidence else None,
                title or event_type.replace("_", " ").title(),
                "open",
                alert_type,
            )

        return event_id

    def _ensure_flush_task(self) -> None:
        """Make sure a timer will write the buffer even if no log() follows.

        Called with the lock held. The task ends once the buffer is empty, so
        an idle logger holds no task, and a new entry starts a new one.
        """
        if self._closed or not self._batch:
            return
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._flush_task = loop.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_seconds)
            try:
                await self.flush()
            except Exception as exc:
                logger.warning("audit_logger timed flush failed: %s", exc)
            # No await between this check and clearing the handle, so a log()
            # arriving later sees no task and starts a new one.
            if not self._batch:
                self._flush_task = None
                return

    async def aclose(self) -> None:
        """Stop the timer and write whatever is still buffered (shutdown hook)."""
        self._closed = True
        task = self._flush_task
        self._flush_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.flush()

    async def flush(self) -> None:
        """Flush batched entries to database."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        """Write the buffer. The caller holds ``self._lock``.

        The hash chain is built here, not in log(): every process has its own
        logger, so the only place that knows the real tail is the database,
        and only while the chain lock is held. See ``_CHAIN_LOCK_KEY``.

        A single bad row (e.g. orphan FK after a user was deleted while the
        event was still in-batch) must NEVER take down the whole flush —
        that propagates as a 500 to whichever unrelated request happened to
        trip the flush. So fall back to per-row inserts on failure and just
        drop the rows that the DB rejects.
        """
        if not self._batch:
            return

        # Snapshot + reset the batch first so a long INSERT doesn't block
        # concurrent log() calls from queueing new entries, and so an
        # unrecoverable error doesn't trap the same poison rows forever.
        batch = self._batch
        self._batch = []
        self._last_flush = datetime.utcnow()

        def as_row(e: dict) -> tuple:
            return (
                e["event_id"], e["event_time"], e["event_type"], e["event_category"],
                e["severity"], e["actor_id"], e["actor_type"], e["actor_role"],
                e["tenant_id"], e["resource_type"], e["resource_id"], e["ip_address"],
                e["user_agent"], e["session_id"], e["device_fingerprint"],
                e["country_code"], e["action"], e["description"],
                # Raw dicts — see log_security_event comment above.
                e["before_state"] if e["before_state"] else None,
                e["after_state"] if e["after_state"] else None,
                e["metadata"] if e["metadata"] else None,
                e["previous_hash"], e["entry_hash"], e["signature"],
                e["compliance_tags"], e["retention_until"],
            )

        sql = """
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
        """

        # One batch can contain several tenants and platform (NULL-tenant) rows.
        try:
            async with acquire_with_tenant(self.db_pool, None) as conn:
                # One chain writer at a time across every process. The lock is
                # held until this transaction ends, so it covers the tail read
                # and the inserts. The wait is bounded; the setting goes back
                # to the default afterwards so the inserts are not subject to it.
                await conn.execute(
                    f"SET LOCAL lock_timeout = {_CHAIN_LOCK_TIMEOUT_MS}; "
                    f"SELECT pg_advisory_xact_lock({_CHAIN_LOCK_KEY}); "
                    "SET LOCAL lock_timeout TO DEFAULT"
                )
                # A separate statement, taken after the lock: under READ
                # COMMITTED (the default) it sees what the previous holder
                # committed. Same order verify_chain_integrity walks, newest first.
                tail = await conn.fetchrow(
                    "SELECT entry_hash, event_time FROM audit_logs "
                    "ORDER BY event_time DESC, created_at DESC LIMIT 1"
                )
                tail_hash = tail["entry_hash"] if tail else "0" * 64
                tail_time = tail["event_time"] if tail else None

                rows = [as_row(e) for e in self._chain(batch, tail_hash, tail_time)]
                try:
                    # A savepoint: a failed batch must not abort the enclosing
                    # transaction, or every per-row retry below would fail too.
                    async with conn.transaction():
                        await conn.executemany(sql, rows)
                    return
                except Exception as exc:
                    logger.warning(
                        "audit_logger batch flush failed (%s rows): %s — "
                        "falling back to per-row inserts and dropping rejected rows.",
                        len(rows), exc,
                    )

                # Per-row fallback. Each insert runs in its own savepoint, so
                # one bad row no longer poisons the rest. The failed batch was
                # rolled back, so the tail is unchanged; the chain is rebuilt
                # over the rows the database accepts, so a dropped row leaves
                # no hole for the next row to point at.
                dropped = 0
                for entry in batch:
                    (linked,) = self._chain([entry], tail_hash, tail_time)
                    row = as_row(linked)
                    try:
                        async with conn.transaction():
                            await conn.execute(sql, *row)
                    except Exception as row_exc:
                        dropped += 1
                        logger.error(
                            "audit_logger dropping bad row event_id=%s actor_id=%s: %s",
                            row[0], row[5], row_exc,
                        )
                    else:
                        tail_hash, tail_time = linked["entry_hash"], linked["event_time"]
                if dropped:
                    logger.warning("audit_logger dropped %s of %s rows this flush", dropped, len(rows))
        except BaseException:
            # The database was unreachable (or we were cancelled mid-write):
            # nothing was stored, so the entries go back in front of anything
            # logged meanwhile and the next flush retries them in chain order.
            self._batch = batch + self._batch
            raise

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
        event_id: Optional[UUID | str] = None,
        event_group: Optional[str] = None,
        user_query: Optional[str] = None,
        partner_id: Optional[UUID | str] = None,
        before_time: Optional[datetime] = None,
        before_id: Optional[UUID | str] = None,
        platform_read: bool = False,
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

        if event_id:
            event_uuid = UUID(event_id) if isinstance(event_id, str) else event_id
            conditions.append(f"event_id = ${param_idx}")
            params.append(event_uuid)
            param_idx += 1

        # Named operator groups are explicit producer contracts. Exact
        # event_type remains an independent AND predicate above.
        if event_group:
            groups = {
                "login": "event_type IN ('login_success','login_failure')",
                "billing_change": "event_type = 'billing_updated'",
                "role_change": """(event_type IN ('role_assigned','role_removed') OR
                    (resource_type='user' AND (action IN ('role_updated','user_primary_membership_configured')
                    OR (action='user_updated' AND jsonb_typeof(metadata->'new_role')='string'
                        AND NULLIF(btrim(metadata->>'new_role'),'') IS NOT NULL))))""",
                "suspension": """(event_type IN ('user_suspended','user_restored','tenant_suspended','tenant_restored')
                    OR (resource_type='suspension' AND action IN ('appeal_submitted','appeal_reviewed')))""",
            }
            conditions.append(groups[event_group])
        if user_query and user_query.strip():
            literal = user_query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(f"""(a.actor_id::text ILIKE ${param_idx} OR EXISTS (
                SELECT 1 FROM user_profiles p WHERE p.id=a.actor_id
                AND (p.name ILIKE ${param_idx} OR p.email ILIKE ${param_idx})))""")
            params.append("%" + literal + "%")
            param_idx += 1
        if partner_id:
            partner_uuid = UUID(partner_id) if isinstance(partner_id, str) else partner_id
            conditions.append(f"""(EXISTS (SELECT 1 FROM tenants t WHERE t.id=a.tenant_id
                AND t.white_label_partner_id=${param_idx})
                OR (a.resource_type='partner' AND a.resource_id=${param_idx}))""")
            params.append(partner_uuid)
            param_idx += 1
        if before_time is not None and before_id is not None:
            cursor_id = UUID(before_id) if isinstance(before_id, str) else before_id
            conditions.append(f"(event_time,event_id) < (${param_idx},${param_idx + 1})")
            params.extend([before_time, cursor_id])
            param_idx += 2

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT a.* FROM audit_logs a
            {where_clause}
            ORDER BY event_time DESC,event_id DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """
        params.extend([limit, offset])

        # Only the canonical list opts into platform_read after current-role
        # validation. Keep event tenant/partner predicates even when current
        # actor profiles belong elsewhere; detail/export retain their defaults.
        async with acquire_with_tenant(
            self.db_pool, None if platform_read else str(tenant_id) if tenant_id is not None else None
        ) as conn:
            rows = await conn.fetch(query, *params)

        return [AuditLogEntry(**dict(row)) for row in rows]

    def _recompute_row_hash(self, row) -> str:
        """Hash a stored row; returns the stored hash if any accepted form of it matches.

        Empty before/after/metadata are stored as NULL. Entries written before
        log() normalised them were hashed as ``{}``, so a NULL column is also
        tried as ``{}`` before a row is declared altered.
        """
        entry = {
            key: row[key]
            for key in ("event_time", "event_type", "actor_id", "tenant_id", "action")
        }
        states = {}
        for key in ("before_state", "after_state", "metadata"):
            value = row[key]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            states[key] = value
        nullable = [key for key, value in states.items() if value is None]
        first = None
        for fills in itertools.product((None, {}), repeat=len(nullable)):
            candidate = {**entry, **states, **dict(zip(nullable, fills))}
            computed = self._compute_hash(candidate)
            if first is None:
                first = computed
            if computed == row["entry_hash"]:
                return computed
        return first

    async def verify_chain_integrity(self) -> dict:
        """
        Verify the integrity of the audit log chain.

        Returns:
            dict with verification results
        """
        # The tamper-evident chain is verified across the entire platform.
        # Chain order is event_time order: _flush_locked keeps event_time
        # strictly increasing along the chain while it holds the chain lock,
        # so created_at (the transaction start) never decides the order.
        async with acquire_with_tenant(self.db_pool, None) as conn:
            rows = await conn.fetch(
                "SELECT event_id, event_time, event_type, actor_id, tenant_id, action, "
                "before_state, after_state, metadata, previous_hash, entry_hash "
                "FROM audit_logs ORDER BY event_time, created_at"
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

            # Recompute the hash over the same content _compute_hash signs.
            computed_hash = self._recompute_row_hash(row)

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
