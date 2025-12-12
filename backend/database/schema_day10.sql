-- =============================================================================
-- Day 10 Schema Updates: Call Logs, Transcripts & Recording Storage
-- =============================================================================
-- 
-- Purpose: Add fields required for Day 10 implementation
-- Run after: schema.sql, schema_dialer.sql, schema_update.sql, schema_day9.sql
-- 
-- This migration adds:
--   1. external_call_uuid to calls (provider-agnostic tracking: Vonage, Asterisk, etc.)
--   2. transcript_json JSONB to calls (structured transcript storage)
--   3. tenant_id to recordings (multi-tenant isolation)
--   4. transcripts table (turn-by-turn conversation storage)
--
-- =============================================================================

-- -----------------------------------------------------------------------------
-- CALLS TABLE ENHANCEMENTS
-- -----------------------------------------------------------------------------

-- 1. Add 'external_call_uuid' column to calls
-- Purpose: Store the external provider's call UUID for matching webhooks
-- Works with any provider: Vonage, Asterisk, FreeSWITCH, Twilio, etc.
ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS external_call_uuid VARCHAR(100);

CREATE INDEX IF NOT EXISTS idx_calls_external_uuid 
ON calls(external_call_uuid);

COMMENT ON COLUMN calls.external_call_uuid IS 'External provider call UUID (Vonage, Asterisk, etc.) for webhook matching';


-- 2. Add 'transcript_json' JSONB column to calls
-- Purpose: Store structured transcript as JSON array of turns
-- Format: [{"role": "user", "content": "...", "timestamp": "..."}, ...]
-- Complements the existing 'transcript' TEXT field for plain text
ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS transcript_json JSONB;

COMMENT ON COLUMN calls.transcript_json IS 'Structured transcript as JSONB array of {role, content, timestamp} turns';


-- -----------------------------------------------------------------------------
-- RECORDINGS TABLE ENHANCEMENTS
-- -----------------------------------------------------------------------------

-- 3. Add 'tenant_id' column to recordings for multi-tenant isolation
ALTER TABLE recordings 
ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_recordings_tenant 
ON recordings(tenant_id);

COMMENT ON COLUMN recordings.tenant_id IS 'Tenant ID for multi-tenant isolation';


-- 4. Add 'status' column to recordings for upload tracking
ALTER TABLE recordings 
ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'pending';

COMMENT ON COLUMN recordings.status IS 'Upload status: pending, uploading, completed, failed';


-- -----------------------------------------------------------------------------
-- TRANSCRIPTS TABLE (NEW)
-- -----------------------------------------------------------------------------

-- 5. Create transcripts table for detailed turn-by-turn storage
-- Separate from calls.transcript for more detailed analysis and storage
CREATE TABLE IF NOT EXISTS transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- References
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    tenant_id VARCHAR(255),
    
    -- Structured conversation data
    turns JSONB NOT NULL DEFAULT '[]',  -- Array of {role, content, timestamp, confidence}
    
    -- Searchable plain text version
    full_text TEXT,
    
    -- Metrics
    word_count INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    user_word_count INTEGER DEFAULT 0,
    assistant_word_count INTEGER DEFAULT 0,
    
    -- Duration tracking
    duration_seconds INTEGER,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_transcripts_call_id ON transcripts(call_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_tenant ON transcripts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_created_at ON transcripts(created_at);

-- Full text search index on transcript content
CREATE INDEX IF NOT EXISTS idx_transcripts_full_text_search 
ON transcripts USING gin(to_tsvector('english', COALESCE(full_text, '')));

COMMENT ON TABLE transcripts IS 'Detailed turn-by-turn transcript storage for calls';
COMMENT ON COLUMN transcripts.turns IS 'JSONB array: [{role, content, timestamp, confidence}]';


-- -----------------------------------------------------------------------------
-- AUTO-UPDATE TIMESTAMP TRIGGER FOR TRANSCRIPTS
-- -----------------------------------------------------------------------------

-- Apply existing trigger function to transcripts table
DROP TRIGGER IF EXISTS update_transcripts_updated_at ON transcripts;
CREATE TRIGGER update_transcripts_updated_at 
    BEFORE UPDATE ON transcripts
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();


-- -----------------------------------------------------------------------------
-- VERIFICATION QUERIES (run these to verify schema updates)
-- -----------------------------------------------------------------------------

-- Check calls table has new columns:
-- SELECT column_name, data_type 
-- FROM information_schema.columns 
-- WHERE table_name = 'calls' 
-- AND column_name IN ('external_call_uuid', 'transcript_json');

-- Check recordings table has new column:
-- SELECT column_name, data_type 
-- FROM information_schema.columns 
-- WHERE table_name = 'recordings' 
-- AND column_name IN ('tenant_id', 'status');

-- Check transcripts table exists:
-- SELECT * FROM information_schema.tables WHERE table_name = 'transcripts';


-- =============================================================================
-- END OF DAY 10 SCHEMA UPDATES
-- =============================================================================
