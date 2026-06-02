-- Day 8: Audit Logs + Suspension System + Secrets Management
-- Migration for comprehensive audit logging, formalized suspension, and secrets management

-- Enable UUID v7 extension if not exists
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 1. AUDIT LOGS TABLE (Immutable security event log)
-- ============================================
CREATE TABLE audit_logs (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
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

    -- Integrity (tamper-evident)
    previous_hash VARCHAR(64),
    entry_hash VARCHAR(64) NOT NULL,
    signature VARCHAR(128),

    -- Compliance
    compliance_tags VARCHAR(50)[],
    retention_until DATE NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for audit_logs
CREATE INDEX idx_audit_logs_event_time ON audit_logs(event_time DESC);
CREATE INDEX idx_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX idx_audit_logs_actor_id ON audit_logs(actor_id);
CREATE INDEX idx_audit_logs_tenant_id ON audit_logs(tenant_id);
CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_logs_category ON audit_logs(event_category);
CREATE INDEX idx_audit_logs_severity ON audit_logs(severity);
CREATE INDEX idx_audit_logs_retention ON audit_logs(retention_until);
CREATE INDEX idx_audit_logs_compliance ON audit_logs USING GIN(compliance_tags);

-- Composite indexes for common queries
CREATE INDEX idx_audit_logs_tenant_time ON audit_logs(tenant_id, event_time DESC);
CREATE INDEX idx_audit_logs_actor_time ON audit_logs(actor_id, event_time DESC);

-- Comments
COMMENT ON TABLE audit_logs IS 'Immutable security audit log with chain integrity';
COMMENT ON COLUMN audit_logs.previous_hash IS 'SHA-256 hash of previous entry for chain integrity';
COMMENT ON COLUMN audit_logs.entry_hash IS 'SHA-256 hash of this entry content';
COMMENT ON COLUMN audit_logs.signature IS 'HMAC-SHA256 signature for verification';

-- ============================================
-- 2. SECURITY EVENTS TABLE (High-priority alerts)
-- ============================================
CREATE TABLE security_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Classification
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(10) NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'investigating', 'resolved', 'false_positive', 'escalated')),

    -- Scope
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES user_profiles(id),
    session_id UUID,

    -- Detection
    detection_source VARCHAR(50) NOT NULL, -- abuse_detection, session_security, manual, automated
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

-- Indexes for security_events
CREATE INDEX idx_security_events_status ON security_events(status);
CREATE INDEX idx_security_events_severity ON security_events(severity);
CREATE INDEX idx_security_events_tenant ON security_events(tenant_id);
CREATE INDEX idx_security_events_user ON security_events(user_id);
CREATE INDEX idx_security_events_created ON security_events(created_at DESC);
CREATE INDEX idx_security_events_assigned ON security_events(assigned_to);
CREATE INDEX idx_security_events_sla ON security_events(sla_deadline) WHERE status = 'open';

-- Comments
COMMENT ON TABLE security_events IS 'High-priority security alerts requiring action';

-- ============================================
-- 3. SUSPENSION EVENTS TABLE (Formal suspension history)
-- ============================================
CREATE TABLE suspension_events (
    suspension_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Target
    target_type VARCHAR(20) NOT NULL CHECK (target_type IN ('user', 'tenant', 'partner')),
    target_id UUID NOT NULL,

    -- Suspension details
    suspension_type VARCHAR(30) NOT NULL, -- TEMPORARY, ADMIN, BILLING, ABUSE, COMPLIANCE, EMERGENCY
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
    appeal_response TEXT,

    -- Audit reference
    audit_log_id UUID REFERENCES audit_logs(event_id)
);

-- Indexes for suspension_events
CREATE INDEX idx_suspension_events_target ON suspension_events(target_type, target_id);
CREATE INDEX idx_suspension_events_active ON suspension_events(target_id, is_active) WHERE is_active = TRUE;
CREATE INDEX idx_suspension_events_type ON suspension_events(suspension_type);
CREATE INDEX idx_suspension_events_suspended_at ON suspension_events(suspended_at DESC);
CREATE INDEX idx_suspension_events_tenant ON suspension_events(target_id) WHERE target_type = 'tenant';
CREATE INDEX idx_suspension_events_user ON suspension_events(target_id) WHERE target_type = 'user';
CREATE INDEX idx_suspension_events_appeal ON suspension_events(appeal_submitted_at) WHERE appeal_decision = 'pending';

