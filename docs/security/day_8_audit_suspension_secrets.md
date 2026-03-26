# Day 8: Audit Logs + Suspension System + Secrets Management

## Overview

Day 8 implements the final security layer: comprehensive audit logging for compliance and forensics, a formalized suspension system with instant propagation, and centralized secrets management. This completes the security foundation with full observability, operational control, and secure credential handling.

## References

- **OWASP Logging Cheat Sheet** (2025) - https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- **NIST SP 800-92** - Guide to Computer Security Log Management
- **SOC 2 Type II** - Audit trail requirements for access control changes
- **GDPR Article 33** - Breach notification and audit requirements
- **HashiCorp Vault Security Model** - Secrets management best practices
- **AWS Secrets Manager Rotation** - Automated secret rotation patterns

## Components

### 1. Comprehensive Audit Logging Service (`app/domain/services/audit_logger.py`)

Unified audit logging system that captures all security-relevant events with tamper-evident properties.

**Audit Event Categories:**

| Category | Events | Retention |
|----------|--------|-----------|
| AUTHENTICATION | login_success, login_failure, logout, session_created, session_terminated | 1 year |
| AUTHORIZATION | permission_denied, role_assigned, role_removed, privilege_escalation | 3 years |
| USER_MANAGEMENT | user_created, user_updated, user_suspended, user_deleted | 3 years |
| TENANT_ADMIN | tenant_created, tenant_suspended, tenant_limits_changed, billing_updated | 7 years |
| SECURITY | mfa_enabled, passkey_registered, password_changed, suspicious_activity | 3 years |
| DATA_ACCESS | record_viewed, record_exported, bulk_download, cross_tenant_access | 1 year |
| SYSTEM | config_changed, secret_rotated, key_revoked, emergency_access | 7 years |

**Tamper-Evident Design:**

```
Each audit log entry includes:
- event_id: UUID v7 (time-sortable)
- previous_hash: SHA-256 of previous entry (chain integrity)
- signature: HMAC-SHA256 of entry content
- created_at: UTC timestamp with microsecond precision
- actor_fingerprint: Device/IP/session binding
```

**Usage:**

```python
from app.domain.services.audit_logger import AuditLogger, AuditEvent

audit = AuditLogger(db_pool)

# Log authentication event
await audit.log(
    event_type=AuditEvent.LOGIN_SUCCESS,
    actor_id=user_id,
    tenant_id=tenant_id,
    resource_type="session",
    resource_id=session_id,
    metadata={
        "ip_address": request.client.host,
        "user_agent": request.headers.get("user-agent"),
        "mfa_used": True,
        "auth_method": "passkey"
    }
)

# Log admin action with before/after state
await audit.log_admin_action(
    action="role_assigned",
    actor_id=admin_id,
    target_user_id=user_id,
    tenant_id=tenant_id,
    before_state={"role": "user"},
    after_state={"role": "tenant_admin"},
    reason="Promotion for project ownership"
)
```

### 2. Suspension System (`app/domain/services/suspension_service.py`)

Formalized suspension workflow with instant block propagation and appeal workflow.

**Suspension Types:**

| Type | Scope | Use Case | Auto-Restore |
|------|-------|----------|--------------|
| TEMPORARY | User | Password breach, suspicious login | Yes, after duration |
| ADMIN | User | Policy violation, manual review | No |
| BILLING | Tenant | Payment failure, invoice dispute | Yes, after payment |
| ABUSE | Tenant | Spam, fraud, ToS violation | No |
| COMPLIANCE | Partner/Tenant | Legal hold, regulatory request | No |
| EMERGENCY | Platform | Critical security incident | No |

**Block Propagation:**

```
Suspension Event → Redis Pub/Sub → All Services

Affected Services:
├── API Gateway (blocks new requests)
├── Session Manager (terminates active sessions)
├── Call Guard (blocks new calls)
├── Webhook Queue (pauses delivery)
├── Background Jobs (pauses processing)
└── CDN/WAF (blocks if configured)
```

**Usage:**

