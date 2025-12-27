-- =============================================================================
-- AI Voice Dialer - Complete Database Schema
-- =============================================================================
--
-- This is the CONSOLIDATED schema file that includes:
--   1. All table definitions with UUID tenant_id (no VARCHAR)
--   2. All indexes for performance
--   3. All trigger functions for auto-update timestamps
--   4. Row Level Security (RLS) with proper UUID comparisons
--   5. Default data (pricing plans)
--
-- IMPORTANT: Run this on a FRESH database or after backing up existing data
-- For migration from existing schema, see tenant_migration_summary.md
--
-- Created: December 2025
-- Project: Talky.ai Voice Dialer
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- SECTION 1: CORE TABLES
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1.1 PLANS TABLE (Pricing Packages - Created first, no dependencies)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plans (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    description TEXT,
    minutes INTEGER NOT NULL,
    agents INTEGER NOT NULL DEFAULT 1,
    concurrent_calls INTEGER NOT NULL DEFAULT 1,
    features JSONB DEFAULT '[]',
    not_included JSONB DEFAULT '[]',
    popular BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 1.2 TENANTS TABLE (Organizations - References plans)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_name VARCHAR(255) NOT NULL,
    plan_id VARCHAR(50) REFERENCES plans(id),
    minutes_allocated INTEGER NOT NULL DEFAULT 0,
    minutes_used INTEGER NOT NULL DEFAULT 0,
    calling_rules JSONB DEFAULT '{
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
    }',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenants_plan_id ON tenants(plan_id);

-- -----------------------------------------------------------------------------
-- 1.3 USER_PROFILES TABLE (Extends Supabase auth.users)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    tenant_id UUID REFERENCES tenants(id),
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_tenant_id ON user_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles(email);

-- -----------------------------------------------------------------------------
-- 1.4 CAMPAIGNS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    system_prompt TEXT NOT NULL,
    voice_id VARCHAR(100) NOT NULL,
    max_concurrent_calls INTEGER DEFAULT 10,
    retry_failed BOOLEAN DEFAULT true,
    max_retries INTEGER DEFAULT 3,
    goal TEXT,
    script_config JSONB DEFAULT '{}',
    calling_config JSONB DEFAULT '{
        "caller_id": null,
        "priority_override": null,
        "retry_on_no_answer": true,
        "retry_on_busy": true
    }',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    total_leads INTEGER DEFAULT 0,
    calls_completed INTEGER DEFAULT 0,
    calls_failed INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_tenant_id ON campaigns(tenant_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_created_at ON campaigns(created_at);

-- -----------------------------------------------------------------------------
-- 1.5 LEADS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(255),
    custom_fields JSONB DEFAULT '{}',
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    is_high_value BOOLEAN DEFAULT false,
    tags TEXT[] DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'pending',
    last_call_result VARCHAR(50) DEFAULT 'pending',
    call_attempts INTEGER DEFAULT 0,
    last_called_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_tenant_id ON leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_leads_campaign_id ON leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_phone_number ON leads(phone_number);
CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_last_call_result ON leads(last_call_result);

-- Unique constraint: prevent duplicate phones within a campaign (excludes deleted)
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_campaign_phone_unique 
ON leads(campaign_id, phone_number) 
WHERE status != 'deleted';

-- -----------------------------------------------------------------------------
-- 1.6 CALLS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    external_call_uuid VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'initiated',
    outcome VARCHAR(100),
    goal_achieved BOOLEAN DEFAULT false,
    started_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    recording_url TEXT,
    transcript TEXT,
    transcript_json JSONB,
    summary TEXT,
    cost DECIMAL(10, 4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_tenant_id ON calls(tenant_id);
CREATE INDEX IF NOT EXISTS idx_calls_campaign_id ON calls(campaign_id);
CREATE INDEX IF NOT EXISTS idx_calls_lead_id ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at);
CREATE INDEX IF NOT EXISTS idx_calls_external_uuid ON calls(external_call_uuid);

-- -----------------------------------------------------------------------------
-- 1.7 CONVERSATIONS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    messages JSONB DEFAULT '[]',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_tenant_id ON conversations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_conversations_call_id ON conversations(call_id);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);

