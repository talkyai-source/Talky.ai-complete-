-- Phase 2 / WS-F
-- Tenant SIP onboarding data model + idempotency ledger.
-- Safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Ensure updated_at trigger function exists without replacing an existing one.
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

CREATE TABLE IF NOT EXISTS tenant_sip_trunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    trunk_name VARCHAR(100) NOT NULL,
    sip_domain VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL DEFAULT 5060 CHECK (port BETWEEN 1 AND 65535),
    transport VARCHAR(8) NOT NULL DEFAULT 'udp' CHECK (transport IN ('udp', 'tcp', 'tls')),
    direction VARCHAR(10) NOT NULL DEFAULT 'both' CHECK (direction IN ('inbound', 'outbound', 'both')),
    auth_username VARCHAR(255),
    auth_password_encrypted TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tenant_sip_trunks_auth_pair
        CHECK (
            (auth_username IS NULL AND auth_password_encrypted IS NULL) OR
            (auth_username IS NOT NULL AND auth_password_encrypted IS NOT NULL)
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_sip_trunks_tenant_name_unique
    ON tenant_sip_trunks(tenant_id, lower(trunk_name));
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_sip_trunks_tenant_id_id_unique
    ON tenant_sip_trunks(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_tenant_sip_trunks_tenant_active
    ON tenant_sip_trunks(tenant_id, is_active);


CREATE TABLE IF NOT EXISTS tenant_codec_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    allowed_codecs TEXT[] NOT NULL DEFAULT ARRAY['PCMU', 'PCMA'],
    preferred_codec VARCHAR(20) NOT NULL DEFAULT 'PCMU',
    sample_rate_hz INTEGER NOT NULL DEFAULT 8000 CHECK (sample_rate_hz IN (8000, 16000, 24000, 48000)),
    ptime_ms INTEGER NOT NULL DEFAULT 20 CHECK (ptime_ms IN (10, 20, 30, 40, 60)),
    max_bitrate_kbps INTEGER CHECK (max_bitrate_kbps IS NULL OR max_bitrate_kbps > 0),
    jitter_buffer_ms INTEGER NOT NULL DEFAULT 60 CHECK (jitter_buffer_ms BETWEEN 0 AND 1000),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tenant_codec_preferred_in_allowed
        CHECK (preferred_codec = ANY (allowed_codecs))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_codec_policies_tenant_name_unique
    ON tenant_codec_policies(tenant_id, lower(policy_name));
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_codec_policies_tenant_id_id_unique
    ON tenant_codec_policies(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_tenant_codec_policies_tenant_active
    ON tenant_codec_policies(tenant_id, is_active);


CREATE TABLE IF NOT EXISTS tenant_route_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    route_type VARCHAR(10) NOT NULL DEFAULT 'outbound' CHECK (route_type IN ('inbound', 'outbound')),
    priority INTEGER NOT NULL DEFAULT 100 CHECK (priority BETWEEN 1 AND 10000),
    match_pattern TEXT NOT NULL,
    target_trunk_id UUID NOT NULL,
    codec_policy_id UUID,
    strip_digits INTEGER NOT NULL DEFAULT 0 CHECK (strip_digits BETWEEN 0 AND 15),
    prepend_digits VARCHAR(20),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_tenant_route_policies_trunk
        FOREIGN KEY (tenant_id, target_trunk_id)
        REFERENCES tenant_sip_trunks(tenant_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_tenant_route_policies_codec
        FOREIGN KEY (tenant_id, codec_policy_id)
        REFERENCES tenant_codec_policies(tenant_id, id)
        ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_route_policies_tenant_name_unique
    ON tenant_route_policies(tenant_id, lower(policy_name));
CREATE INDEX IF NOT EXISTS idx_tenant_route_policies_tenant_route_active_priority
    ON tenant_route_policies(tenant_id, route_type, is_active, priority);


CREATE TABLE IF NOT EXISTS tenant_telephony_idempotency (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    operation VARCHAR(120) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    response_body JSONB,
    status_code INTEGER CHECK (status_code IS NULL OR status_code BETWEEN 100 AND 599),
    resource_type VARCHAR(64),
    resource_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    CONSTRAINT uq_tenant_telephony_idempotency
        UNIQUE (tenant_id, operation, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_idempotency_tenant_created
    ON tenant_telephony_idempotency(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_telephony_idempotency_expires
    ON tenant_telephony_idempotency(expires_at);


DROP TRIGGER IF EXISTS update_tenant_sip_trunks_updated_at ON tenant_sip_trunks;
CREATE TRIGGER update_tenant_sip_trunks_updated_at
    BEFORE UPDATE ON tenant_sip_trunks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tenant_codec_policies_updated_at ON tenant_codec_policies;
CREATE TRIGGER update_tenant_codec_policies_updated_at
    BEFORE UPDATE ON tenant_codec_policies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tenant_route_policies_updated_at ON tenant_route_policies;
CREATE TRIGGER update_tenant_route_policies_updated_at
    BEFORE UPDATE ON tenant_route_policies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