```python
from app.domain.services.suspension_service import SuspensionService, SuspensionType

suspension = SuspensionService(db_pool, redis_client)

# Suspend tenant for abuse
result = await suspension.suspend_tenant(
    tenant_id=tenant_id,
    suspension_type=SuspensionType.ABUSE,
    reason="Detected toll fraud pattern - Wangiri scheme",
    evidence={
        "abuse_event_id": event_id,
        "call_pattern": "sequential_dialing",
        "affected_numbers": ["+1234567890", "+1234567891"]
    },
    suspended_by=admin_id,
    duration_hours=None  # Indefinite, requires manual restore
)

# Instant propagation
await suspension.propagate_blocks(
    scope="tenant",
    target_id=tenant_id,
    action="suspend"
)

# Check suspension status
status = await suspension.get_status(
    tenant_id=tenant_id,
    user_id=user_id
)
# Returns: active, suspended, pending_review, scheduled_restore
```

### 3. Secrets Management Service (`app/domain/services/secrets_manager.py`)

Centralized secrets management with encryption at rest, rotation tracking, and access audit.

**Secret Types:**

| Type | Storage | Rotation | Example |
|------|---------|----------|---------|
| PLATFORM | HashiCorp Vault/AWS Secrets Manager | 90 days | Database credentials |
| TENANT_API_KEY | Database (encrypted) | On demand | Tenant API access |
| WEBHOOK_HMAC | Database (encrypted) | 180 days | Webhook signatures |
| INTEGRATION_OAUTH | Database (encrypted) | On refresh | Third-party tokens |
| EMERGENCY_BREAK_GLASS | Hardware security module | Manual | Root access keys |

**Encryption Architecture:**

```
Secret Value
    ↓
Envelope Encryption:
├── Data Encryption Key (DEK) - Per-secret AES-256-GCM
├── Key Encryption Key (KEK) - Master key in KMS/HSM
└── Encrypted DEK stored with ciphertext

Key Hierarchy:
Root Key (HSM) → KEK → DEK → Secret
```

**Usage:**

```python
from app.domain.services.secrets_manager import SecretsManager, SecretType

secrets = SecretsManager(db_pool, kms_client)

# Store tenant API key
secret_id = await secrets.create(
    secret_type=SecretType.TENANT_API_KEY,
    owner_type="tenant",
    owner_id=tenant_id,
    value={"api_key": "tk_live_...", "permissions": ["read", "write"]},
    metadata={
        "created_by": admin_id,
        "purpose": "Production API access",
        "ip_whitelist": ["203.0.113.0/24"]
    },
    rotation_days=90
)

# Retrieve with audit
value = await secrets.get(
    secret_id=secret_id,
    accessed_by=user_id,
    access_reason="Webhook configuration"
)

# Rotate secret
new_secret_id = await secrets.rotate(
    secret_id=secret_id,
    rotated_by=admin_id,
    grace_period_hours=24  # Old key valid for transition
)

# Validate API key (constant-time comparison)
is_valid = await secrets.validate_api_key(
    api_key=presented_key,
    required_permission="calls:write"
)
```

### 4. Emergency Access System (`app/core/security/emergency_access.py`)

Break-glass access for critical incidents with mandatory dual-control and full audit.

**Emergency Scenarios:**

| Scenario | Required Approvers | Time Limit | Post-Action |
|----------|-------------------|------------|-------------|
| Platform admin lockout | 2 senior admins + security | 4 hours | Full audit review |
| Security incident response | 1 C-level + 1 security | 24 hours | Board notification |
| Compliance investigation | Legal + CISO | Case duration | Regulatory filing |
| Disaster recovery | 2 senior ops + on-call | 72 hours | Post-mortem |

**Usage:**

```python
from app.core.security.emergency_access import EmergencyAccess

emergency = EmergencyAccess(db_pool)

# Request break-glass access
request = await emergency.request(
    requestor_id=admin_id,
    scenario="security_incident",
    justification="Suspected data exfiltration - need immediate access",
    required_access=["audit_logs:read", "sessions:terminate"]
)

# Second approver confirms
await emergency.approve(
    request_id=request.id,
    approver_id=senior_admin_id,
    approval_code=sms_verification_code
)

# Access granted for limited time
session = await emergency.create_session(
    request_id=request.id,
    ttl_hours=4
)
```