-- Comments
COMMENT ON TABLE suspension_events IS 'Formal suspension history with appeal workflow';

-- ============================================
-- 4. TENANT SECRETS TABLE (Encrypted secrets storage)
-- ============================================
CREATE TABLE tenant_secrets (
    secret_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Ownership
    tenant_id UUID REFERENCES tenants(id),
    created_by UUID REFERENCES user_profiles(id),

    -- Secret metadata
    secret_type VARCHAR(30) NOT NULL, -- API_KEY, WEBHOOK_HMAC, INTEGRATION_OAUTH, PLATFORM
    secret_name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Encryption (envelope encryption)
    encrypted_value BYTEA NOT NULL,
    encrypted_dek BYTEA NOT NULL, -- Data Encryption Key (encrypted by KEK)
    iv BYTEA NOT NULL, -- Initialization vector
    algorithm VARCHAR(20) NOT NULL DEFAULT 'AES-256-GCM',

    -- Access control
    permissions JSONB DEFAULT '{}', -- {roles: [], users: [], ip_whitelist: []}

    -- Rotation
    version INTEGER NOT NULL DEFAULT 1,
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

-- Indexes for tenant_secrets
CREATE INDEX idx_tenant_secrets_tenant ON tenant_secrets(tenant_id);
CREATE INDEX idx_tenant_secrets_type ON tenant_secrets(secret_type);
CREATE INDEX idx_tenant_secrets_active ON tenant_secrets(tenant_id, is_active) WHERE is_active = TRUE;
CREATE INDEX idx_tenant_secrets_expires ON tenant_secrets(expires_at) WHERE expires_at IS NOT NULL AND is_active = TRUE;
CREATE INDEX idx_tenant_secrets_rotated_from ON tenant_secrets(rotated_from);

-- Comments
COMMENT ON TABLE tenant_secrets IS 'Encrypted tenant-specific secrets with envelope encryption';
COMMENT ON COLUMN tenant_secrets.encrypted_dek IS 'Data Encryption Key encrypted by master KEK';
COMMENT ON COLUMN tenant_secrets.iv IS 'Initialization vector for AES-GCM';

-- ============================================
-- 5. SECRET ACCESS LOG TABLE (Audit trail for secret access)
-- ============================================
CREATE TABLE secret_access_log (
    access_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    secret_id UUID NOT NULL REFERENCES tenant_secrets(secret_id),
    tenant_id UUID REFERENCES tenants(id),

    accessed_by UUID REFERENCES user_profiles(id),
    access_type VARCHAR(30) NOT NULL, -- read, rotate, revoke, validate, create
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

-- Indexes for secret_access_log
CREATE INDEX idx_secret_access_secret ON secret_access_log(secret_id);
CREATE INDEX idx_secret_access_time ON secret_access_log(accessed_at DESC);
CREATE INDEX idx_secret_access_user ON secret_access_log(accessed_by);
CREATE INDEX idx_secret_access_success ON secret_access_log(success);
CREATE INDEX idx_secret_access_tenant ON secret_access_log(tenant_id);

-- Comments
COMMENT ON TABLE secret_access_log IS 'Audit trail for all secret access operations';

-- ============================================
-- 6. EMERGENCY ACCESS REQUESTS TABLE (Break-glass access)
-- ============================================
CREATE TABLE emergency_access_requests (
    request_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Request details
    requestor_id UUID NOT NULL REFERENCES user_profiles(id),
    scenario VARCHAR(50) NOT NULL, -- security_incident, platform_admin_lockout, compliance, disaster_recovery
    justification TEXT NOT NULL,
    requested_access TEXT[] NOT NULL,

    -- Approval chain (dual control)
    approvers_required INTEGER NOT NULL DEFAULT 2,
    approvals JSONB DEFAULT '[]', -- [{approver_id, approved_at, method, verification_code}]

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'used', 'cancelled')),
    approved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,

    -- Session
    session_created_at TIMESTAMPTZ,
    session_terminated_at TIMESTAMPTZ,
    session_token_hash VARCHAR(64),
    actions_taken JSONB DEFAULT '[]',

    -- Post-review
    reviewed_at TIMESTAMPTZ,
    reviewed_by UUID REFERENCES user_profiles(id),
    review_notes TEXT
);

