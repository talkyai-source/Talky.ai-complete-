-- Phase 2 / WS-I
-- Tenant telephony quotas + abuse controls (Redis-backed enforcement + DB policy model).
-- Safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc
        WHERE proname = 'update_updated_at_column'
          AND pg_function_is_visible(oid)
    ) THEN
        CREATE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $fn$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS tenant_telephony_threshold_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    policy_scope VARCHAR(32) NOT NULL
        CHECK (policy_scope IN ('api_mutation', 'runtime_mutation', 'sip_edge')),
    metric_key VARCHAR(120) NOT NULL DEFAULT '*',
    window_seconds INTEGER NOT NULL DEFAULT 60 CHECK (window_seconds BETWEEN 1 AND 3600),
    warn_threshold INTEGER NOT NULL DEFAULT 20 CHECK (warn_threshold > 0),
    throttle_threshold INTEGER NOT NULL DEFAULT 30 CHECK (throttle_threshold > 0),
    block_threshold INTEGER NOT NULL DEFAULT 45 CHECK (block_threshold > 0),
    block_duration_seconds INTEGER NOT NULL DEFAULT 300 CHECK (block_duration_seconds BETWEEN 1 AND 86400),
    throttle_retry_seconds INTEGER NOT NULL DEFAULT 2 CHECK (throttle_retry_seconds BETWEEN 1 AND 60),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_telephony_threshold_policy UNIQUE (tenant_id, policy_scope, metric_key),
    CONSTRAINT chk_tenant_telephony_threshold_order
        CHECK (warn_threshold <= throttle_threshold AND throttle_threshold <= block_threshold)
);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_threshold_scope_active
    ON tenant_telephony_threshold_policies(tenant_id, policy_scope, is_active);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_threshold_metric
    ON tenant_telephony_threshold_policies(tenant_id, policy_scope, metric_key);


CREATE TABLE IF NOT EXISTS tenant_telephony_quota_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_id UUID REFERENCES tenant_telephony_threshold_policies(id) ON DELETE SET NULL,
    event_type VARCHAR(16) NOT NULL CHECK (event_type IN ('warn', 'throttle', 'block')),
    policy_scope VARCHAR(32) NOT NULL,
    metric_key VARCHAR(120) NOT NULL,
    counter_value BIGINT NOT NULL DEFAULT 0,
    threshold_value BIGINT,
    window_seconds INTEGER NOT NULL CHECK (window_seconds > 0),
    block_ttl_seconds INTEGER NOT NULL DEFAULT 0 CHECK (block_ttl_seconds >= 0),
    request_id VARCHAR(128),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_quota_events_tenant_created
    ON tenant_telephony_quota_events(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_quota_events_policy
    ON tenant_telephony_quota_events(policy_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_quota_events_scope_metric
    ON tenant_telephony_quota_events(tenant_id, policy_scope, metric_key, created_at DESC);


DROP TRIGGER IF EXISTS update_tenant_telephony_threshold_policies_updated_at ON tenant_telephony_threshold_policies;
CREATE TRIGGER update_tenant_telephony_threshold_policies_updated_at
    BEFORE UPDATE ON tenant_telephony_threshold_policies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- Seed baseline wildcard policies so enforcement is deterministic from first run.
INSERT INTO tenant_telephony_threshold_policies (
    tenant_id,
    policy_name,
    policy_scope,
    metric_key,
    window_seconds,
    warn_threshold,
    throttle_threshold,
    block_threshold,
    block_duration_seconds,
    throttle_retry_seconds,
    metadata
)
SELECT
    t.id,
    'api-default',
    'api_mutation',
    '*',
    60,
    20,
    30,
    45,
    300,
    2,
    '{"seeded_by":"ws-i"}'::jsonb
FROM tenants t
ON CONFLICT (tenant_id, policy_scope, metric_key) DO NOTHING;

INSERT INTO tenant_telephony_threshold_policies (
    tenant_id,
    policy_name,
    policy_scope,
    metric_key,
    window_seconds,
    warn_threshold,
    throttle_threshold,
    block_threshold,
    block_duration_seconds,
    throttle_retry_seconds,
    metadata
)
SELECT
    t.id,
    'runtime-default',
    'runtime_mutation',
    '*',
    60,
    10,
    15,
    20,
    300,
    2,
    '{"seeded_by":"ws-i"}'::jsonb
FROM tenants t
ON CONFLICT (tenant_id, policy_scope, metric_key) DO NOTHING;


-- RLS
ALTER TABLE tenant_telephony_threshold_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_quota_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE tenant_telephony_threshold_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_quota_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_select ON tenant_telephony_threshold_policies;
DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_insert ON tenant_telephony_threshold_policies;
DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_update ON tenant_telephony_threshold_policies;
DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_delete ON tenant_telephony_threshold_policies;

CREATE POLICY p_tenant_telephony_threshold_policies_select ON tenant_telephony_threshold_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_threshold_policies_insert ON tenant_telephony_threshold_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_threshold_policies_update ON tenant_telephony_threshold_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_threshold_policies_delete ON tenant_telephony_threshold_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_telephony_quota_events_select ON tenant_telephony_quota_events;
DROP POLICY IF EXISTS p_tenant_telephony_quota_events_insert ON tenant_telephony_quota_events;
DROP POLICY IF EXISTS p_tenant_telephony_quota_events_update ON tenant_telephony_quota_events;
DROP POLICY IF EXISTS p_tenant_telephony_quota_events_delete ON tenant_telephony_quota_events;

CREATE POLICY p_tenant_telephony_quota_events_select ON tenant_telephony_quota_events
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_quota_events_insert ON tenant_telephony_quota_events
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_quota_events_update ON tenant_telephony_quota_events
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_quota_events_delete ON tenant_telephony_quota_events
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
