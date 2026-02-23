-- =============================================================================
-- Security Features Migration
-- =============================================================================
--
-- This migration adds security enhancements including:
--   1. tenant_quotas - Action limits per tenant
--   2. tenant_quota_usage - Daily usage tracking
--   3. Enhanced assistant_actions columns for full audit
--   4. Token rotation tracking on connector_accounts
--   5. Replay protection via idempotency keys
--
-- Run this AFTER add_assistant_agent.sql has been applied.
--
-- Created: January 15, 2026
-- Project: Talky.ai - Security Enhancement
-- =============================================================================

-- =============================================================================
-- SECTION 1: TENANT QUOTAS TABLE
-- Per-tenant action limits
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_quotas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Daily action limits
    emails_per_day INTEGER DEFAULT 50,
    sms_per_day INTEGER DEFAULT 25,
    calls_per_day INTEGER DEFAULT 50,
    meetings_per_day INTEGER DEFAULT 10,
    -- Special limits
    max_concurrent_connectors INTEGER DEFAULT 5,
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id)
);

-- Indexes for tenant_quotas
CREATE INDEX IF NOT EXISTS idx_tenant_quotas_tenant_id ON tenant_quotas(tenant_id);

-- Trigger for updated_at
DROP TRIGGER IF EXISTS update_tenant_quotas_updated_at ON tenant_quotas;
CREATE TRIGGER update_tenant_quotas_updated_at BEFORE UPDATE ON tenant_quotas
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Comments
COMMENT ON TABLE tenant_quotas IS 'Per-tenant action quotas and limits';
COMMENT ON COLUMN tenant_quotas.emails_per_day IS 'Maximum emails sent per day';
COMMENT ON COLUMN tenant_quotas.sms_per_day IS 'Maximum SMS sent per day';
COMMENT ON COLUMN tenant_quotas.calls_per_day IS 'Maximum calls initiated per day';
COMMENT ON COLUMN tenant_quotas.meetings_per_day IS 'Maximum meetings booked per day';

-- =============================================================================
-- SECTION 2: TENANT QUOTA USAGE TABLE
-- Daily usage tracking per tenant
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_quota_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
    -- Usage counters
    emails_sent INTEGER DEFAULT 0,
    sms_sent INTEGER DEFAULT 0,
    calls_initiated INTEGER DEFAULT 0,
    meetings_booked INTEGER DEFAULT 0,
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, usage_date)
);

-- Indexes for tenant_quota_usage
CREATE INDEX IF NOT EXISTS idx_quota_usage_tenant_id ON tenant_quota_usage(tenant_id);
CREATE INDEX IF NOT EXISTS idx_quota_usage_tenant_date ON tenant_quota_usage(tenant_id, usage_date);
CREATE INDEX IF NOT EXISTS idx_quota_usage_date ON tenant_quota_usage(usage_date);

-- Trigger for updated_at
DROP TRIGGER IF EXISTS update_tenant_quota_usage_updated_at ON tenant_quota_usage;
CREATE TRIGGER update_tenant_quota_usage_updated_at BEFORE UPDATE ON tenant_quota_usage
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Comments
COMMENT ON TABLE tenant_quota_usage IS 'Daily action usage tracking per tenant';
COMMENT ON COLUMN tenant_quota_usage.usage_date IS 'Date of usage (for daily reset)';

-- =============================================================================
-- SECTION 3: ENHANCED ASSISTANT ACTIONS COLUMNS
-- Additional fields for full audit trail
-- =============================================================================

-- Add new columns for comprehensive audit logging
ALTER TABLE assistant_actions
ADD COLUMN IF NOT EXISTS ip_address INET,
ADD COLUMN IF NOT EXISTS user_agent TEXT,
ADD COLUMN IF NOT EXISTS request_id UUID,
ADD COLUMN IF NOT EXISTS outcome_status VARCHAR(50),
ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255);

-- Unique index for idempotency (scoped to tenant)
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_actions_idempotency 
ON assistant_actions(tenant_id, idempotency_key) 
WHERE idempotency_key IS NOT NULL;

-- Index for request correlation
CREATE INDEX IF NOT EXISTS idx_actions_request_id 
ON assistant_actions(request_id) 
WHERE request_id IS NOT NULL;

-- Index for outcome queries
CREATE INDEX IF NOT EXISTS idx_actions_outcome_status 
ON assistant_actions(outcome_status);

-- Comments
COMMENT ON COLUMN assistant_actions.ip_address IS 'Client IP address for web requests';
COMMENT ON COLUMN assistant_actions.user_agent IS 'Client user agent string';
COMMENT ON COLUMN assistant_actions.request_id IS 'UUID for request correlation';
COMMENT ON COLUMN assistant_actions.outcome_status IS 'Detailed outcome: success, quota_exceeded, permission_denied, replay_rejected, failed';
COMMENT ON COLUMN assistant_actions.idempotency_key IS 'Unique key for replay protection';

