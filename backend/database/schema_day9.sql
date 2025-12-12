-- =============================================================================
-- Day 9 Schema Updates: Campaign & Contact Management Enhancement
-- =============================================================================
-- 
-- Purpose: Add fields required for Day 9 implementation
-- Run after: schema.sql, schema_dialer.sql, schema_update.sql
-- 
-- This migration adds:
--   1. goal column to campaigns (campaign objective)
--   2. script_config JSONB to campaigns (AI agent configuration)
--   3. last_call_result column to leads (quick status lookup)
--   4. Indexes for efficient querying
--   5. Unique constraint to prevent duplicate phones within a campaign
--
-- =============================================================================

-- -----------------------------------------------------------------------------
-- CAMPAIGNS TABLE ENHANCEMENTS
-- -----------------------------------------------------------------------------

-- 1. Add 'goal' column to campaigns
-- Purpose: Store the measurable objective of the campaign (separate from description)
-- Example: "Book appointment", "Generate lead", "Collect survey response"
-- This is separate from 'description' which is free-form notes about the campaign
ALTER TABLE campaigns 
ADD COLUMN IF NOT EXISTS goal TEXT;

COMMENT ON COLUMN campaigns.goal IS 'Campaign objective/goal (e.g., "Book appointment", "Generate lead")';


-- 2. Add 'script_config' JSONB column to campaigns
-- Purpose: Store structured AI agent configuration as JSON
-- This holds the AgentConfig structure including:
--   - agent_name: Name the AI uses
--   - goals: Array of goal definitions
--   - rules: Conversation rules and do_not_say_rules
--   - flow: Conversation flow settings (max_turns, objection_attempts)
-- Using JSONB for flexibility and efficient querying
ALTER TABLE campaigns 
ADD COLUMN IF NOT EXISTS script_config JSONB DEFAULT '{}';

COMMENT ON COLUMN campaigns.script_config IS 'AI agent configuration (AgentConfig structure) as JSONB';


-- -----------------------------------------------------------------------------
-- LEADS TABLE ENHANCEMENTS
-- -----------------------------------------------------------------------------

-- 3. Add 'last_call_result' column to leads
-- Purpose: Store the result of the most recent call attempt for quick status lookup
-- This avoids expensive JOINs with the calls table for common queries
-- Values: 'pending', 'answered', 'no_answer', 'busy', 'failed', 'voicemail', 
--         'goal_achieved', 'declined', 'callback_requested'
ALTER TABLE leads 
ADD COLUMN IF NOT EXISTS last_call_result VARCHAR(50) DEFAULT 'pending';

COMMENT ON COLUMN leads.last_call_result IS 'Result of most recent call attempt for quick status lookup';


-- 4. Add index on last_call_result for efficient filtering
-- This enables fast queries like "show me all leads that answered" or "show pending leads"
CREATE INDEX IF NOT EXISTS idx_leads_last_call_result 
ON leads(last_call_result);


-- 5. Add unique constraint for phone number within a campaign
-- Purpose: Prevent duplicate phone numbers within the same campaign
-- The WHERE clause excludes deleted leads so phones can be re-added after deletion
-- This is crucial for CSV upload duplicate detection at the database level
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_campaign_phone_unique 
ON leads(campaign_id, phone_number) 
WHERE status != 'deleted';

COMMENT ON INDEX idx_leads_campaign_phone_unique IS 'Prevents duplicate phone numbers within a campaign (excludes deleted leads)';


-- -----------------------------------------------------------------------------
-- VERIFICATION QUERIES (run these to verify schema updates)
-- -----------------------------------------------------------------------------

-- Check campaigns table has new columns:
-- SELECT column_name, data_type, column_default 
-- FROM information_schema.columns 
-- WHERE table_name = 'campaigns' 
-- AND column_name IN ('goal', 'script_config');

-- Check leads table has new column:
-- SELECT column_name, data_type, column_default 
-- FROM information_schema.columns 
-- WHERE table_name = 'leads' 
-- AND column_name = 'last_call_result';

-- Check indexes exist:
-- SELECT indexname FROM pg_indexes WHERE tablename = 'leads';


-- =============================================================================
-- END OF DAY 9 SCHEMA UPDATES
-- =============================================================================
