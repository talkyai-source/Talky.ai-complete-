-- =============================================================================
-- Day 1: Voice Contract — Lock the Contract & Call Logging
-- =============================================================================
--
-- Adds:
--   1. talklee_call_id column to existing calls table (nullable, unique)
--   2. call_legs table — models individual legs of a call
--   3. call_events table — append-only event log for end-to-end tracing
--
-- Safety: All changes are additive (new columns are nullable, new tables).
--         No existing columns or constraints are modified.
--
-- Run: psql $DATABASE_URL -f database/migrations/add_voice_contract.sql
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. ADD talklee_call_id TO EXISTING CALLS TABLE
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS talklee_call_id VARCHAR(20) UNIQUE;

COMMENT ON COLUMN calls.talklee_call_id
    IS 'Human-friendly call identifier (format: tlk_<12hex>). '
       'Cross-system tracing key introduced in Day 1.';

CREATE INDEX IF NOT EXISTS idx_calls_talklee_id
    ON calls(talklee_call_id)
    WHERE talklee_call_id IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. CALL_LEGS TABLE
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS call_legs (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id          UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    talklee_call_id  VARCHAR(20),

    -- Leg identity
    leg_type         VARCHAR(30) NOT NULL,           -- pstn_outbound, websocket, sip, browser …
    direction        VARCHAR(10) NOT NULL DEFAULT 'outbound',  -- inbound | outbound
    provider         VARCHAR(30) NOT NULL DEFAULT 'vonage',    -- vonage, freeswitch, sip, browser, simulation
    provider_leg_id  VARCHAR(100),                   -- External provider leg UUID

    -- Endpoints
    from_number      VARCHAR(20),
    to_number        VARCHAR(20),

    -- Status tracking
    status           VARCHAR(30) NOT NULL DEFAULT 'initiated',

    -- Timing
    started_at       TIMESTAMPTZ,
    answered_at      TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    duration_seconds INTEGER,

    -- Flexible metadata
    metadata         JSONB DEFAULT '{}',

    -- Housekeeping
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_call_legs_call_id
    ON call_legs(call_id);

CREATE INDEX IF NOT EXISTS idx_call_legs_talklee_id
    ON call_legs(talklee_call_id)
    WHERE talklee_call_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_call_legs_provider_leg_id
    ON call_legs(provider_leg_id)
    WHERE provider_leg_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_call_legs_status
    ON call_legs(status);

-- Auto-update trigger
DROP TRIGGER IF EXISTS update_call_legs_updated_at ON call_legs;
CREATE TRIGGER update_call_legs_updated_at
    BEFORE UPDATE ON call_legs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. CALL_EVENTS TABLE (append-only)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS call_events (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id          UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    talklee_call_id  VARCHAR(20),
    leg_id           UUID REFERENCES call_legs(id) ON DELETE SET NULL,

    -- Event payload
    event_type       VARCHAR(30) NOT NULL,            -- state_change, leg_started, webhook_received …
    previous_state   VARCHAR(30),                     -- e.g. 'ringing'
    new_state        VARCHAR(30),                     -- e.g. 'answered'
    event_data       JSONB DEFAULT '{}',              -- Flexible payload
    source           VARCHAR(50) NOT NULL DEFAULT 'system',  -- vonage_webhook, call_service, websocket …

    -- Timestamp (immutable — no updated_at for append-only table)
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_call_events_call_id
    ON call_events(call_id);

CREATE INDEX IF NOT EXISTS idx_call_events_talklee_id
    ON call_events(talklee_call_id)
    WHERE talklee_call_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_call_events_event_type
    ON call_events(event_type);

CREATE INDEX IF NOT EXISTS idx_call_events_created_at
    ON call_events(created_at);

-- Composite index for time-range queries by call
CREATE INDEX IF NOT EXISTS idx_call_events_call_time
    ON call_events(call_id, created_at);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. ROW LEVEL SECURITY
-- ─────────────────────────────────────────────────────────────────────────────

-- call_legs: inherit tenant isolation via joins to calls
ALTER TABLE call_legs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can manage all call_legs" ON call_legs;
CREATE POLICY "Service role can manage all call_legs" ON call_legs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "Users can view call_legs in their tenant" ON call_legs;
CREATE POLICY "Users can view call_legs in their tenant" ON call_legs
    FOR SELECT USING (
        call_id IN (
            SELECT id FROM calls
            WHERE tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
        )
    );

-- call_events: inherit tenant isolation via joins to calls
ALTER TABLE call_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can manage all call_events" ON call_events;
CREATE POLICY "Service role can manage all call_events" ON call_events
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "Users can view call_events in their tenant" ON call_events;
CREATE POLICY "Users can view call_events in their tenant" ON call_events
    FOR SELECT USING (
        call_id IN (
            SELECT id FROM calls
            WHERE tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
        )
    );


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. TABLE COMMENTS
-- ─────────────────────────────────────────────────────────────────────────────

COMMENT ON TABLE call_legs IS
    'Individual legs of a call (PSTN, WebSocket, SIP, Browser). '
    'Introduced Day 1 for multi-leg traceability.';

COMMENT ON TABLE call_events IS
    'Append-only event log for call state transitions and pipeline events. '
    'Introduced Day 1 for end-to-end call tracing.';


-- ─────────────────────────────────────────────────────────────────────────────
-- SUCCESS
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    RAISE NOTICE '=== Day 1: Voice Contract migration applied successfully ===';
    RAISE NOTICE 'Added: calls.talklee_call_id column';
    RAISE NOTICE 'Created: call_legs table';
    RAISE NOTICE 'Created: call_events table';
END $$;
