-- Day 9
-- Tenant telephony concurrency policies, leases, and event trail.
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

CREATE TABLE IF NOT EXISTS tenant_telephony_concurrency_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    max_active_calls INTEGER NOT NULL DEFAULT 10 CHECK (max_active_calls BETWEEN 1 AND 1000),
    max_transfer_inflight INTEGER NOT NULL DEFAULT 2 CHECK (max_transfer_inflight BETWEEN 1 AND 500),
    lease_ttl_seconds INTEGER NOT NULL DEFAULT 120 CHECK (lease_ttl_seconds BETWEEN 10 AND 3600),
    heartbeat_grace_seconds INTEGER NOT NULL DEFAULT 30 CHECK (heartbeat_grace_seconds BETWEEN 5 AND 600),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_telephony_concurrency_policy UNIQUE (tenant_id, policy_name)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_policy_active_unique
    ON tenant_telephony_concurrency_policies(tenant_id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_policy_tenant_active
    ON tenant_telephony_concurrency_policies(tenant_id, is_active, updated_at DESC);


CREATE TABLE IF NOT EXISTS tenant_telephony_concurrency_leases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_id UUID REFERENCES tenant_telephony_concurrency_policies(id) ON DELETE SET NULL,
    call_id UUID NOT NULL,
    talklee_call_id VARCHAR(64) NOT NULL,
    lease_kind VARCHAR(16) NOT NULL CHECK (lease_kind IN ('call', 'transfer')),
    state VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'releasing', 'released', 'expired')),
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    release_reason VARCHAR(64),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tenant_telephony_concurrency_release_consistency
        CHECK (
            (state IN ('released', 'expired') AND released_at IS NOT NULL) OR
            (state IN ('active', 'releasing'))
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_leases_active_unique
    ON tenant_telephony_concurrency_leases(tenant_id, call_id, lease_kind)
    WHERE released_at IS NULL AND state IN ('active', 'releasing');

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_leases_tenant_active
    ON tenant_telephony_concurrency_leases(tenant_id, state, acquired_at DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_leases_tenant_heartbeat
    ON tenant_telephony_concurrency_leases(tenant_id, last_heartbeat_at DESC);


CREATE TABLE IF NOT EXISTS tenant_telephony_concurrency_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_id UUID REFERENCES tenant_telephony_concurrency_policies(id) ON DELETE SET NULL,
    lease_id UUID REFERENCES tenant_telephony_concurrency_leases(id) ON DELETE SET NULL,
    event_type VARCHAR(16) NOT NULL CHECK (event_type IN ('acquire', 'reject', 'release', 'expire', 'heartbeat')),
    lease_kind VARCHAR(16) NOT NULL CHECK (lease_kind IN ('call', 'transfer')),
    call_id UUID,
    talklee_call_id VARCHAR(64),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_id VARCHAR(128),
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_events_tenant_created
    ON tenant_telephony_concurrency_events(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_events_event_type
    ON tenant_telephony_concurrency_events(tenant_id, event_type, created_at DESC);


DROP TRIGGER IF EXISTS update_tenant_telephony_concurrency_policies_updated_at ON tenant_telephony_concurrency_policies;
CREATE TRIGGER update_tenant_telephony_concurrency_policies_updated_at
    BEFORE UPDATE ON tenant_telephony_concurrency_policies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tenant_telephony_concurrency_leases_updated_at ON tenant_telephony_concurrency_leases;
CREATE TRIGGER update_tenant_telephony_concurrency_leases_updated_at
    BEFORE UPDATE ON tenant_telephony_concurrency_leases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


INSERT INTO tenant_telephony_concurrency_policies (
    tenant_id,
    policy_name,
    max_active_calls,
    max_transfer_inflight,
    lease_ttl_seconds,
    heartbeat_grace_seconds,
    is_active,
    metadata
)
SELECT
    t.id,
    'runtime-default',
    10,
    2,
    120,
    30,
    TRUE,
    '{"seeded_by":"day9"}'::jsonb
FROM tenants t
ON CONFLICT (tenant_id, policy_name) DO NOTHING;


ALTER TABLE tenant_telephony_concurrency_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_concurrency_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_concurrency_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE tenant_telephony_concurrency_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_concurrency_leases FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_concurrency_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_tenant_telephony_concurrency_policies_select ON tenant_telephony_concurrency_policies;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_policies_insert ON tenant_telephony_concurrency_policies;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_policies_update ON tenant_telephony_concurrency_policies;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_policies_delete ON tenant_telephony_concurrency_policies;

CREATE POLICY p_tenant_telephony_concurrency_policies_select ON tenant_telephony_concurrency_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_policies_insert ON tenant_telephony_concurrency_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_policies_update ON tenant_telephony_concurrency_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_policies_delete ON tenant_telephony_concurrency_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_telephony_concurrency_leases_select ON tenant_telephony_concurrency_leases;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_leases_insert ON tenant_telephony_concurrency_leases;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_leases_update ON tenant_telephony_concurrency_leases;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_leases_delete ON tenant_telephony_concurrency_leases;

CREATE POLICY p_tenant_telephony_concurrency_leases_select ON tenant_telephony_concurrency_leases
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_leases_insert ON tenant_telephony_concurrency_leases
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_leases_update ON tenant_telephony_concurrency_leases
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_leases_delete ON tenant_telephony_concurrency_leases
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_telephony_concurrency_events_select ON tenant_telephony_concurrency_events;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_events_insert ON tenant_telephony_concurrency_events;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_events_update ON tenant_telephony_concurrency_events;
DROP POLICY IF EXISTS p_tenant_telephony_concurrency_events_delete ON tenant_telephony_concurrency_events;

CREATE POLICY p_tenant_telephony_concurrency_events_select ON tenant_telephony_concurrency_events
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_events_insert ON tenant_telephony_concurrency_events
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_events_update ON tenant_telephony_concurrency_events
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_concurrency_events_delete ON tenant_telephony_concurrency_events
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