## Database Schema

### Tables

**audit_logs** - Immutable security event log
```sql
CREATE TABLE audit_logs (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    event_category VARCHAR(30) NOT NULL,
    severity VARCHAR(10) NOT NULL DEFAULT 'INFO',

    -- Actor
    actor_id UUID REFERENCES user_profiles(id),
    actor_type VARCHAR(20) NOT NULL DEFAULT 'user',
    actor_role VARCHAR(50),

    -- Target
    tenant_id UUID REFERENCES tenants(id),
    resource_type VARCHAR(50),
    resource_id UUID,

    -- Location/Device
    ip_address INET,
    user_agent TEXT,
    session_id UUID,
    device_fingerprint VARCHAR(64),
    country_code CHAR(2),

    -- Content
    action VARCHAR(100) NOT NULL,
    description TEXT,
    before_state JSONB,
    after_state JSONB,
    metadata JSONB,

    -- Integrity
    previous_hash VARCHAR(64),
    entry_hash VARCHAR(64) NOT NULL,
    signature VARCHAR(128) NOT NULL,

    -- Compliance
    compliance_tags VARCHAR(50)[],
    retention_until DATE NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**security_events** - High-priority security alerts requiring action
```sql
CREATE TABLE security_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Classification
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(10) NOT NULL, -- CRITICAL, HIGH, MEDIUM, LOW
    status VARCHAR(20) NOT NULL DEFAULT 'open', -- open, investigating, resolved, false_positive

    -- Scope
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES user_profiles(id),
    session_id UUID,

    -- Detection
    detection_source VARCHAR(50) NOT NULL, -- abuse_detection, session_security, manual
    rule_id UUID REFERENCES abuse_detection_rules(id),

    -- Details
    title VARCHAR(200) NOT NULL,
    description TEXT,
    evidence JSONB,

    -- Response
    assigned_to UUID REFERENCES user_profiles(id),
    resolved_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES user_profiles(id),
    resolution_notes TEXT,

    -- Automated response
    auto_action_taken VARCHAR(50),
    auto_action_success BOOLEAN,

    -- SLA tracking
    sla_deadline TIMESTAMPTZ,
    first_response_at TIMESTAMPTZ
);
```

**suspension_events** - Formal suspension history
```sql
CREATE TABLE suspension_events (
    suspension_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Target
    target_type VARCHAR(20) NOT NULL, -- user, tenant, partner
    target_id UUID NOT NULL,

    -- Suspension details
    suspension_type VARCHAR(30) NOT NULL,
    reason_category VARCHAR(50) NOT NULL,
    reason_description TEXT NOT NULL,
    evidence JSONB,

    -- Timing
    suspended_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    suspended_until TIMESTAMPTZ, -- NULL = indefinite
    restored_at TIMESTAMPTZ,

    -- Actors
    suspended_by UUID REFERENCES user_profiles(id),
    restored_by UUID REFERENCES user_profiles(id),
    restore_reason TEXT,

    -- State tracking
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    propagated_services VARCHAR(50)[],
    propagation_confirmed_at TIMESTAMPTZ,

    -- Appeal workflow
    appeal_submitted_at TIMESTAMPTZ,
    appeal_reason TEXT,
    appeal_reviewed_by UUID REFERENCES user_profiles(id),
    appeal_decision VARCHAR(20), -- granted, denied, pending

    -- Audit reference
    audit_log_id UUID REFERENCES audit_logs(event_id)
);
```

**tenant_secrets** - Encrypted tenant-specific secrets
```sql
CREATE TABLE tenant_secrets (
    secret_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Ownership
    tenant_id UUID REFERENCES tenants(id),
    created_by UUID REFERENCES user_profiles(id),

    -- Secret metadata
    secret_type VARCHAR(30) NOT NULL,
    secret_name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Encryption (envelope encryption)
    encrypted_value BYTEA NOT NULL,
    encrypted_dek BYTEA NOT NULL, -- Data Encryption Key (encrypted by KEK)
    iv BYTEA NOT NULL, -- Initialization vector
    algorithm VARCHAR(20) NOT NULL DEFAULT 'AES-256-GCM',

    -- Access control
    permissions JSONB, -- {roles: [], users: [], ip_whitelist: []}

    -- Rotation
    created_at_version INTEGER NOT NULL DEFAULT 1,
    rotated_from UUID REFERENCES tenant_secrets(secret_id),
    rotated_to UUID REFERENCES tenant_secrets(secret_id),
    rotated_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,

    -- Usage tracking
    last_accessed_at TIMESTAMPTZ,
    last_accessed_by UUID,
    access_count INTEGER NOT NULL DEFAULT 0,

    -- Status
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_compromised BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at TIMESTAMPTZ,
    revoked_reason TEXT,

    UNIQUE(tenant_id, secret_name, is_active)
);
```

**secret_access_log** - Audit trail for secret access
```sql
CREATE TABLE secret_access_log (
    access_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    secret_id UUID NOT NULL REFERENCES tenant_secrets(secret_id),
    tenant_id UUID REFERENCES tenants(id),

    accessed_by UUID REFERENCES user_profiles(id),
    access_type VARCHAR(30) NOT NULL, -- read, rotate, revoke, validate
    access_reason TEXT,

    -- Context
    ip_address INET,
    user_agent TEXT,
    success BOOLEAN NOT NULL,
    failure_reason TEXT,

    -- For API key validation (no user context)
    api_key_prefix VARCHAR(16),
    presented_permission VARCHAR(50)
);
```

**emergency_access_requests** - Break-glass access audit
```sql
CREATE TABLE emergency_access_requests (
    request_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Request details
    requestor_id UUID NOT NULL REFERENCES user_profiles(id),
    scenario VARCHAR(50) NOT NULL,
    justification TEXT NOT NULL,
    requested_access TEXT[] NOT NULL,

    -- Approval chain (dual control)
    approvers_required INTEGER NOT NULL DEFAULT 2,
    approvals JSONB DEFAULT '[]', -- [{approver_id, approved_at, method}]

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, approved, denied, expired, used
    approved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,

    -- Session
    session_created_at TIMESTAMPTZ,
    session_terminated_at TIMESTAMPTZ,
    actions_taken JSONB DEFAULT '[]',

    -- Post-review
    reviewed_at TIMESTAMPTZ,
    reviewed_by UUID REFERENCES user_profiles(id),
    review_notes TEXT
);
```

**Migrations:** `database/migrations/day8_audit_suspension_secrets.sql`

## API Endpoints

### Audit Logs (`/api/v1/admin/audit/`)

```
GET    /logs                     # Query audit logs (filtered)
GET    /logs/{event_id}          # Get specific event
GET    /logs/export              # Export for compliance (CSV/JSON)
POST   /logs/query               # Advanced query with JSON body