-- Indexes for emergency_access_requests
CREATE INDEX idx_emergency_access_status ON emergency_access_requests(status);
CREATE INDEX idx_emergency_access_requestor ON emergency_access_requests(requestor_id);
CREATE INDEX idx_emergency_access_created ON emergency_access_requests(created_at DESC);
CREATE INDEX idx_emergency_access_expires ON emergency_access_requests(expires_at) WHERE status = 'approved';

-- Comments
COMMENT ON TABLE emergency_access_requests IS 'Break-glass emergency access audit trail';

-- ============================================
-- 7. AUDIT LOG CHAIN TABLE (For integrity verification)
-- ============================================
CREATE TABLE audit_chain_state (
    id SERIAL PRIMARY KEY,
    last_event_id UUID REFERENCES audit_logs(event_id),
    last_event_hash VARCHAR(64) NOT NULL,
    events_count BIGINT NOT NULL DEFAULT 0,
    verified_at TIMESTAMPTZ,
    verification_result BOOLEAN,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Initialize chain state
INSERT INTO audit_chain_state (last_event_hash, events_count) VALUES ('0' * 64, 0);

COMMENT ON TABLE audit_chain_state IS 'Tracks audit log chain for integrity verification';

-- ============================================
-- 8. SUSPENSION PROPAGATION QUEUE (For reliable propagation)
-- ============================================
CREATE TABLE suspension_propagation_queue (
    queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    suspension_id UUID NOT NULL REFERENCES suspension_events(suspension_id),
    target_type VARCHAR(20) NOT NULL,
    target_id UUID NOT NULL,
    action VARCHAR(20) NOT NULL CHECK (action IN ('suspend', 'restore')),

    service_name VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'failed')),

    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    last_error TEXT,
    completed_at TIMESTAMPTZ,

    next_attempt_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX idx_suspension_queue_status ON suspension_propagation_queue(status, next_attempt_at);
CREATE INDEX idx_suspension_queue_suspension ON suspension_propagation_queue(suspension_id);

COMMENT ON TABLE suspension_propagation_queue IS 'Queue for reliable suspension propagation to services';

-- ============================================
-- TRIGGERS
-- ============================================

-- Update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_tenant_secrets_updated_at BEFORE UPDATE ON tenant_secrets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- RETENTION POLICY FUNCTION
-- ============================================
CREATE OR REPLACE FUNCTION apply_audit_retention()
RETURNS void AS $$
BEGIN
    -- Archive and delete expired audit logs
    -- In production, this would move to cold storage before deletion
    DELETE FROM audit_logs WHERE retention_until < CURRENT_DATE - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION apply_audit_retention() IS 'Archives and purges expired audit logs';

-- ============================================
-- VIEWS FOR COMMON QUERIES
-- ============================================

-- Active suspensions view
CREATE VIEW active_suspensions AS
SELECT * FROM suspension_events
WHERE is_active = TRUE AND (suspended_until IS NULL OR suspended_until > NOW());

-- Expiring secrets view
CREATE VIEW expiring_secrets AS
SELECT * FROM tenant_secrets
WHERE is_active = TRUE
  AND expires_at IS NOT NULL
  AND expires_at < NOW() + INTERVAL '7 days';

-- Open security events view
CREATE VIEW open_security_events AS
SELECT * FROM security_events
WHERE status IN ('open', 'investigating', 'escalated');

-- ============================================
-- INITIAL DATA
-- ============================================

-- Insert default audit event types reference (for documentation)
CREATE TABLE IF NOT EXISTS audit_event_types (
    event_type VARCHAR(50) PRIMARY KEY,
    category VARCHAR(30) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    description TEXT,
    retention_days INTEGER NOT NULL
);

