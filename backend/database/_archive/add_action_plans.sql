-- =============================================================================
-- Day 28: AssistantAgentService - Action Plans Table
-- Multi-step workflow orchestration with safety guardrails
-- =============================================================================
--
-- This migration adds the action_plans table for storing and auditing
-- multi-step action workflows executed by the AssistantAgentService.
--
-- Run: psql $DATABASE_URL -f backend/database/migrations/add_action_plans.sql
-- =============================================================================

-- Create action_plans table
CREATE TABLE IF NOT EXISTS action_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID,  -- References assistant_conversations if exists
    user_id UUID REFERENCES user_profiles(id),
    
    -- Intent and context
    intent TEXT NOT NULL,
    context JSONB DEFAULT '{}',
    
    -- Actions (validated against allowlist in application layer)
    -- Each action: {type, parameters, use_result_from?, condition?}
    actions JSONB NOT NULL DEFAULT '[]',
    
    -- Execution state
    status VARCHAR(50) DEFAULT 'pending',  -- pending, running, completed, partially_completed, failed, cancelled
    current_step INTEGER DEFAULT 0,
    step_results JSONB DEFAULT '[]',  -- Array of step execution results
    error TEXT,
    
    -- Timing
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_action_plans_tenant_id 
    ON action_plans(tenant_id);
    
CREATE INDEX IF NOT EXISTS idx_action_plans_status 
    ON action_plans(status);
    
CREATE INDEX IF NOT EXISTS idx_action_plans_conversation_id 
    ON action_plans(conversation_id) 
    WHERE conversation_id IS NOT NULL;
    
CREATE INDEX IF NOT EXISTS idx_action_plans_created_at 
    ON action_plans(created_at DESC);

-- Composite index for tenant + status queries
CREATE INDEX IF NOT EXISTS idx_action_plans_tenant_status 
    ON action_plans(tenant_id, status);

-- Enable Row Level Security
ALTER TABLE action_plans ENABLE ROW LEVEL SECURITY;

-- RLS Policies
-- Users can view action plans in their tenant
DROP POLICY IF EXISTS "Users can view action_plans in their tenant" ON action_plans;
CREATE POLICY "Users can view action_plans in their tenant" ON action_plans
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

-- Service role can manage all action plans (for backend operations)
DROP POLICY IF EXISTS "Service role can manage all action_plans" ON action_plans;
CREATE POLICY "Service role can manage all action_plans" ON action_plans
    FOR ALL USING (auth.role() = 'service_role');

-- Trigger for auto-updating updated_at
DROP TRIGGER IF EXISTS update_action_plans_updated_at ON action_plans;
CREATE TRIGGER update_action_plans_updated_at 
    BEFORE UPDATE ON action_plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Table and column comments
COMMENT ON TABLE action_plans IS 'Multi-step action plans for orchestrated workflows (Day 28)';
COMMENT ON COLUMN action_plans.intent IS 'Natural language description of the workflow intent';
COMMENT ON COLUMN action_plans.context IS 'Context data: lead_id, campaign_id, etc.';
COMMENT ON COLUMN action_plans.actions IS 'JSONB array of action steps: [{type, parameters, use_result_from?, condition?}]';
COMMENT ON COLUMN action_plans.status IS 'pending, running, completed, partially_completed, failed, cancelled';
COMMENT ON COLUMN action_plans.step_results IS 'JSONB array of execution results for each step';
COMMENT ON COLUMN action_plans.current_step IS 'Index of current/last executed step';

-- Success notification
DO $$
BEGIN
    RAISE NOTICE '============================================================================';
    RAISE NOTICE 'Day 28 Migration: action_plans table created successfully!';
    RAISE NOTICE '';
    RAISE NOTICE 'New table: action_plans';
    RAISE NOTICE '  - Multi-step workflow orchestration';
    RAISE NOTICE '  - Result chaining between steps';
    RAISE NOTICE '  - Conditional execution support';
    RAISE NOTICE '  - Full audit trail';
    RAISE NOTICE '';
    RAISE NOTICE 'Indexes created for: tenant_id, status, conversation_id, created_at';
    RAISE NOTICE 'RLS policies enabled for tenant isolation';
    RAISE NOTICE '============================================================================';
END $$;