# Analytics
GET    /stats/events-by-type     # Event type distribution
GET    /stats/failed-logins      # Failed login analysis
GET    /stats/admin-actions      # Admin action summary
```

**Query Parameters:**
- `start_date`, `end_date` - Time range
- `event_type` - Filter by type
- `tenant_id` - Tenant-scoped query
- `user_id` - User-specific query
- `severity` - MIN severity level

### Security Events (`/api/v1/admin/security-events/`)

```
GET    /events                   # List security events
GET    /events/{event_id}        # Get event details
PATCH  /events/{event_id}        # Update status/assignee
POST   /events/{event_id}/resolve # Mark resolved

# Alerting
GET    /alerts/open              # Open high-priority events
GET    /alerts/overdue           # Past SLA deadline
POST   /events/{event_id}/escalate # Escalate to senior team
```

### Suspension Management (`/api/v1/admin/suspensions/`)

```
# User suspension
POST   /users/{user_id}/suspend
POST   /users/{user_id}/restore
GET    /users/{user_id}/history

# Tenant suspension
POST   /tenants/{tenant_id}/suspend
POST   /tenants/{tenant_id}/restore
GET    /tenants/{tenant_id}/status

# Partner suspension
POST   /partners/{partner_id}/suspend
POST   /partners/{partner_id}/restore

