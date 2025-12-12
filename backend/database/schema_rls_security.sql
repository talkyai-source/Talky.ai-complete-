-- ============================================
-- SUPABASE SECURITY FIX
-- Enables RLS + Multi-tenant Policies
-- Run after: schema_multi_tenant.sql
-- ============================================

-- 1. Enable RLS on all tables
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE dialer_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE recordings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

-- ============================================
-- 2. PLANS TABLE POLICIES (Public read, admin write)
-- ============================================
CREATE POLICY "Plans are publicly readable" ON plans
    FOR SELECT USING (true);

CREATE POLICY "Service role can manage plans" ON plans
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 3. TENANTS TABLE POLICIES
-- ============================================
CREATE POLICY "Users can view their own tenant" ON tenants
    FOR SELECT USING (
        id::text = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Service role can manage tenants" ON tenants
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 4. USER_PROFILES TABLE POLICIES
-- ============================================
CREATE POLICY "Users can view own profile" ON user_profiles
    FOR SELECT USING (id = auth.uid());

CREATE POLICY "Users can update own profile" ON user_profiles
    FOR UPDATE USING (id = auth.uid());

CREATE POLICY "Service role can manage profiles" ON user_profiles
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 5. CAMPAIGNS TABLE POLICIES (Multi-tenant)
-- ============================================
CREATE POLICY "Users can view campaigns in their tenant" ON campaigns
    FOR SELECT USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Users can manage campaigns in their tenant" ON campaigns
    FOR ALL USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Service role can manage all campaigns" ON campaigns
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 6. LEADS TABLE POLICIES (Multi-tenant)
-- ============================================
CREATE POLICY "Users can view leads in their tenant" ON leads
    FOR SELECT USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Users can manage leads in their tenant" ON leads
    FOR ALL USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Service role can manage all leads" ON leads
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 7. CALLS TABLE POLICIES (Multi-tenant)
-- ============================================
CREATE POLICY "Users can view calls in their tenant" ON calls
    FOR SELECT USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Users can manage calls in their tenant" ON calls
    FOR ALL USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Service role can manage all calls" ON calls
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 8. CONVERSATIONS TABLE POLICIES (Multi-tenant)
-- ============================================
CREATE POLICY "Users can view conversations in their tenant" ON conversations
    FOR SELECT USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

CREATE POLICY "Service role can manage all conversations" ON conversations
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 9. DIALER_JOBS TABLE POLICIES (Multi-tenant)
-- ============================================
CREATE POLICY "Users can view dialer_jobs in their tenant" ON dialer_jobs
    FOR SELECT USING (
        tenant_id = (
            SELECT tenant_id FROM user_profiles 
            WHERE id = auth.uid()
        )::uuid
    );

CREATE POLICY "Service role can manage all dialer_jobs" ON dialer_jobs
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 10. RECORDINGS TABLE POLICIES
-- ============================================
CREATE POLICY "Users can view recordings in their tenant" ON recordings
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM calls c 
            WHERE c.id = recordings.call_id 
            AND c.tenant_id = (
                SELECT tenant_id FROM user_profiles 
                WHERE id = auth.uid()
            )
        )
    );

CREATE POLICY "Service role can manage all recordings" ON recordings
    FOR ALL USING (auth.role() = 'service_role');

-- ============================================
-- 11. FIX FUNCTION SEARCH PATH
-- ============================================
ALTER FUNCTION update_updated_at_column() SET search_path = public;
ALTER FUNCTION update_dialer_jobs_updated_at() SET search_path = public;

-- ============================================
-- Success message
-- ============================================
DO $$
BEGIN
    RAISE NOTICE 'Security policies applied successfully!';
    RAISE NOTICE 'RLS enabled on all tables';
    RAISE NOTICE 'Multi-tenant policies created';
    RAISE NOTICE '';
    RAISE NOTICE 'IMPORTANT: Enable Leaked Password Protection in Supabase Dashboard:';
    RAISE NOTICE 'Authentication > Settings > Enable leaked password protection';
END $$;
