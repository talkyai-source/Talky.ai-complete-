-- Persist the per-tenant Realtime (gpt-realtime-2) pipeline selection.
--
-- Before this, tenant_ai_configs stored only the cascaded LLM/STT/TTS columns,
-- so selecting the Realtime pipeline in AI Options was silently dropped on save
-- (the row has nowhere to hold pipeline_mode / realtime_*), and every reload +
-- every live call fell back to "cascaded". Additive + idempotent.
ALTER TABLE tenant_ai_configs
    ADD COLUMN IF NOT EXISTS pipeline_mode    TEXT NOT NULL DEFAULT 'cascaded',
    ADD COLUMN IF NOT EXISTS realtime_model   TEXT,
    ADD COLUMN IF NOT EXISTS realtime_voice   TEXT,
    ADD COLUMN IF NOT EXISTS realtime_settings JSONB;
