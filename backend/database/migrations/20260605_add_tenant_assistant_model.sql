-- Per-tenant dashboard-assistant LLM model (Groq). NULL = use the default.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS assistant_model TEXT;
