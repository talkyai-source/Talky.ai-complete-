-- Phase 2 / WS-G
-- Runtime policy compiler artifacts + activation/rollback event ledger.
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

CREATE TABLE IF NOT EXISTS tenant_runtime_policy_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_version INTEGER NOT NULL CHECK (policy_version > 0),
    source_hash CHAR(64) NOT NULL,
    schema_version VARCHAR(32) NOT NULL DEFAULT 'ws-g.v1',
    input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    compiled_artifact JSONB NOT NULL,
    validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    build_status VARCHAR(20) NOT NULL DEFAULT 'compiled'
        CHECK (build_status IN ('compiled', 'active', 'failed', 'superseded', 'rolled_back')),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    is_last_good BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    activated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    CONSTRAINT uq_tenant_runtime_policy_version UNIQUE (tenant_id, policy_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_runtime_policy_versions_tenant_active_unique
    ON tenant_runtime_policy_versions(tenant_id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_versions_tenant_version
    ON tenant_runtime_policy_versions(tenant_id, policy_version DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_versions_tenant_last_good
    ON tenant_runtime_policy_versions(tenant_id, is_last_good, policy_version DESC);


CREATE TABLE IF NOT EXISTS tenant_runtime_policy_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_version_id UUID NOT NULL REFERENCES tenant_runtime_policy_versions(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL CHECK (action IN ('activate', 'rollback')),
    stage VARCHAR(20) NOT NULL CHECK (stage IN ('precheck', 'apply', 'verify', 'commit', 'rollback')),
    status VARCHAR(20) NOT NULL CHECK (status IN ('started', 'succeeded', 'failed')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_id VARCHAR(128),
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_events_tenant_created
    ON tenant_runtime_policy_events(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_events_policy_version
    ON tenant_runtime_policy_events(policy_version_id, created_at DESC);


DROP TRIGGER IF EXISTS update_tenant_runtime_policy_versions_updated_at ON tenant_runtime_policy_versions;
CREATE TRIGGER update_tenant_runtime_policy_versions_updated_at
    BEFORE UPDATE ON tenant_runtime_policy_versions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tenant_runtime_policy_events_updated_at ON tenant_runtime_policy_events;
CREATE TRIGGER update_tenant_runtime_policy_events_updated_at
    BEFORE UPDATE ON tenant_runtime_policy_events
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

