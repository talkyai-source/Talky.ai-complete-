-- ============================================
-- AI Voice Dialer - Schema Update
-- ADDITIVE MIGRATION - Does NOT modify existing tables
-- Run this in Supabase SQL Editor AFTER the original schema.sql
-- ============================================

-- This migration adds:
-- 1. plans (pricing packages)
-- 2. tenants (organizations/businesses)
-- 3. user_profiles (extends Supabase auth.users)
-- 4. recordings (call recordings)
-- 5. clients (contacts/clients for outreach)
-- 6. Adds 'outcome' column to existing calls table

-- ============================================
-- IMPACT ANALYSIS:
-- - campaigns: NO CHANGE
-- - leads: NO CHANGE  
-- - calls: ADDS 1 optional column (outcome)
-- - conversations: NO CHANGE
-- ============================================

-- Enable UUID extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 1. PLANS TABLE (Pricing Packages)
-- ============================================
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================
-- 2. TENANTS TABLE (Organizations)
-- ============================================
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_name VARCHAR(255) NOT NULL,
    plan_id VARCHAR(50) REFERENCES plans(id),
    minutes_allocated INTEGER NOT NULL DEFAULT 0,
    minutes_used INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenants_plan_id ON tenants(plan_id);

-- ============================================
-- 3. USER_PROFILES TABLE (Extends Supabase auth.users)
-- This table stores additional user info linked to Supabase Auth
-- ============================================
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    tenant_id UUID REFERENCES tenants(id),
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_tenant_id ON user_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles(email);

-- ============================================
-- 4. RECORDINGS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS recordings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    mime_type VARCHAR(50) DEFAULT 'audio/wav',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recordings_call_id ON recordings(call_id);
CREATE INDEX IF NOT EXISTS idx_recordings_created_at ON recordings(created_at);

-- ============================================
-- 5. CLIENTS TABLE (Contacts for outreach)
-- ============================================
CREATE TABLE IF NOT EXISTS clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    phone VARCHAR(20),
    email VARCHAR(255),
    tags JSONB DEFAULT '[]',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clients_tenant_id ON clients(tenant_id);
CREATE INDEX IF NOT EXISTS idx_clients_email ON clients(email);
CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients(phone);

-- ============================================
-- 6. ADD OUTCOME COLUMN TO CALLS (if not exists)
-- This is a non-breaking change - adds optional column
-- ============================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'calls' AND column_name = 'outcome'
    ) THEN
        ALTER TABLE calls ADD COLUMN outcome VARCHAR(100);
    END IF;
END $$;

-- ============================================
-- 7. AUTO-UPDATE TIMESTAMPS FOR NEW TABLES
-- ============================================
CREATE TRIGGER update_plans_updated_at BEFORE UPDATE ON plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tenants_updated_at BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_user_profiles_updated_at BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_clients_updated_at BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 8. INSERT DEFAULT PLANS
-- ============================================
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

-- ============================================
-- SUCCESS NOTIFICATION
-- ============================================
DO $$
BEGIN
    RAISE NOTICE '============================================';
    RAISE NOTICE 'Schema update completed successfully!';
    RAISE NOTICE 'New tables created: plans, tenants, user_profiles, recordings, clients';
    RAISE NOTICE 'Modified tables: calls (added outcome column)';
    RAISE NOTICE 'Existing tables UNCHANGED: campaigns, leads, conversations';
    RAISE NOTICE '============================================';
END $$;
