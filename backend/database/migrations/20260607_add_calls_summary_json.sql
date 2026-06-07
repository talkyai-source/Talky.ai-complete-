-- Structured AI call summary (see app/domain/services/call_summary). NULL = not yet generated.
ALTER TABLE calls ADD COLUMN IF NOT EXISTS summary_json JSONB;
