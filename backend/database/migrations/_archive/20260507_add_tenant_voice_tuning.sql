-- T4-C3: Per-tenant voice-tuning persistence layer.
--
-- Adds a single nullable JSONB column to tenant_ai_configs. The
-- VoiceTuningResolver (app/domain/services/voice_tuning.py) reads the
-- column at every call setup and merges it on top of env defaults
-- and code defaults. Empty JSONB ('{}') means "use env / code
-- defaults" — operators who haven't opted in see zero behaviour
-- change.
--
-- Idempotent (IF NOT EXISTS) so a re-run is a no-op. Default '{}'::jsonb
-- means existing rows pass NOT NULL validation without a backfill
-- step, and SELECT continues to work for the existing endpoint.
--
-- JSON shape mirrors the ``VoiceTuning`` dataclass field names:
--   {
--     "stt_eot_threshold":         0.5 .. 0.9,
--     "stt_eager_eot_threshold":   0.3 .. 0.9 OR null,
--     "stt_eot_timeout_ms":        500 .. 10000,
--     "turn_0_min_confidence":     0.0 .. 1.0,
--     "turn_0_min_alpha_chars":    1 .. 10
--   }
-- All fields are optional; partial dicts merge onto defaults at lookup.
-- Validation happens in code (VoiceTuningResolver.coerce_user_partial)
-- so the column itself stays a permissive JSONB.

ALTER TABLE tenant_ai_configs
    ADD COLUMN IF NOT EXISTS voice_tuning JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Index on tenant_id is already present (UNIQUE constraint from the
-- table definition) so per-tenant lookup is already O(log n). No new
-- index needed.

COMMENT ON COLUMN tenant_ai_configs.voice_tuning IS
    'Per-tenant voice-pipeline tuning (T4-C3). Partial dict; missing '
    'keys fall back to env defaults then code defaults. See '
    'VoiceTuning dataclass in app/domain/services/voice_tuning.py for '
    'the field set.';