# Appeals
POST   /{suspension_id}/appeal           # Submit appeal
POST   /{suspension_id}/appeal/review     # Review appeal

# Bulk operations
POST   /bulk-suspend                      # Mass suspend by criteria
GET    /propagation-status/{suspension_id} # Check block propagation
```

**Suspend Request Body:**
```json
{
  "suspension_type": "ABUSE",
  "reason_category": "toll_fraud",
  "reason_description": "Wangiri pattern detected",
  "evidence": {"event_id": "uuid", "confidence": 0.95},
  "duration_hours": null,
  "immediate": true,
  "notify_user": true
}
```

### Secrets Management (`/api/v1/admin/secrets/`)

```
# Tenant secrets
POST   /tenants/{tenant_id}/secrets      # Create secret
GET    /tenants/{tenant_id}/secrets      # List secrets (no values)
GET    /tenants/{tenant_id}/secrets/{id} # Get secret (with audit)
PUT    /tenants/{tenant_id}/secrets/{id} # Update metadata
POST   /tenants/{tenant_id}/secrets/{id}/rotate  # Rotate value
DELETE /tenants/{tenant_id}/secrets/{id} # Revoke secret

# Validation
POST   /validate-api-key                 # Check API key validity

# Platform secrets (platform admin only)
GET    /platform/secrets
POST   /platform/secrets
POST   /platform/secrets/{id}/rotate
```

### Emergency Access (`/api/v1/admin/emergency/`)

```
POST   /request               # Request break-glass access
POST   /{request_id}/approve  # Approve request
POST   /{request_id}/deny     # Deny request
POST   /{request_id}/session  # Create emergency session
DELETE /{request_id}/session  # Terminate early
GET    /requests              # List requests (audit)
```

## Configuration

### Audit Log Configuration

```python
# app/core/config.py
class AuditConfig:
    # Retention (days)
    RETENTION_AUTH = 365
    RETENTION_ADMIN = 365 * 3
    RETENTION_TENANT = 365 * 7
    RETENTION_SYSTEM = 365 * 7

    # Storage
    BATCH_SIZE = 100  # Events per batch insert
    FLUSH_INTERVAL_SECONDS = 5

    # Alerting
    ALERT_ON_EVENTS = ["privilege_escalation", "emergency_access_used"]
    ALERT_WEBHOOK_URL = os.getenv("AUDIT_ALERT_WEBHOOK")

    # Export
    EXPORT_ENCRYPTION_KEY = os.getenv("AUDIT_EXPORT_KEY")  # AES-256 for exports
```

### Suspension Configuration

```python
class SuspensionConfig:
    # Auto-restore
    AUTO_RESTORE = {
        SuspensionType.TEMPORARY: True,
        SuspensionType.BILLING: True,
        SuspensionType.ABUSE: False,
    }

    # Notification
    NOTIFY_USER_ON_SUSPEND = True
    NOTIFY_ADMIN_ON_APPEAL = True

    # Propagation timeout
    PROPAGATION_TIMEOUT_SECONDS = 30
    PROPAGATION_SERVICES = [
        "api_gateway",
        "session_manager",
        "call_guard",
        "webhook_queue",
        "background_worker"
    ]
```

### Secrets Configuration

```python
class SecretsConfig:
    # KMS provider
    KMS_PROVIDER = os.getenv("KMS_PROVIDER", "aws")  # aws, gcp, azure, hashicorp
    KMS_KEY_ID = os.getenv("KMS_KEY_ID")

    # Rotation
    DEFAULT_ROTATION_DAYS = 90
    MAX_SECRET_AGE_DAYS = 365
    ROTATION_GRACE_PERIOD_HOURS = 24

    # API Keys
    API_KEY_PREFIX = "tk_live_"  # Production
    API_KEY_PREFIX_TEST = "tk_test_"
    API_KEY_LENGTH = 48

    # Validation
    RATE_LIMIT_VALIDATION = 100  # per minute per IP
