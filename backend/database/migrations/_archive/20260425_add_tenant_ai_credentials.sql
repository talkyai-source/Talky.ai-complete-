-- =============================================================================
-- Migration: tenant_ai_credentials
-- Date:      2026-04-25
-- Purpose:   T1.1 — per-tenant encrypted AI-provider API keys.
--
-- Today every tenant shares the same GROQ_API_KEY / DEEPGRAM_API_KEY /
-- CARTESIA_API_KEY / ELEVENLABS_API_KEY / GEMINI_API_KEY from the process
-- environment. That means:
--   - One noisy tenant drains everyone's provider rate limits.
--   - Per-tenant billing and cost-attribution are impossible.
--   - There's no way to honour a tenant's own provider contract.
--
-- This table lets each tenant bring their own API keys. A per-tenant row
-- takes precedence; when no row exists, the CredentialResolver falls back
-- to the env var so existing single-tenant deploys keep working.
--
-- Encryption: the `encrypted_key` column holds an envelope-encrypted
-- ciphertext produced by TokenEncryptionService (the same service that
-- handles SIP trunk passwords at telephony_sip.py:33). Plaintext never
-- touches the DB.
--
-- Idempotent: safe to re-run.
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_ai_credentials (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Provider identifier — matches the factory keys used by
    -- LLMFactory / TTSFactory / STT. Free-text (not an enum) so adding a
    -- new provider doesn't require a migration.
    provider         TEXT NOT NULL CHECK (char_length(provider) > 0 AND char_length(provider) <= 64),

    -- Kind of credential. Today every provider is a single API key but
    -- this allows future "oauth_refresh_token", "service_account_json",
    -- etc. without another migration.
    credential_kind  TEXT NOT NULL DEFAULT 'api_key'
                     CHECK (credential_kind IN ('api_key', 'oauth_refresh_token', 'service_account_json')),

    -- Envelope-encrypted payload. TokenEncryptionService handles the key
    -- material; we just store the ciphertext blob.
    encrypted_key    TEXT NOT NULL,

    -- Last four characters of the plaintext, stored for UI display
    -- ("••••8f3a") so operators can tell keys apart without decryption.
    -- Optional — the server never relies on this value for auth.
    last4            TEXT CHECK (last4 IS NULL OR char_length(last4) <= 8),

    -- Optional label / notes the tenant set.
    label            TEXT,

    -- Lifecycle. `active` keys are resolved; `disabled` keys are kept for
    -- audit but are ignored by the resolver.
    status           TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'disabled')),

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at     TIMESTAMPTZ,
    rotated_at       TIMESTAMPTZ,

    -- Only one ACTIVE credential per (tenant, provider, kind). A
    -- disabled key with the same key does not collide so you can rotate
    -- by flipping the old to `disabled` and inserting the new.
    CONSTRAINT tenant_ai_credentials_unique_active
        UNIQUE NULLS NOT DISTINCT (tenant_id, provider, credential_kind, status)
);

-- Fast resolver path — every outbound call touches this table.
CREATE INDEX IF NOT EXISTS idx_tenant_ai_credentials_lookup
    ON tenant_ai_credentials (tenant_id, provider, credential_kind)
    WHERE status = 'active';

-- RLS: tenant sees only its own rows. Admins get cross-tenant access
-- through service-role connections that bypass this policy.
ALTER TABLE tenant_ai_credentials ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_ai_credentials_isolation ON tenant_ai_credentials;
CREATE POLICY tenant_ai_credentials_isolation ON tenant_ai_credentials
    FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);

-- updated_at housekeeping — reuse the trigger-function pattern from the
-- other T0.* migrations.
CREATE OR REPLACE FUNCTION tenant_ai_credentials_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tenant_ai_credentials_updated_at ON tenant_ai_credentials;
CREATE TRIGGER trg_tenant_ai_credentials_updated_at
    BEFORE UPDATE ON tenant_ai_credentials
    FOR EACH ROW
    EXECUTE FUNCTION tenant_ai_credentials_touch_updated_at();

COMMENT ON TABLE tenant_ai_credentials IS
    'Per-tenant AI provider API keys (encrypted). Consulted by '
    'CredentialResolver; env vars are the fallback when no row exists.';

COMMENT ON COLUMN tenant_ai_credentials.encrypted_key IS
    'Envelope-encrypted ciphertext from TokenEncryptionService. '
    'Plaintext never lands in the DB.';
