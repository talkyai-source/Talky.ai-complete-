-- Day 6: API Security + Rate Limiting
-- OWASP API Security Top 10 2023
-- Created: 2026-03-17

-- =============================================
-- 1. Rate Limiting Audit Log
-- =============================================
-- Tracks rate limit events for security monitoring and analysis

CREATE TABLE IF NOT EXISTS rate_limit_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tier TEXT NOT NULL CHECK (tier IN ('ip', 'user', 'tenant', 'global')),
    scope_key TEXT NOT NULL, -- IP address, user_id, or tenant_id
    endpoint TEXT,
    action_taken TEXT NOT NULL CHECK (action_taken IN ('allow', 'warn', 'throttle', 'block')),
    limit_config JSONB, -- Stores the configuration that triggered the action
    triggered_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for querying events by scope and time
CREATE INDEX IF NOT EXISTS idx_rate_limit_events_scope
    ON rate_limit_events(tier, scope_key, triggered_at DESC);

-- Index for time-based cleanup queries
CREATE INDEX IF NOT EXISTS idx_rate_limit_events_triggered_at
    ON rate_limit_events(triggered_at DESC);

-- =============================================
-- 2. Webhook Configurations
-- =============================================
-- Stores HMAC secrets and configuration for webhook signature verification

CREATE TABLE IF NOT EXISTS webhook_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    webhook_name TEXT NOT NULL,
    secret_key TEXT NOT NULL, -- HMAC secret (store encrypted in production)
    signature_algorithm TEXT DEFAULT 'hmac-sha256',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Each tenant can only have one config per webhook name
    UNIQUE(tenant_id, webhook_name)
);

-- Index for webhook lookup
CREATE INDEX IF NOT EXISTS idx_webhook_configs_tenant
    ON webhook_configs(tenant_id, webhook_name)
    WHERE is_active = TRUE;

-- =============================================
-- 3. Idempotency Keys
-- =============================================
-- PostgreSQL backup for idempotency keys (Redis is primary)
-- Provides durability across cache restarts

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key_hash TEXT PRIMARY KEY, -- SHA-256 hash of the idempotency key
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    request_method TEXT NOT NULL,
    request_path TEXT NOT NULL,
    request_body_hash TEXT, -- Hash of request body for integrity
    response_status INTEGER,
    response_body_hash TEXT, -- Hash of response body
    response_body_preview TEXT, -- Small preview of response (optional)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Index for expiration cleanup
CREATE INDEX IF NOT EXISTS idx_idempotency_keys_expires
    ON idempotency_keys(expires_at);

-- Index for tenant lookups
CREATE INDEX IF NOT EXISTS idx_idempotency_keys_tenant
    ON idempotency_keys(tenant_id, created_at DESC);

-- =============================================
-- 4. Security Event Log (Extended)
-- =============================================
-- Extension for API security events

CREATE TABLE IF NOT EXISTS api_security_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL, -- 'webhook_verify_failed', 'idempotency_conflict', etc.
    source_ip INET,
    user_agent TEXT,
    request_path TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for querying by tenant and time
CREATE INDEX IF NOT EXISTS idx_api_security_events_tenant_time
    ON api_security_events(tenant_id, event_type, created_at DESC);

-- =============================================
-- 5. Functions and Triggers
-- =============================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for webhook_configs
DROP TRIGGER IF EXISTS update_webhook_configs_updated_at ON webhook_configs;
CREATE TRIGGER update_webhook_configs_updated_at
    BEFORE UPDATE ON webhook_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================
-- 6. Cleanup Function
-- =============================================
-- Function to clean up expired idempotency keys

CREATE OR REPLACE FUNCTION cleanup_expired_idempotency_keys()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM idempotency_keys
    WHERE expires_at < NOW();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =============================================
-- 7. Comments
-- =============================================

COMMENT ON TABLE rate_limit_events IS 'Audit log for rate limiting actions - Day 6 API Security';
COMMENT ON TABLE webhook_configs IS 'Webhook HMAC signature configuration - Day 6 API Security';
COMMENT ON TABLE idempotency_keys IS 'Idempotency key backup store (Redis is primary) - Day 6 API Security';
COMMENT ON TABLE api_security_events IS 'API security event log - Day 6 API Security';
