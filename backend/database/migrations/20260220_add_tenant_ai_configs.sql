-- Add persistent tenant-level AI provider configuration storage.
-- Safe to run multiple times.

CREATE TABLE IF NOT EXISTS tenant_ai_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    llm_provider VARCHAR(50) NOT NULL DEFAULT 'groq',
    llm_model TEXT NOT NULL DEFAULT 'llama-3.3-70b-versatile',
    llm_temperature DOUBLE PRECISION NOT NULL DEFAULT 0.6,
    llm_max_tokens INTEGER NOT NULL DEFAULT 150,
    stt_provider VARCHAR(50) NOT NULL DEFAULT 'deepgram',
    stt_model TEXT NOT NULL DEFAULT 'nova-3',
    stt_language VARCHAR(16) NOT NULL DEFAULT 'en',
    tts_provider VARCHAR(50) NOT NULL DEFAULT 'google',
    tts_model TEXT NOT NULL DEFAULT 'Chirp3-HD',
    tts_voice_id TEXT NOT NULL DEFAULT 'en-US-Chirp3-HD-Leda',
    tts_sample_rate INTEGER NOT NULL DEFAULT 24000,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_ai_configs_tenant_id
    ON tenant_ai_configs(tenant_id);
