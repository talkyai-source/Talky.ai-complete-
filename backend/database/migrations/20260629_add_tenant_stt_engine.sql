-- Per-tenant STT engine selection (Flux vs Nova-3), surfaced in AI Options.
-- Additive + idempotent. Default 'deepgram_flux' preserves current behaviour
-- (every existing tenant stays on Flux until they opt into Nova-3).
ALTER TABLE tenant_ai_configs
    ADD COLUMN IF NOT EXISTS stt_engine TEXT NOT NULL DEFAULT 'deepgram_flux';
