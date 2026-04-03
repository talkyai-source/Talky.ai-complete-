-- =============================================================================
-- Migration: CallGuard Required Tables
-- Date:      2026-04-02
-- Purpose:   Create all missing tables referenced by CallGuard service.
--
-- NOTE: The tenants table is owned by postgres. Column additions to tenants
--       (status, suspended_at, etc.) require a DBA-level migration run as
--       the postgres superuser. This migration only creates NEW tables that
--       the application user (talkyai) can own.
--
-- Tables Created:
--   1. tenant_call_limits     — per-tenant call rate/concurrency configuration
--   2. partner_limits         — partner-level aggregate limits
--   3. dnc_entries            — Do-Not-Call registry
--   4. call_guard_decisions   — audit log of guard decisions
--   5. abuse_events           — abuse detection events
--
-- Idempotent: Safe to re-run (all CREATE IF NOT EXISTS).
-- =============================================================================

-- =============================================================================
-- SECTION 1: tenant_call_limits — per-tenant call guardrails
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_call_limits (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    calls_per_minute          INTEGER NOT NULL DEFAULT 60       CHECK (calls_per_minute > 0),
    calls_per_hour            INTEGER NOT NULL DEFAULT 1000     CHECK (calls_per_hour > 0),
    calls_per_day             INTEGER NOT NULL DEFAULT 10000    CHECK (calls_per_day > 0),
    max_concurrent_calls      INTEGER NOT NULL DEFAULT 10       CHECK (max_concurrent_calls > 0),
    max_queue_size            INTEGER NOT NULL DEFAULT 50       CHECK (max_queue_size >= 0),
    monthly_minutes_allocated INTEGER NOT NULL DEFAULT 0,
    monthly_minutes_used      INTEGER NOT NULL DEFAULT 0,
    monthly_spend_cap         NUMERIC(12, 4),
    monthly_spend_used        NUMERIC(12, 4) NOT NULL DEFAULT 0.0,
    max_call_duration_seconds INTEGER NOT NULL DEFAULT 3600     CHECK (max_call_duration_seconds > 0),
    min_call_interval_seconds INTEGER NOT NULL DEFAULT 300      CHECK (min_call_interval_seconds >= 0),
    allowed_country_codes     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    blocked_country_codes     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    blocked_prefixes          TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    features_enabled          JSONB NOT NULL DEFAULT '{}'::jsonb,
    features_disabled         JSONB NOT NULL DEFAULT '{}'::jsonb,
    respect_business_hours    BOOLEAN NOT NULL DEFAULT FALSE,
    business_hours_start      TIME,
    business_hours_end        TIME,
    business_hours_timezone   VARCHAR(64) NOT NULL DEFAULT 'UTC',
    is_active                 BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_until           TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_call_limits_tenant_active
    ON tenant_call_limits(tenant_id, is_active, effective_from DESC);

-- Seed default limits for every existing tenant that doesn't have one yet.
INSERT INTO tenant_call_limits (tenant_id)
SELECT t.id FROM tenants t
WHERE NOT EXISTS (
    SELECT 1 FROM tenant_call_limits tcl WHERE tcl.tenant_id = t.id
)
ON CONFLICT DO NOTHING;

-- =============================================================================
-- SECTION 2: partner_limits — partner/reseller aggregate limits
-- =============================================================================

CREATE TABLE IF NOT EXISTS partner_limits (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id                  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    max_tenants                 INTEGER NOT NULL DEFAULT 10      CHECK (max_tenants > 0),
    current_tenant_count        INTEGER NOT NULL DEFAULT 0,
    aggregate_calls_per_minute  INTEGER NOT NULL DEFAULT 600     CHECK (aggregate_calls_per_minute > 0),
    aggregate_calls_per_hour    INTEGER NOT NULL DEFAULT 10000   CHECK (aggregate_calls_per_hour > 0),
    aggregate_calls_per_day     INTEGER NOT NULL DEFAULT 100000  CHECK (aggregate_calls_per_day > 0),
    aggregate_concurrent_calls  INTEGER NOT NULL DEFAULT 100     CHECK (aggregate_concurrent_calls > 0),
    revenue_share_percent       NUMERIC(5, 2) NOT NULL DEFAULT 20.0
        CHECK (revenue_share_percent BETWEEN 0 AND 100),
    min_billing_amount          NUMERIC(12, 4) NOT NULL DEFAULT 100.0,
    max_billing_amount          NUMERIC(12, 4),
    feature_whitelist           TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    feature_blacklist           TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    fraud_detection_sensitivity INTEGER NOT NULL DEFAULT 50
        CHECK (fraud_detection_sensitivity BETWEEN 0 AND 100),
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_partner_limits_partner_active
    ON partner_limits(partner_id, is_active);

-- =============================================================================
-- SECTION 3: dnc_entries — Do-Not-Call registry
-- =============================================================================

CREATE TABLE IF NOT EXISTS dnc_entries (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID REFERENCES tenants(id) ON DELETE CASCADE,
    normalized_number VARCHAR(20) NOT NULL,
    source            VARCHAR(50) NOT NULL DEFAULT 'manual',
    reason            TEXT,
    added_by          UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    expires_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dnc_entries_number
    ON dnc_entries(normalized_number);

CREATE INDEX IF NOT EXISTS idx_dnc_entries_expires
    ON dnc_entries(expires_at)
    WHERE expires_at IS NOT NULL;

-- =============================================================================
-- SECTION 4: call_guard_decisions — audit log of guard evaluations
-- =============================================================================

CREATE TABLE IF NOT EXISTS call_guard_decisions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    partner_id           UUID REFERENCES tenants(id) ON DELETE SET NULL,
    call_id              VARCHAR(100),
    phone_number         VARCHAR(20),
    decision             VARCHAR(20) NOT NULL
        CHECK (decision IN ('allow', 'block', 'queue', 'throttle')),
    checks_performed     JSONB NOT NULL DEFAULT '[]'::jsonb,
    failed_checks        JSONB NOT NULL DEFAULT '[]'::jsonb,
    queue_position       INTEGER,
    retry_after_seconds  INTEGER,
    total_latency_ms     INTEGER,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_guard_decisions_tenant_created
    ON call_guard_decisions(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_call_guard_decisions_phone
    ON call_guard_decisions(phone_number, created_at DESC)
    WHERE phone_number IS NOT NULL;

-- =============================================================================
-- SECTION 5: abuse_events — abuse/velocity detection events
-- =============================================================================

CREATE TABLE IF NOT EXISTS abuse_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    event_type      VARCHAR(50) NOT NULL DEFAULT 'velocity_anomaly',
    severity        VARCHAR(20) NOT NULL DEFAULT 'medium'
        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    source          VARCHAR(50) NOT NULL DEFAULT 'system',
    description     TEXT,
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved_at     TIMESTAMPTZ,
    resolved_by     UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    resolution_note TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_abuse_events_tenant_severity
    ON abuse_events(tenant_id, severity, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_abuse_events_unresolved
    ON abuse_events(tenant_id, created_at DESC)
    WHERE resolved_at IS NULL;
