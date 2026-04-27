-- =============================================================================
-- Migration: tenant_recording_policy
-- Date:      2026-04-25
-- Purpose:   T0.4 — per-tenant call recording consent policy.
--
-- Today recording_service.py uploads every call to S3 with no consent
-- logic. That is a legal landmine in any two-party-consent jurisdiction
-- (California, Massachusetts, Illinois, Washington, Florida, Maryland,
-- Montana, New Hampshire, Pennsylvania + most of EU under GDPR + UK +
-- Australia). This table lets each tenant declare their recording
-- policy; the RecordingService consults it before creating a recording.
--
-- Idempotent: safe to re-run.
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_recording_policy (
    tenant_id                UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,

    -- 'one_party' = record without explicit consent (legal in most of the
    --   US except the two-party-consent states listed above).
    -- 'two_party' = record only after announcement + implicit or explicit
    --   consent. Also the correct default for GDPR jurisdictions.
    -- 'disabled' = never record, ever. Belt-and-braces opt-out.
    default_consent_mode     TEXT NOT NULL DEFAULT 'two_party'
                             CHECK (default_consent_mode IN ('one_party', 'two_party', 'disabled')),

    -- Spoken announcement played to the callee before pipeline start when
    -- consent_mode='two_party' and the destination country is in the
    -- two_party_country_codes list (or _any_ country if that list is empty).
    -- Short, clear, and in the target language.
    announcement_text        TEXT NOT NULL DEFAULT
        'This call may be recorded for quality and training purposes. Press 9 at any time to opt out of recording.',

    -- DTMF digit the callee presses to opt out of recording during or
    -- right after the announcement. Default '9' — low chance of accidental
    -- press. Null = no DTMF opt-out (not recommended).
    opt_out_dtmf_digit       TEXT CHECK (opt_out_dtmf_digit IS NULL OR opt_out_dtmf_digit ~ '^[0-9*#]$')
                             DEFAULT '9',

    -- ISO-3166 alpha-2 codes of countries treated as two-party-consent.
    -- Empty array = apply two_party behaviour to every destination (safe
    -- default when you don't know who you'll be calling). Populated with
    -- a sensible default list below on INSERT.
    two_party_country_codes  TEXT[] NOT NULL DEFAULT ARRAY[
        'US-CA', 'US-MA', 'US-IL', 'US-WA', 'US-FL', 'US-MD', 'US-MT',
        'US-NH', 'US-PA', 'US-CT', 'US-DE', 'US-MI', 'US-NV', 'US-OR',
        'CA',  -- Canada — two-party under PIPEDA / provincial law
        -- Entire EU treats call recording as personal-data processing
        -- under GDPR; safe default is two-party everywhere.
        'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR',
        'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL',
        'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE',
        'GB', 'AU'
    ]::TEXT[],

    -- S3 retention in days. Plays alongside any S3 lifecycle rule; the
    -- effective retention is min(policy, lifecycle). Per-plan defaults:
    --   30d basic, 90d pro, 365d enterprise — policy can tighten further.
    retention_days           INTEGER NOT NULL DEFAULT 90
                             CHECK (retention_days > 0 AND retention_days <= 3650),

    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION tenant_recording_policy_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tenant_recording_policy_updated_at ON tenant_recording_policy;
CREATE TRIGGER trg_tenant_recording_policy_updated_at
    BEFORE UPDATE ON tenant_recording_policy
    FOR EACH ROW
    EXECUTE FUNCTION tenant_recording_policy_touch_updated_at();

-- RLS: tenant sees only its own row.
ALTER TABLE tenant_recording_policy ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_recording_policy_isolation ON tenant_recording_policy;
CREATE POLICY tenant_recording_policy_isolation ON tenant_recording_policy
    FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);

COMMENT ON TABLE tenant_recording_policy IS
    'Per-tenant recording consent policy. Consulted by RecordingService '
    'before creating any recording. See backend/docs/telephony/'
    'production-requirements.md for jurisdiction guidance.';
