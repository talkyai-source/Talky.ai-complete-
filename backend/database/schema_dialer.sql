-- ============================================
-- Dialer Engine Schema Updates
-- Run this in Supabase SQL Editor
-- ============================================

-- ============================================
-- 1. ADD PRIORITY FIELDS TO LEADS TABLE
-- ============================================
ALTER TABLE leads ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 5;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_high_value BOOLEAN DEFAULT false;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';

-- Add constraint for priority range
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_priority_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_priority_check 
            CHECK (priority >= 1 AND priority <= 10);
    END IF;
END $$;

-- Index for priority-based ordering
CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(priority DESC, created_at);

-- ============================================
-- 2. ADD CALLING_RULES TO TENANTS TABLE
-- ============================================
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS calling_rules JSONB DEFAULT '{
    "time_window_start": "09:00",
    "time_window_end": "19:00",
    "timezone": "America/New_York",
    "allowed_days": [0, 1, 2, 3, 4],
    "max_concurrent_calls": 10,
    "retry_delay_seconds": 7200,
    "max_retry_attempts": 3,
    "enable_priority_override": true,
    "high_priority_threshold": 8,
    "skip_dnc": true,
    "min_hours_between_calls": 2
}';

-- ============================================
-- 3. CREATE DIALER_JOBS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS dialer_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    
    -- Priority (1-10, higher = more urgent)
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    
    -- Status tracking
    status VARCHAR(50) DEFAULT 'pending',
    attempt_number INTEGER DEFAULT 1,
    
    -- Timing
    scheduled_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processed_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    
    -- Result tracking
    last_outcome VARCHAR(50),
    last_error TEXT,
    call_id UUID REFERENCES calls(id),
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================
-- 4. DIALER_JOBS INDEXES
-- ============================================
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_tenant ON dialer_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_campaign ON dialer_jobs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_lead ON dialer_jobs(lead_id);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_status ON dialer_jobs(status);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_scheduled ON dialer_jobs(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_priority ON dialer_jobs(priority DESC, created_at);

-- Composite index for queue queries
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_queue ON dialer_jobs(tenant_id, status, priority DESC, scheduled_at);

-- ============================================
-- 5. ADD OUTCOME FIELDS TO CALLS TABLE
-- ============================================
ALTER TABLE calls ADD COLUMN IF NOT EXISTS outcome VARCHAR(50);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS goal_achieved BOOLEAN DEFAULT false;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS dialer_job_id UUID REFERENCES dialer_jobs(id);

-- ============================================
-- 6. TRIGGER FOR UPDATED_AT
-- ============================================
CREATE OR REPLACE FUNCTION update_dialer_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_dialer_jobs_updated_at ON dialer_jobs;
CREATE TRIGGER trigger_dialer_jobs_updated_at
    BEFORE UPDATE ON dialer_jobs
    FOR EACH ROW
    EXECUTE FUNCTION update_dialer_jobs_updated_at();

-- ============================================
-- 7. CAMPAIGN CALLING CONFIG
-- ============================================
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS calling_config JSONB DEFAULT '{
    "caller_id": null,
    "priority_override": null,
    "retry_on_no_answer": true,
    "retry_on_busy": true
}';

-- ============================================
-- 8. ADD PRIORITY TAGS SUPPORT
-- ============================================
COMMENT ON COLUMN leads.priority IS '1-10 priority level. 8+ goes to priority queue.';
COMMENT ON COLUMN leads.is_high_value IS 'VIP flag - adds +2 to priority';
COMMENT ON COLUMN leads.tags IS 'Tags like urgent, appointment, reminder - add +1 to priority';
COMMENT ON COLUMN dialer_jobs.status IS 'pending, processing, completed, failed, retry_scheduled, skipped, goal_achieved, non_retryable';
COMMENT ON COLUMN dialer_jobs.last_outcome IS 'answered, no_answer, busy, failed, spam, invalid, unavailable, goal_achieved';