-- =============================================================================
-- SECTION 4: TOKEN ROTATION TRACKING
-- Additional columns on connector_accounts
-- =============================================================================

ALTER TABLE connector_accounts
ADD COLUMN IF NOT EXISTS token_last_rotated_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS rotation_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS revoked_reason TEXT;

-- Index for finding tokens needing refresh
CREATE INDEX IF NOT EXISTS idx_connector_accounts_status_expires 
ON connector_accounts(status, token_expires_at)
WHERE status = 'active';

-- Index for rotation monitoring
CREATE INDEX IF NOT EXISTS idx_connector_accounts_last_rotated 
ON connector_accounts(token_last_rotated_at)
WHERE status = 'active';

-- Comments
COMMENT ON COLUMN connector_accounts.token_last_rotated_at IS 'When token was last refreshed';
COMMENT ON COLUMN connector_accounts.rotation_count IS 'Number of times token was rotated';
COMMENT ON COLUMN connector_accounts.revoked_at IS 'When connector was revoked';
COMMENT ON COLUMN connector_accounts.revoked_reason IS 'Reason for revocation: user_requested, security, expired';

-- =============================================================================
-- SECTION 5: ROW LEVEL SECURITY
-- =============================================================================

-- Enable RLS on new tables
ALTER TABLE tenant_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_quota_usage ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------------------
-- 5.1 TENANT QUOTAS POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view their tenant quota" ON tenant_quotas;
CREATE POLICY "Users can view their tenant quota" ON tenant_quotas
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage quotas" ON tenant_quotas;
CREATE POLICY "Service role can manage quotas" ON tenant_quotas
    FOR ALL 
    TO service_role
    USING (true)
    WITH CHECK (true);

-- -----------------------------------------------------------------------------
-- 5.2 TENANT QUOTA USAGE POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view their usage" ON tenant_quota_usage;
CREATE POLICY "Users can view their usage" ON tenant_quota_usage
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage usage" ON tenant_quota_usage;
CREATE POLICY "Service role can manage usage" ON tenant_quota_usage
    FOR ALL 
    TO service_role
    USING (true)
    WITH CHECK (true);

-- =============================================================================
-- SECTION 6: DEFAULT QUOTAS FOR EXISTING TENANTS
-- Auto-create quota records for existing tenants
-- =============================================================================

-- Create default quotas for any tenants without them
INSERT INTO tenant_quotas (tenant_id, emails_per_day, sms_per_day, calls_per_day, meetings_per_day)
SELECT t.id, 
    CASE 
        WHEN t.plan_id = 'enterprise' THEN 1000
        WHEN t.plan_id = 'professional' THEN 200
        ELSE 50
    END,
    CASE 
        WHEN t.plan_id = 'enterprise' THEN 500
        WHEN t.plan_id = 'professional' THEN 100
        ELSE 25
    END,
    CASE 
        WHEN t.plan_id = 'enterprise' THEN 200
        WHEN t.plan_id = 'professional' THEN 100
        ELSE 50
    END,
    CASE 
        WHEN t.plan_id = 'enterprise' THEN 100
        WHEN t.plan_id = 'professional' THEN 20
        ELSE 10
    END
FROM tenants t
WHERE NOT EXISTS (
    SELECT 1 FROM tenant_quotas tq WHERE tq.tenant_id = t.id
)
ON CONFLICT (tenant_id) DO NOTHING;

-- =============================================================================
-- SUCCESS NOTIFICATION
-- =============================================================================

DO $$
BEGIN
    RAISE NOTICE '=============================================================================';
    RAISE NOTICE 'Security Features Migration completed successfully!';
    RAISE NOTICE '';
    RAISE NOTICE 'Tables created:';
    RAISE NOTICE '  - tenant_quotas: Per-tenant action limits';
    RAISE NOTICE '  - tenant_quota_usage: Daily usage tracking';
    RAISE NOTICE '';
    RAISE NOTICE 'Columns added to assistant_actions:';
    RAISE NOTICE '  - ip_address, user_agent, request_id, outcome_status, idempotency_key';
    RAISE NOTICE '';
    RAISE NOTICE 'Columns added to connector_accounts:';
    RAISE NOTICE '  - token_last_rotated_at, rotation_count, revoked_at, revoked_reason';
    RAISE NOTICE '';
    RAISE NOTICE 'RLS policies enabled on all new tables.';
    RAISE NOTICE 'Default quotas created for existing tenants.';
    RAISE NOTICE '=============================================================================';
END $$;