```

## Security Considerations

### Audit Log Integrity

1. **Chain Hashing**: Each entry includes hash of previous entry
2. **Async Signing**: Background job signs batches with HMAC
3. **Immutable Storage**: Write-once database partition
4. **Export Encryption**: All exports encrypted with separate key
5. **Tamper Detection**: Daily verification job detects chain breaks

### Suspension Propagation

1. **Event-Driven**: Redis pub/sub for instant propagation
2. **Retry Logic**: Exponential backoff for failed propagations
3. **Confirmation**: Services confirm receipt of suspension
4. **Emergency Override**: Platform admin can force unblock
5. **Grace Period**: 30-second delay before blocking existing sessions

### Secrets Security

1. **Envelope Encryption**: Master key never leaves KMS/HSM
2. **Automatic Rotation**: Optional automatic rotation on schedule
3. **Versioning**: All rotations preserve history for audit
4. **Access Logging**: Every access logged with justification
5. **Compromise Recovery**: One-click revoke + rotate on breach
6. **No Plaintext**: Secrets never logged or returned in APIs

## Integration

### Adding Audit Logging to Endpoints

```python
from app.domain.services.audit_logger import audit_log

@router.post("/users")
async def create_user(
    data: UserCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    audit: AuditLogger = Depends(get_audit_logger)
):
    user = await create_user(data)

    await audit.log(
        event_type=AuditEvent.USER_CREATED,
        actor_id=current_user.id,
        tenant_id=data.tenant_id,
        resource_type="user",
        resource_id=user.id,
        metadata={"created_via": "api", "initial_role": data.role}
    )

    return user
```

### Automatic Suspension from Abuse Detection

```python
# In abuse_detection.py
from app.domain.services.suspension_service import SuspensionService

async def handle_critical_event(event: AbuseEvent):
    if event.severity == Severity.CRITICAL:
        suspension = SuspensionService(db, redis)

        result = await suspension.suspend_tenant(
            tenant_id=event.tenant_id,
            suspension_type=SuspensionType.ABUSE,
            reason=f"Auto-suspended: {event.abuse_type}",
            evidence=event.to_dict(),
            suspended_by=None,  # System
            immediate=True
        )

        # Create security event for tracking
        await create_security_event(
            type="auto_suspension",
            related_abuse_event=event.event_id,
            suspension_id=result.suspension_id
        )
```

### Secrets in Connectors

```python
# Encrypting connector credentials
from app.domain.services.secrets_manager import SecretsManager

async def save_connector_config(
    tenant_id: str,
    credentials: dict
):
    secrets = SecretsManager(db, kms)

    # Store encrypted
    secret_id = await secrets.create(
        secret_type=SecretType.INTEGRATION_OAUTH,
        owner_type="tenant",
        owner_id=tenant_id,
        value=credentials,
        metadata={"connector": "salesforce", "environment": "production"}
    )

    # Store only reference in connector config
    await db.execute(
        "UPDATE connectors SET secret_id = $1 WHERE id = $2",
        secret_id, connector_id
    )
```

## Monitoring

### Key Metrics

| Metric | Type | Alert Threshold |
|--------|------|-----------------|
| audit_log_latency_seconds | Histogram | p99 > 100ms |
| audit_log_backlog_size | Gauge | > 1000 events |
| suspension_propagation_seconds | Histogram | p99 > 5s |
| suspension_propagation_failures | Counter | > 0 in 5min |
| secrets_rotation_overdue | Gauge | > 0 |
| secret_access_denied | Counter | > 10/min |
| emergency_access_requests | Counter | > 1/day |
| security_events_open | Gauge | > 10 critical |

### Alerts

```yaml
# Critical alerts
- alert: SuspensionPropagationFailed
  expr: suspension_propagation_failures > 0
  severity: critical

- alert: AuditLogBacklogGrowing
  expr: rate(audit_log_backlog_size[5m]) > 10
  severity: high

- alert: EmergencyAccessUsed
  expr: emergency_access_requests > 0
  severity: warning

