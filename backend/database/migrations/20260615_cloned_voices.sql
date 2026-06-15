-- 20260615_cloned_voices.sql
--
-- Per-tenant voice clones (ElevenLabs Instant Voice Cloning).
--
-- A cloned voice lives in the single shared ElevenLabs account (one
-- ELEVENLABS_API_KEY), so its voice_id is globally visible via the EL
-- "voices for current key" listing. This table is the OWNERSHIP record:
-- it maps each platform-created clone to the tenant that made it, so the
-- voice catalog can show a tenant only ITS OWN clones (plus the shared
-- library voices) and hide other tenants' clones.
--
-- consent_at is the user's attestation that they had the right to clone
-- the supplied voice (ElevenLabs ToS requirement). Never null.
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS cloned_voices (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL,
    voice_id     text NOT NULL,                       -- ElevenLabs voice_id
    name         text NOT NULL,
    provider     text NOT NULL DEFAULT 'elevenlabs',
    created_by   text,                                -- user email / id
    consent_at   timestamptz NOT NULL,                -- rights attestation
    status       text NOT NULL DEFAULT 'ready',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    -- One row per EL voice_id; a clone belongs to exactly one tenant.
    CONSTRAINT uq_cloned_voices_voice_id UNIQUE (voice_id)
);

CREATE INDEX IF NOT EXISTS idx_cloned_voices_tenant
    ON cloned_voices (tenant_id);

COMMIT;
