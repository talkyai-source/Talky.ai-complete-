-- Phase 4.2 — Per-provider cost ledger.
--
-- Records every chargeable provider event (LLM token batch, TTS
-- character batch, STT-second batch) so 1000-concurrent traffic
-- survives budget review. Granularity is per-call, per-provider,
-- per-event so we can attribute cost spikes to individual campaigns
-- or pods without aggregation guesswork.
--
-- Why a separate ledger (not just rolling-up call_cost on the calls
-- table): a single call has 5–30 LLM streams + 10–50 TTS sentences +
-- 1 STT session, each potentially on a different key from the pool
-- with different unit pricing. Rolling up loses the per-key
-- attribution that we need to prove "key 3 cost 10× more this week
-- because it was the failover for keys 1+2".
--
-- Tenant-scoped with RLS, indexed for the dashboards that aggregate
-- by tenant_id + provider + day.

BEGIN;

CREATE TABLE IF NOT EXISTS tenant_provider_cost_events (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    -- Optional FK-by-id to calls.id; NULL for non-call events
    -- (catalog refresh, voice list fetch, etc.)
    call_id         UUID,

    -- Provider classification.
    provider        TEXT NOT NULL,         -- "groq" | "elevenlabs" | "deepgram" | "cartesia" | "google_tts" | "openai" | "assemblyai"
    provider_role   TEXT NOT NULL,         -- "llm" | "tts" | "stt"
    -- Redacted key fingerprint (first/last 4 chars) so we can attribute
    -- cost per pool entry without ever logging the key itself.
    api_key_fp      TEXT,

    -- The unit being billed: "tokens_in" / "tokens_out" / "characters" /
    -- "audio_seconds" / "requests" / "minutes". Free-form so each
    -- provider records what its bill itemises.
    unit            TEXT NOT NULL,
    quantity        NUMERIC(20, 6) NOT NULL,

    -- Cost computed at write time using the unit_price snapshot below.
    -- Snapshotting unit_price means historical roll-ups stay correct
    -- even after we renegotiate provider pricing.
    unit_price_usd  NUMERIC(20, 10),       -- nullable when not yet known
    cost_usd        NUMERIC(20, 6),

    -- Optional model / voice / endpoint label so dashboards can
    -- segment by sub-product within a provider.
    model           TEXT,                   -- "llama-3.3-70b" / "eleven_flash_v2_5" / "nova-3" / ...
    voice_id        TEXT,                   -- TTS only

    -- Latency snapshot of the call to this provider — useful for the
    -- weekly cost+latency cross-tab dashboard (4.3 chaos suite output).
    latency_ms      INTEGER,
    status          TEXT NOT NULL DEFAULT 'ok',   -- "ok" | "error" | "fallback"

    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Ingestion timestamp; differs from occurred_at when we batch
    -- ingest a previously buffered window after a Redis outage.
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tpce_tenant_occurred
    ON tenant_provider_cost_events (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tpce_provider_occurred
    ON tenant_provider_cost_events (provider, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tpce_call
    ON tenant_provider_cost_events (call_id) WHERE call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tpce_keyfp
    ON tenant_provider_cost_events (provider, api_key_fp);

ALTER TABLE tenant_provider_cost_events ENABLE ROW LEVEL SECURITY;

-- Read policy: tenant sees only their own rows; ops bypasses via the
-- existing app.bypass_rls switch.
DROP POLICY IF EXISTS tpce_tenant_isolation ON tenant_provider_cost_events;
CREATE POLICY tpce_tenant_isolation ON tenant_provider_cost_events
    USING (
        current_setting('app.bypass_rls', true) = 'true'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

-- Append-only insert policy — writes always carry the tenant_id.
DROP POLICY IF EXISTS tpce_tenant_insert ON tenant_provider_cost_events;
CREATE POLICY tpce_tenant_insert ON tenant_provider_cost_events
    FOR INSERT
    WITH CHECK (
        current_setting('app.bypass_rls', true) = 'true'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

COMMIT;