-- -----------------------------------------------------------------------------
-- 1.8 RECORDINGS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recordings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    mime_type VARCHAR(50) DEFAULT 'audio/wav',
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recordings_tenant_id ON recordings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_recordings_call_id ON recordings(call_id);
CREATE INDEX IF NOT EXISTS idx_recordings_created_at ON recordings(created_at);

-- -----------------------------------------------------------------------------
-- 1.9 TRANSCRIPTS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    turns JSONB NOT NULL DEFAULT '[]',
    full_text TEXT,
    word_count INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    user_word_count INTEGER DEFAULT 0,
    assistant_word_count INTEGER DEFAULT 0,
    duration_seconds INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transcripts_tenant_id ON transcripts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_call_id ON transcripts(call_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_created_at ON transcripts(created_at);
CREATE INDEX IF NOT EXISTS idx_transcripts_full_text_search 
ON transcripts USING gin(to_tsvector('english', COALESCE(full_text, '')));

-- -----------------------------------------------------------------------------
-- 1.10 CLIENTS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    phone VARCHAR(20),
    email VARCHAR(255),
    tags JSONB DEFAULT '[]',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clients_tenant_id ON clients(tenant_id);
CREATE INDEX IF NOT EXISTS idx_clients_email ON clients(email);
CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients(phone);

-- -----------------------------------------------------------------------------
-- 1.11 DIALER_JOBS TABLE (Multi-tenant with UUID)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dialer_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    call_id UUID REFERENCES calls(id),
    phone_number VARCHAR(20) NOT NULL,
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    status VARCHAR(50) DEFAULT 'pending',
    attempt_number INTEGER DEFAULT 1,
    scheduled_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_outcome VARCHAR(50),
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dialer_jobs_tenant_id ON dialer_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_campaign_id ON dialer_jobs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_lead_id ON dialer_jobs(lead_id);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_status ON dialer_jobs(status);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_scheduled ON dialer_jobs(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_priority ON dialer_jobs(priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_queue ON dialer_jobs(tenant_id, status, priority DESC, scheduled_at);

-- =============================================================================
-- SECTION 2: TRIGGER FUNCTIONS
-- =============================================================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Set search path for security
ALTER FUNCTION update_updated_at_column() SET search_path = public;

-- Dialer jobs specific trigger function
CREATE OR REPLACE FUNCTION update_dialer_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

ALTER FUNCTION update_dialer_jobs_updated_at() SET search_path = public;

-- =============================================================================
-- SECTION 3: TRIGGERS
-- =============================================================================

-- Apply updated_at triggers to all tables
DROP TRIGGER IF EXISTS update_plans_updated_at ON plans;
CREATE TRIGGER update_plans_updated_at BEFORE UPDATE ON plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tenants_updated_at ON tenants;
CREATE TRIGGER update_tenants_updated_at BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER update_user_profiles_updated_at BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_campaigns_updated_at ON campaigns;
CREATE TRIGGER update_campaigns_updated_at BEFORE UPDATE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_leads_updated_at ON leads;
CREATE TRIGGER update_leads_updated_at BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_calls_updated_at ON calls;
CREATE TRIGGER update_calls_updated_at BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_conversations_updated_at ON conversations;
CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_clients_updated_at ON clients;
CREATE TRIGGER update_clients_updated_at BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_transcripts_updated_at ON transcripts;
CREATE TRIGGER update_transcripts_updated_at BEFORE UPDATE ON transcripts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_dialer_jobs_updated_at ON dialer_jobs;
CREATE TRIGGER trigger_dialer_jobs_updated_at BEFORE UPDATE ON dialer_jobs
    FOR EACH ROW EXECUTE FUNCTION update_dialer_jobs_updated_at();

-- =============================================================================
-- SECTION 4: ROW LEVEL SECURITY (RLS)
-- =============================================================================

-- Enable RLS on all tables
ALTER TABLE plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE recordings ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE dialer_jobs ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------------------
-- 4.1 PLANS POLICIES (Public read, service role write)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Plans are publicly readable" ON plans;
CREATE POLICY "Plans are publicly readable" ON plans
    FOR SELECT USING (true);

DROP POLICY IF EXISTS "Service role can manage plans" ON plans;
CREATE POLICY "Service role can manage plans" ON plans
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.2 TENANTS POLICIES
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view their own tenant" ON tenants;
CREATE POLICY "Users can view their own tenant" ON tenants
    FOR SELECT USING (
        id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage tenants" ON tenants;
CREATE POLICY "Service role can manage tenants" ON tenants
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.3 USER_PROFILES POLICIES
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view own profile" ON user_profiles;
CREATE POLICY "Users can view own profile" ON user_profiles
    FOR SELECT USING (id = auth.uid());

DROP POLICY IF EXISTS "Users can insert own profile" ON user_profiles;
CREATE POLICY "Users can insert own profile" ON user_profiles
    FOR INSERT WITH CHECK (id = auth.uid());

DROP POLICY IF EXISTS "Users can update own profile" ON user_profiles;
CREATE POLICY "Users can update own profile" ON user_profiles
    FOR UPDATE USING (id = auth.uid());

DROP POLICY IF EXISTS "Service role can manage profiles" ON user_profiles;
CREATE POLICY "Service role can manage profiles" ON user_profiles
    FOR ALL USING (auth.role() = 'service_role');


-- -----------------------------------------------------------------------------
-- 4.4 CAMPAIGNS POLICIES (Multi-tenant)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view campaigns in their tenant" ON campaigns;
CREATE POLICY "Users can view campaigns in their tenant" ON campaigns
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage campaigns in their tenant" ON campaigns;
CREATE POLICY "Users can manage campaigns in their tenant" ON campaigns
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all campaigns" ON campaigns;
CREATE POLICY "Service role can manage all campaigns" ON campaigns
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.5 LEADS POLICIES (Multi-tenant)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view leads in their tenant" ON leads;
CREATE POLICY "Users can view leads in their tenant" ON leads
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage leads in their tenant" ON leads;
CREATE POLICY "Users can manage leads in their tenant" ON leads
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all leads" ON leads;
CREATE POLICY "Service role can manage all leads" ON leads
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.6 CALLS POLICIES (Multi-tenant)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view calls in their tenant" ON calls;
CREATE POLICY "Users can view calls in their tenant" ON calls
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage calls in their tenant" ON calls;
CREATE POLICY "Users can manage calls in their tenant" ON calls
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all calls" ON calls;
CREATE POLICY "Service role can manage all calls" ON calls
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.7 CONVERSATIONS POLICIES (Multi-tenant)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view conversations in their tenant" ON conversations;
CREATE POLICY "Users can view conversations in their tenant" ON conversations
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all conversations" ON conversations;
CREATE POLICY "Service role can manage all conversations" ON conversations
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.8 RECORDINGS POLICIES (Multi-tenant via calls relationship)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view recordings in their tenant" ON recordings;
CREATE POLICY "Users can view recordings in their tenant" ON recordings
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all recordings" ON recordings;
CREATE POLICY "Service role can manage all recordings" ON recordings
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.9 TRANSCRIPTS POLICIES (Multi-tenant)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view transcripts in their tenant" ON transcripts;
CREATE POLICY "Users can view transcripts in their tenant" ON transcripts
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all transcripts" ON transcripts;
CREATE POLICY "Service role can manage all transcripts" ON transcripts
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.10 CLIENTS POLICIES (Multi-tenant)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view clients in their tenant" ON clients;
CREATE POLICY "Users can view clients in their tenant" ON clients
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage clients in their tenant" ON clients;
CREATE POLICY "Users can manage clients in their tenant" ON clients
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all clients" ON clients;
CREATE POLICY "Service role can manage all clients" ON clients
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 4.11 DIALER_JOBS POLICIES (Multi-tenant)
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Users can view dialer_jobs in their tenant" ON dialer_jobs;
CREATE POLICY "Users can view dialer_jobs in their tenant" ON dialer_jobs
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all dialer_jobs" ON dialer_jobs;
CREATE POLICY "Service role can manage all dialer_jobs" ON dialer_jobs
    FOR ALL USING (auth.role() = 'service_role');

-- =============================================================================
-- SECTION 5: DEFAULT DATA
-- =============================================================================

-- Insert default pricing plans
INSERT INTO plans (id, name, price, description, minutes, agents, concurrent_calls, features, not_included, popular) VALUES
(
    'basic', 
    'Basic', 
    29, 
    'Perfect for startups and solo entrepreneurs.', 
    300, 
    1, 
    1, 
    '["300 minutes/month", "1 AI agent", "Basic analytics", "Email support"]'::jsonb, 
    '["API access", "Custom voices", "Priority support"]'::jsonb, 
    false
),
(
    'professional', 
    'Professional', 
    79, 
    'Ideal for growing businesses.', 
    1500, 
    3, 
    3,
    '["1500 minutes/month", "3 AI agents", "Advanced analytics", "Priority support", "Custom voices"]'::jsonb,
    '["API access", "White-label"]'::jsonb, 
    true
),
(
    'enterprise', 
    'Enterprise', 
    199, 
    'For large scale operations.', 
    5000, 
    10, 
    10,
    '["5000 minutes/month", "10 AI agents", "Full analytics", "24/7 support", "API access", "White-label"]'::jsonb,
    '[]'::jsonb, 
    false
)
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- SECTION 6: TABLE COMMENTS
-- =============================================================================

COMMENT ON TABLE plans IS 'Pricing plans for the SaaS platform';
COMMENT ON TABLE tenants IS 'Organizations/businesses using the platform';
COMMENT ON TABLE user_profiles IS 'User profiles extending Supabase auth.users';
COMMENT ON TABLE campaigns IS 'Outbound calling campaigns';
COMMENT ON TABLE leads IS 'Contact leads for campaigns';
COMMENT ON TABLE calls IS 'Call records and outcomes';
COMMENT ON TABLE conversations IS 'Conversation transcripts for calls';
COMMENT ON TABLE recordings IS 'Audio recording storage references';
COMMENT ON TABLE transcripts IS 'Detailed turn-by-turn transcript storage';
COMMENT ON TABLE clients IS 'Client/contact management';
COMMENT ON TABLE dialer_jobs IS 'Queue jobs for automated dialing';

COMMENT ON COLUMN leads.priority IS '1-10 priority level. 8+ goes to priority queue.';
COMMENT ON COLUMN leads.is_high_value IS 'VIP flag - adds +2 to priority';
COMMENT ON COLUMN leads.tags IS 'Tags like urgent, appointment, reminder';
COMMENT ON COLUMN dialer_jobs.status IS 'pending, processing, completed, failed, retry_scheduled, skipped, goal_achieved, non_retryable';
COMMENT ON COLUMN dialer_jobs.last_outcome IS 'answered, no_answer, busy, failed, spam, invalid, unavailable, goal_achieved';
COMMENT ON COLUMN campaigns.goal IS 'Campaign objective (e.g., "Book appointment")';
COMMENT ON COLUMN campaigns.script_config IS 'AI agent configuration as JSONB';
COMMENT ON COLUMN calls.external_call_uuid IS 'External provider call UUID for webhook matching';
COMMENT ON COLUMN calls.transcript_json IS 'Structured transcript as JSONB';
COMMENT ON COLUMN recordings.tenant_id IS 'Tenant ID for multi-tenant isolation';
COMMENT ON COLUMN transcripts.turns IS 'JSONB array: [{role, content, timestamp, confidence}]';

-- =============================================================================
-- SUCCESS NOTIFICATION
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=============================================================================';
    RAISE NOTICE 'Schema created successfully!';
    RAISE NOTICE '';
    RAISE NOTICE 'Tables created: plans, tenants, user_profiles, campaigns, leads, calls,';
    RAISE NOTICE '               conversations, recordings, transcripts, clients, dialer_jobs';
    RAISE NOTICE '';
    RAISE NOTICE 'Features enabled:';
    RAISE NOTICE '  - UUID tenant_id on all multi-tenant tables';
    RAISE NOTICE '  - Row Level Security (RLS) with proper UUID comparisons';
    RAISE NOTICE '  - Auto-update triggers for updated_at columns';
    RAISE NOTICE '  - Performance indexes on all key columns';
    RAISE NOTICE '  - Default pricing plans inserted';
    RAISE NOTICE '';
    RAISE NOTICE 'IMPORTANT: Enable "Leaked Password Protection" in Supabase Dashboard:';
    RAISE NOTICE '  Authentication > Settings > Enable leaked password protection';
    RAISE NOTICE '=============================================================================';
END $$;