INSERT INTO audit_event_types (event_type, category, severity, description, retention_days) VALUES
-- Authentication events
('login_success', 'AUTHENTICATION', 'INFO', 'User successfully logged in', 365),
('login_failure', 'AUTHENTICATION', 'WARNING', 'Failed login attempt', 365),
('logout', 'AUTHENTICATION', 'INFO', 'User logged out', 365),
('session_created', 'AUTHENTICATION', 'INFO', 'New session created', 365),
('session_terminated', 'AUTHENTICATION', 'INFO', 'Session terminated', 365),
('password_changed', 'AUTHENTICATION', 'INFO', 'User changed password', 365),
('mfa_enabled', 'AUTHENTICATION', 'INFO', 'MFA enabled for account', 365),
('mfa_disabled', 'AUTHENTICATION', 'WARNING', 'MFA disabled for account', 365),
('passkey_registered', 'AUTHENTICATION', 'INFO', 'New passkey registered', 365),
('passkey_removed', 'AUTHENTICATION', 'INFO', 'Passkey removed', 365),
-- Authorization events
('permission_denied', 'AUTHORIZATION', 'WARNING', 'Access denied due to insufficient permissions', 365),
('role_assigned', 'AUTHORIZATION', 'INFO', 'Role assigned to user', 1095),
('role_removed', 'AUTHORIZATION', 'INFO', 'Role removed from user', 1095),
('privilege_escalation', 'AUTHORIZATION', 'CRITICAL', 'User escalated privileges', 1095),
-- User management
('user_created', 'USER_MANAGEMENT', 'INFO', 'New user account created', 1095),
('user_updated', 'USER_MANAGEMENT', 'INFO', 'User profile updated', 1095),
('user_suspended', 'USER_MANAGEMENT', 'WARNING', 'User account suspended', 1095),
('user_restored', 'USER_MANAGEMENT', 'INFO', 'User account restored', 1095),
('user_deleted', 'USER_MANAGEMENT', 'WARNING', 'User account deleted', 1095),
-- Tenant admin
('tenant_created', 'TENANT_ADMIN', 'INFO', 'New tenant created', 2555),
('tenant_updated', 'TENANT_ADMIN', 'INFO', 'Tenant configuration updated', 2555),
('tenant_suspended', 'TENANT_ADMIN', 'CRITICAL', 'Tenant suspended', 2555),
('tenant_restored', 'TENANT_ADMIN', 'INFO', 'Tenant restored', 2555),
('billing_updated', 'TENANT_ADMIN', 'INFO', 'Billing information updated', 2555),
('limits_changed', 'TENANT_ADMIN', 'INFO', 'Tenant limits modified', 2555),
-- Security events
('suspicious_activity', 'SECURITY', 'HIGH', 'Suspicious activity detected', 1095),
('session_hijacking_detected', 'SECURITY', 'CRITICAL', 'Potential session hijacking', 1095),
('rate_limit_exceeded', 'SECURITY', 'WARNING', 'Rate limit exceeded', 365),
('api_key_created', 'SECURITY', 'INFO', 'New API key created', 1095),
('api_key_revoked', 'SECURITY', 'WARNING', 'API key revoked', 1095),
-- Data access
('record_viewed', 'DATA_ACCESS', 'INFO', 'Record accessed', 365),
('record_exported', 'DATA_ACCESS', 'INFO', 'Data exported', 365),
('bulk_download', 'DATA_ACCESS', 'WARNING', 'Bulk data download', 365),
('cross_tenant_access', 'DATA_ACCESS', 'CRITICAL', 'Cross-tenant data access attempted', 1095),
-- System
('config_changed', 'SYSTEM', 'INFO', 'System configuration changed', 2555),
('secret_rotated', 'SYSTEM', 'INFO', 'Secret rotated', 2555),
('secret_revoked', 'SYSTEM', 'WARNING', 'Secret revoked', 2555),
('key_revoked', 'SYSTEM', 'WARNING', 'Signing key revoked', 2555),
('emergency_access_requested', 'SYSTEM', 'CRITICAL', 'Emergency access requested', 2555),
('emergency_access_approved', 'SYSTEM', 'CRITICAL', 'Emergency access approved', 2555),
('emergency_access_used', 'SYSTEM', 'CRITICAL', 'Emergency access session used', 2555)
ON CONFLICT (event_type) DO NOTHING;

-- ============================================
-- PARTITIONING FOR AUDIT LOGS (if supported)
-- ============================================
-- Note: For PostgreSQL 12+, consider partitioning by month:
-- CREATE TABLE audit_logs_partitioned (LIKE audit_logs) PARTITION BY RANGE (event_time);
-- This would require a separate migration script to create partitions

-- ============================================
-- GRANTS
-- ============================================
-- Grant appropriate permissions to application role
-- GRANT SELECT, INSERT ON audit_logs TO app_role;
-- GRANT SELECT, INSERT, UPDATE ON security_events TO app_role;
-- GRANT SELECT, INSERT, UPDATE ON suspension_events TO app_role;
-- GRANT SELECT, INSERT, UPDATE ON tenant_secrets TO app_role;
-- GRANT SELECT, INSERT ON secret_access_log TO app_role;
-- GRANT SELECT, INSERT, UPDATE ON emergency_access_requests TO app_role;

COMMIT;
