-- =============================================================================
-- Day 29: Voice Intent Detection & Assistant Actions
-- =============================================================================
-- Adds columns to calls table for post-call intent tracking and action results
-- Also adds auto_actions_enabled to tenant_settings for permission control

-- Add detected_intents JSONB to store intent detection results
-- Format: [{"intent": "booking_request", "confidence": 0.85, "extracted_data": {...}, ...}]
ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS detected_intents JSONB DEFAULT '[]';

-- Add action_plan_id to link calls to executed action plans
ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS action_plan_id UUID REFERENCES action_plans(id);

-- Add action_results JSONB to store action execution results
-- Format: {"plan_id": "...", "status": "completed", "successful_steps": 3, ...}
ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS action_results JSONB DEFAULT '{}';

-- Add pending_recommendations for surfacing in next interaction
-- Stores user-facing message when action can't be executed
ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS pending_recommendations TEXT;

-- Add auto_actions_enabled to tenant_settings for permission control
-- When true, system can automatically execute actions from detected intents
ALTER TABLE tenant_settings 
ADD COLUMN IF NOT EXISTS auto_actions_enabled BOOLEAN DEFAULT FALSE;

-- Create indexes for common queries

-- Index for querying calls with action plans
CREATE INDEX IF NOT EXISTS idx_calls_action_plan_id 
ON calls(action_plan_id) 
WHERE action_plan_id IS NOT NULL;

-- Index for querying calls with pending recommendations
CREATE INDEX IF NOT EXISTS idx_calls_pending_recommendations 
ON calls(id) 
WHERE pending_recommendations IS NOT NULL;

-- GIN index for querying detected_intents JSONB
CREATE INDEX IF NOT EXISTS idx_calls_detected_intents 
ON calls USING GIN(detected_intents);

-- Add comments for documentation
COMMENT ON COLUMN calls.detected_intents IS 'JSONB array of actionable intents detected from call transcript (Day 29)';
COMMENT ON COLUMN calls.action_plan_id IS 'Reference to action_plans table if auto-action was executed';
COMMENT ON COLUMN calls.action_results IS 'JSONB with execution results: status, steps completed, etc.';
COMMENT ON COLUMN calls.pending_recommendations IS 'User-facing message when action needs API connection or permission';
COMMENT ON COLUMN tenant_settings.auto_actions_enabled IS 'Allow automatic execution of actions from detected call intents';

-- Log migration
DO $$
BEGIN
    RAISE NOTICE 'Day 29: Voice intent detection columns added to calls table';
    RAISE NOTICE 'Added: detected_intents, action_plan_id, action_results, pending_recommendations';
    RAISE NOTICE 'Added: auto_actions_enabled to tenant_settings';
END $$;
