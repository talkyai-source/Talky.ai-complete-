-- Phase 2 / WS-H
-- Tenant isolation + trust policy model.
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

CREATE TABLE IF NOT EXISTS tenant_sip_trust_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    allowed_source_cidrs CIDR[] NOT NULL DEFAULT ARRAY[]::CIDR[],
    blocked_source_cidrs CIDR[] NOT NULL DEFAULT ARRAY[]::CIDR[],
    kamailio_group SMALLINT NOT NULL DEFAULT 1 CHECK (kamailio_group > 0),
    priority INTEGER NOT NULL DEFAULT 100 CHECK (priority BETWEEN 1 AND 10000),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tenant_sip_trust_has_source
        CHECK (cardinality(allowed_source_cidrs) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_sip_trust_policies_tenant_name_unique
    ON tenant_sip_trust_policies(tenant_id, lower(policy_name));

CREATE INDEX IF NOT EXISTS idx_tenant_sip_trust_policies_tenant_active
    ON tenant_sip_trust_policies(tenant_id, is_active, priority);

DROP TRIGGER IF EXISTS update_tenant_sip_trust_policies_updated_at ON tenant_sip_trust_policies;
CREATE TRIGGER update_tenant_sip_trust_policies_updated_at
    BEFORE UPDATE ON tenant_sip_trust_policies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- RLS baseline for WS-F / WS-G / WS-H tenant policy tables.
ALTER TABLE tenant_sip_trunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_codec_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_route_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_sip_trust_policies ENABLE ROW LEVEL SECURITY;

ALTER TABLE tenant_sip_trunks FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_codec_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_route_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_idempotency FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_events FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_sip_trust_policies FORCE ROW LEVEL SECURITY;

-- tenant_sip_trunks
DROP POLICY IF EXISTS p_tenant_sip_trunks_select ON tenant_sip_trunks;
DROP POLICY IF EXISTS p_tenant_sip_trunks_insert ON tenant_sip_trunks;
DROP POLICY IF EXISTS p_tenant_sip_trunks_update ON tenant_sip_trunks;
DROP POLICY IF EXISTS p_tenant_sip_trunks_delete ON tenant_sip_trunks;

CREATE POLICY p_tenant_sip_trunks_select ON tenant_sip_trunks
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trunks_insert ON tenant_sip_trunks
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trunks_update ON tenant_sip_trunks
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trunks_delete ON tenant_sip_trunks
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

-- tenant_codec_policies
DROP POLICY IF EXISTS p_tenant_codec_policies_select ON tenant_codec_policies;
DROP POLICY IF EXISTS p_tenant_codec_policies_insert ON tenant_codec_policies;
DROP POLICY IF EXISTS p_tenant_codec_policies_update ON tenant_codec_policies;
DROP POLICY IF EXISTS p_tenant_codec_policies_delete ON tenant_codec_policies;

CREATE POLICY p_tenant_codec_policies_select ON tenant_codec_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_codec_policies_insert ON tenant_codec_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_codec_policies_update ON tenant_codec_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_codec_policies_delete ON tenant_codec_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

-- tenant_route_policies
DROP POLICY IF EXISTS p_tenant_route_policies_select ON tenant_route_policies;
DROP POLICY IF EXISTS p_tenant_route_policies_insert ON tenant_route_policies;
DROP POLICY IF EXISTS p_tenant_route_policies_update ON tenant_route_policies;
DROP POLICY IF EXISTS p_tenant_route_policies_delete ON tenant_route_policies;

CREATE POLICY p_tenant_route_policies_select ON tenant_route_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_route_policies_insert ON tenant_route_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_route_policies_update ON tenant_route_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_route_policies_delete ON tenant_route_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

-- tenant_telephony_idempotency
DROP POLICY IF EXISTS p_tenant_telephony_idempotency_select ON tenant_telephony_idempotency;
DROP POLICY IF EXISTS p_tenant_telephony_idempotency_insert ON tenant_telephony_idempotency;
DROP POLICY IF EXISTS p_tenant_telephony_idempotency_update ON tenant_telephony_idempotency;
DROP POLICY IF EXISTS p_tenant_telephony_idempotency_delete ON tenant_telephony_idempotency;

CREATE POLICY p_tenant_telephony_idempotency_select ON tenant_telephony_idempotency
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_idempotency_insert ON tenant_telephony_idempotency
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_idempotency_update ON tenant_telephony_idempotency
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_idempotency_delete ON tenant_telephony_idempotency
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

-- tenant_runtime_policy_versions
DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_select ON tenant_runtime_policy_versions;
DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_insert ON tenant_runtime_policy_versions;
DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_update ON tenant_runtime_policy_versions;
DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_delete ON tenant_runtime_policy_versions;

CREATE POLICY p_tenant_runtime_policy_versions_select ON tenant_runtime_policy_versions
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_versions_insert ON tenant_runtime_policy_versions
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_versions_update ON tenant_runtime_policy_versions
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_versions_delete ON tenant_runtime_policy_versions
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

-- tenant_runtime_policy_events
DROP POLICY IF EXISTS p_tenant_runtime_policy_events_select ON tenant_runtime_policy_events;
DROP POLICY IF EXISTS p_tenant_runtime_policy_events_insert ON tenant_runtime_policy_events;
DROP POLICY IF EXISTS p_tenant_runtime_policy_events_update ON tenant_runtime_policy_events;
DROP POLICY IF EXISTS p_tenant_runtime_policy_events_delete ON tenant_runtime_policy_events;

CREATE POLICY p_tenant_runtime_policy_events_select ON tenant_runtime_policy_events
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_events_insert ON tenant_runtime_policy_events
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_events_update ON tenant_runtime_policy_events
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_events_delete ON tenant_runtime_policy_events
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

-- tenant_sip_trust_policies
DROP POLICY IF EXISTS p_tenant_sip_trust_policies_select ON tenant_sip_trust_policies;
DROP POLICY IF EXISTS p_tenant_sip_trust_policies_insert ON tenant_sip_trust_policies;
DROP POLICY IF EXISTS p_tenant_sip_trust_policies_update ON tenant_sip_trust_policies;
DROP POLICY IF EXISTS p_tenant_sip_trust_policies_delete ON tenant_sip_trust_policies;

CREATE POLICY p_tenant_sip_trust_policies_select ON tenant_sip_trust_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trust_policies_insert ON tenant_sip_trust_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trust_policies_update ON tenant_sip_trust_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trust_policies_delete ON tenant_sip_trust_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

