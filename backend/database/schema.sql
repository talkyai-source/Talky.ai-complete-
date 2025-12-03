-- ============================================
-- AI Voice Dialer - Database Schema
-- Run this in Supabase SQL Editor
-- ============================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 1. CAMPAIGNS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- MULTI-TENANT: Uncomment when enabling multi-tenancy
    -- tenant_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    system_prompt TEXT NOT NULL,
    voice_id VARCHAR(100) NOT NULL,
    max_concurrent_calls INTEGER DEFAULT 10,
    retry_failed BOOLEAN DEFAULT true,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    total_leads INTEGER DEFAULT 0,
    calls_completed INTEGER DEFAULT 0,
    calls_failed INTEGER DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_created_at ON campaigns(created_at);
-- MULTI-TENANT: Uncomment when enabling multi-tenancy
-- CREATE INDEX IF NOT EXISTS idx_campaigns_tenant_id ON campaigns(tenant_id);

-- ============================================
-- 2. LEADS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- MULTI-TENANT: Uncomment when enabling multi-tenancy
    -- tenant_id VARCHAR(255) NOT NULL,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(255),
    custom_fields JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_called_at TIMESTAMP WITH TIME ZONE,
    call_attempts INTEGER DEFAULT 0,
    status VARCHAR(50) DEFAULT 'pending',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_leads_campaign_id ON leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_phone_number ON leads(phone_number);
-- MULTI-TENANT: Uncomment when enabling multi-tenancy
-- CREATE INDEX IF NOT EXISTS idx_leads_tenant_id ON leads(tenant_id);

-- ============================================
-- 3. CALLS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS calls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- MULTI-TENANT: Uncomment when enabling multi-tenancy
    -- tenant_id VARCHAR(255) NOT NULL,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'initiated',
    started_at TIMESTAMP WITH TIME ZONE,
    answered_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    recording_url TEXT,
    transcript TEXT,
    summary TEXT,
    cost DECIMAL(10, 4),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_calls_campaign_id ON calls(campaign_id);
CREATE INDEX IF NOT EXISTS idx_calls_lead_id ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at);
-- MULTI-TENANT: Uncomment when enabling multi-tenancy
-- CREATE INDEX IF NOT EXISTS idx_calls_tenant_id ON calls(tenant_id);

-- ============================================
-- 4. CONVERSATIONS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- MULTI-TENANT: Uncomment when enabling multi-tenancy
    -- tenant_id VARCHAR(255) NOT NULL,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    messages JSONB DEFAULT '[]',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ended_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_conversations_call_id ON conversations(call_id);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);
-- MULTI-TENANT: Uncomment when enabling multi-tenancy
-- CREATE INDEX IF NOT EXISTS idx_conversations_tenant_id ON conversations(tenant_id);

-- ============================================
-- 5. AUTO-UPDATE TIMESTAMPS
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply triggers
CREATE TRIGGER update_campaigns_updated_at BEFORE UPDATE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_leads_updated_at BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_calls_updated_at BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 6. INSERT DUMMY DATA FOR TESTING
-- ============================================

-- Insert test campaign
INSERT INTO campaigns (
    id,
    name,
    description,
    status,
    system_prompt,
    voice_id,
    max_concurrent_calls
) VALUES (
    '11111111-1111-1111-1111-111111111111',
    'Test Sales Campaign',
    'Automated sales outreach for product demo',
    'draft',
    'You are a friendly sales representative. Introduce our product and schedule demos.',
    'voice-professional-male',
    5
) ON CONFLICT (id) DO NOTHING;

-- Insert test leads
INSERT INTO leads (
    id,
    campaign_id,
    phone_number,
    first_name,
    last_name,
    email,
    status
) VALUES 
(
    '22222222-2222-2222-2222-222222222222',
    '11111111-1111-1111-1111-111111111111',
    '+1234567890',
    'John',
    'Doe',
    'john.doe@example.com',
    'pending'
),
(
    '33333333-3333-3333-3333-333333333333',
    '11111111-1111-1111-1111-111111111111',
    '+1987654321',
    'Jane',
    'Smith',
    'jane.smith@example.com',
    'pending'
) ON CONFLICT (id) DO NOTHING;

-- Insert test call
INSERT INTO calls (
    id,
    campaign_id,
    lead_id,
    phone_number,
    status,
    started_at,
    answered_at,
    ended_at,
    duration_seconds,
    transcript,
    summary,
    cost
) VALUES (
    '44444444-4444-4444-4444-444444444444',
    '11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222',
    '+1234567890',
    'completed',
    NOW() - INTERVAL '1 hour',
    NOW() - INTERVAL '59 minutes',
    NOW() - INTERVAL '54 minutes',
    300,
    'Agent: Hello, this is calling about our product demo. Customer: Yes, I am interested. Agent: Great! Let me schedule that for you.',
    'Customer expressed interest in product demo. Scheduled for next week.',
    0.15
) ON CONFLICT (id) DO NOTHING;

-- Success notification
DO $$
BEGIN
    RAISE NOTICE 'Database schema created successfully';
    RAISE NOTICE 'Dummy data inserted';
    RAISE NOTICE 'Tables created: campaigns, leads, calls, conversations';
    RAISE NOTICE 'Check the tables in Supabase Table Editor';
END $$;