- alert: SecretCompromiseDetected
  expr: secrets_marked_compromised > 0
  severity: critical
```

## Testing

### Unit Tests

```python
async def test_audit_log_chain_integrity():
    audit = AuditLogger(db)

    # Create entries
    id1 = await audit.log(event_type=AuditEvent.LOGIN_SUCCESS, ...)
    id2 = await audit.log(event_type=AuditEvent.LOGOUT, ...)

    # Verify chain
    entry1 = await db.fetchrow("SELECT * FROM audit_logs WHERE event_id = $1", id1)
    entry2 = await db.fetchrow("SELECT * FROM audit_logs WHERE event_id = $1", id2)

    assert entry2.previous_hash == entry1.entry_hash

async def test_suspension_propagates_to_sessions():
    suspension = SuspensionService(db, redis)

    # Create active session
    session = await create_session(user_id, tenant_id)

    # Suspend tenant
    await suspension.suspend_tenant(
        tenant_id=tenant_id,
        suspension_type=SuspensionType.ABUSE,
        ...
    )

    # Verify session terminated
    sessions = await get_active_sessions(tenant_id)
    assert len(sessions) == 0

async def test_secrets_encryption():
    secrets = SecretsManager(db, kms)

    # Store secret
    secret_id = await secrets.create(
        secret_type=SecretType.TENANT_API_KEY,
        value={"key": "secret_value"},
        ...
    )

    # Verify encrypted in DB
    row = await db.fetchrow(
        "SELECT encrypted_value FROM tenant_secrets WHERE secret_id = $1",
        secret_id
    )
    assert row["encrypted_value"] != b"secret_value"
    assert b"key" not in row["encrypted_value"]  # Not just base64
```

### Integration Tests

```python
async def test_full_suspension_workflow():
    # 1. Detect abuse
    detector = AbuseDetectionService(db, redis)
    events = await detector.analyze_call_initiated(tenant_id, "+1234567890")

    # 2. Auto-suspend
    assert events[0].auto_action == "suspend"

    # 3. Verify blocks
    response = await client.post(
        "/api/v1/sip/telephony/call",
        headers={"X-Tenant-ID": tenant_id}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "tenant_suspended"

    # 4. Verify audit trail
    logs = await client.get("/api/v1/admin/audit/logs?event_type=tenant_suspended")
    assert len(logs.json()) == 1

async def test_emergency_access_dual_control():
    # Request access
    response = await client.post(
        "/api/v1/admin/emergency/request",
        json={"scenario": "security_incident", ...},
        headers={"X-User-ID": admin1_id}
    )
    request_id = response.json()["request_id"]

    # First approval
    await client.post(
        f"/api/v1/admin/emergency/{request_id}/approve",
        headers={"X-User-ID": admin2_id}
    )

    # Second approval
    await client.post(
        f"/api/v1/admin/emergency/{request_id}/approve",
        headers={"X-User-ID": admin3_id}
    )

    # Access granted
    status = await client.get(f"/api/v1/admin/emergency/{request_id}")
    assert status.json()["status"] == "approved"
```

## Migration

Apply Day 8 migration:

```bash
psql -U postgres -d talky -f database/migrations/day8_audit_suspension_secrets.sql
```

This creates:
1. All audit and security tables with indexes
2. Chain integrity triggers
3. Default retention policies
4. Partitioning for audit_logs (monthly partitions)
5. Encryption key reference table

## Post-Migration Tasks

1. **Initialize Audit Chain**: Run script to set initial hash
2. **Backfill Suspension History**: Migrate existing `is_active=false` to suspension_events
3. **Encrypt Existing Secrets**: Migrate webhook_configs to tenant_secrets format
4. **Configure KMS**: Set up KMS key rotation policy
5. **Enable Monitoring**: Deploy alerts and dashboards

## Future Enhancements

- Real-time audit log streaming (Kafka/Kinesis)
- ML-based anomaly detection on audit patterns
- Blockchain anchoring for audit integrity
- Automated suspension appeal via AI assessment
- Hardware security module (HSM) integration
- Cross-region audit replication
