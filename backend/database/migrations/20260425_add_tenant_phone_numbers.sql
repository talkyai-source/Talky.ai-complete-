-- =============================================================================
-- Migration: tenant_phone_numbers (verified DID registry)
-- Date:      2026-04-25
-- Purpose:   T0.1 + T0.5 — ownership-verified Caller IDs per tenant.
--
-- Before this table, `make_call` accepted `caller_id` as a free query param,
-- letting any tenant spoof any number. Carriers reject spoofed ANI, and
-- STIR/SHAKEN attestation fails. This table is the authority on which
-- numbers a tenant is allowed to originate FROM.
--
-- T0.5 note: the `stir_shaken_token` column stores the attestation level /
-- token returned by the upstream provider (Twilio, Telnyx, Bandwidth). A
-- row with status='verified' but stir_shaken_token=NULL is usable for TEST
-- deploys only — the enforcement layer gates prod traffic on both.
--
-- Idempotent: safe to re-run.
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_phone_numbers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Normalized E.164 number. Enforced with a CHECK so garbage can't
    -- sneak in via direct INSERT — prevents the "what does +1-555?-abc mean"
    -- ambiguity downstream.
    e164                TEXT NOT NULL,

    -- Where we got the number from. Free-text on purpose — tracks upstream
    -- (twilio, telnyx, bandwidth, byo_sip, manual_admin). Useful for routing
    -- and for audit when a carrier raises a spoofing complaint.
    provider            TEXT NOT NULL DEFAULT 'manual_admin',

    -- Lifecycle. A number is only dial-able from when status='verified'.
    --   pending_verification → waiting on operator / SMS / carrier callback
    --   verified             → dial-able subject to stir_shaken_token check
    --   suspended            → operator-disabled; retain row for audit
    --   revoked              → tenant no longer owns number (carrier port-out, etc.)
    status              TEXT NOT NULL DEFAULT 'pending_verification'
                        CHECK (status IN ('pending_verification', 'verified', 'suspended', 'revoked')),

    -- How verification happened. Keeps a durable audit trail — you want to
    -- be able to prove, years later, how tenant X was allowed to use +1234.
    verification_method TEXT
                        CHECK (verification_method IN ('sms_code', 'carrier_api', 'manual_admin', 'letter_of_authorization')),

    -- When the verification challenge was issued (for time-boxing) and when
    -- it was completed.
    verification_sent_at TIMESTAMPTZ,
    verified_at         TIMESTAMPTZ,

    -- Audit trail on who did the admin override, if applicable. Free-text —
    -- keep a human name / email / ticket number.
    verified_by         TEXT,

    -- T0.5 — STIR/SHAKEN attestation token from the upstream provider.
    -- NULL means "usable for test deploys only". Prod enforcement layer
    -- refuses to originate if this is NULL and the ENVIRONMENT is production.
    stir_shaken_token   TEXT,

    -- Optional human label for the UI (e.g. "Main outbound line", "Support").
    label               TEXT,

    -- Free-form provider metadata (twilio_sid, telnyx_number_id, ...).
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- A tenant cannot register the same number twice. Different tenants
    -- CAN share a number only if both have independently verified — real
    -- scenario: shared front-desk line across sister businesses. The app
    -- layer enforces "one tenant per number in production" where needed.
    CONSTRAINT tenant_phone_numbers_tenant_e164_unique UNIQUE (tenant_id, e164)
);

-- Fast-path lookup used by every outbound call:
--   SELECT 1 FROM tenant_phone_numbers WHERE tenant_id = $1 AND e164 = $2 AND status = 'verified'
CREATE INDEX IF NOT EXISTS idx_tenant_phone_numbers_tenant_e164
    ON tenant_phone_numbers (tenant_id, e164)
    WHERE status = 'verified';

CREATE INDEX IF NOT EXISTS idx_tenant_phone_numbers_tenant_status
    ON tenant_phone_numbers (tenant_id, status);

-- Row-Level Security: a tenant can only see/modify its own rows. The
-- current_tenant() helper is defined by day4_rbac_tenant_isolation.sql.
ALTER TABLE tenant_phone_numbers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_phone_numbers_isolation ON tenant_phone_numbers;
CREATE POLICY tenant_phone_numbers_isolation ON tenant_phone_numbers
    FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);

-- updated_at housekeeping.
CREATE OR REPLACE FUNCTION tenant_phone_numbers_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tenant_phone_numbers_updated_at ON tenant_phone_numbers;
CREATE TRIGGER trg_tenant_phone_numbers_updated_at
    BEFORE UPDATE ON tenant_phone_numbers
    FOR EACH ROW
    EXECUTE FUNCTION tenant_phone_numbers_touch_updated_at();

COMMENT ON TABLE tenant_phone_numbers IS
    'Verified Caller IDs (DIDs) per tenant. A number must have status=verified '
    'before any outbound call can originate with it as caller_id. See '
    'backend/docs/telephony/production-requirements.md for STIR/SHAKEN rules.';

COMMENT ON COLUMN tenant_phone_numbers.stir_shaken_token IS
    'Attestation token from upstream provider. NULL = test-only. Production '
    'enforcement refuses to originate when NULL.';
