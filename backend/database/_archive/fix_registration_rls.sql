-- =============================================================================
-- Fix RLS for Registration Flow
-- =============================================================================
-- Issue: New users cannot register because RLS policy blocks INSERT on tenants
-- The service role key should bypass RLS, but if not configured correctly,
-- we need explicit policies for tenant and profile creation during registration.
--
-- Execute in Supabase SQL Editor: Dashboard → SQL Editor → New Query → Paste → Run
-- =============================================================================

-- -----------------------------------------------------------------------------
-- OPTION 1: Add INSERT policy for tenants (allows authenticated users to create tenants)
-- This is needed if service_role isn't working properly
-- -----------------------------------------------------------------------------

-- Allow service role full access to tenants (should already exist, but recreate to be sure)
DROP POLICY IF EXISTS "Service role can manage tenants" ON tenants;
CREATE POLICY "Service role can manage tenants" ON tenants
    FOR ALL 
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Allow authenticated users to INSERT new tenants (for registration)
-- This is a fallback if service_role key isn't configured
DROP POLICY IF EXISTS "Authenticated users can create tenants" ON tenants;
CREATE POLICY "Authenticated users can create tenants" ON tenants
    FOR INSERT 
    TO authenticated
    WITH CHECK (true);

-- Allow anon users to INSERT for registration flow (before they are authenticated)
DROP POLICY IF EXISTS "Anon can create tenants for registration" ON tenants;
CREATE POLICY "Anon can create tenants for registration" ON tenants
    FOR INSERT 
    TO anon
    WITH CHECK (true);

-- -----------------------------------------------------------------------------
-- OPTION 2: Verify user_profiles INSERT policy exists
-- -----------------------------------------------------------------------------

-- Ensure users can insert their own profile
DROP POLICY IF EXISTS "Users can insert own profile" ON user_profiles;
CREATE POLICY "Users can insert own profile" ON user_profiles
    FOR INSERT 
    WITH CHECK (id = auth.uid());

-- Allow service role full access
DROP POLICY IF EXISTS "Service role can manage profiles" ON user_profiles;
CREATE POLICY "Service role can manage profiles" ON user_profiles
    FOR ALL 
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Allow anon to insert profiles (for registration flow)
DROP POLICY IF EXISTS "Anon can create profiles for registration" ON user_profiles;
CREATE POLICY "Anon can create profiles for registration" ON user_profiles
    FOR INSERT 
    TO anon
    WITH CHECK (true);

-- -----------------------------------------------------------------------------
-- VERIFICATION
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    RAISE NOTICE '=============================================================================';
    RAISE NOTICE 'RLS policies updated for registration flow!';
    RAISE NOTICE '';
    RAISE NOTICE 'Policies added:';
    RAISE NOTICE '  - Service role can manage tenants (FOR ALL)';
    RAISE NOTICE '  - Authenticated users can create tenants (FOR INSERT)';
    RAISE NOTICE '  - Anon can create tenants for registration (FOR INSERT)';
    RAISE NOTICE '  - Users can insert own profile (FOR INSERT)';
    RAISE NOTICE '  - Service role can manage profiles (FOR ALL)';
    RAISE NOTICE '  - Anon can create profiles for registration (FOR INSERT)';
    RAISE NOTICE '';
    RAISE NOTICE 'IMPORTANT: Also ensure SUPABASE_SERVICE_KEY is correctly set in .env';
    RAISE NOTICE '  Get it from: Supabase Dashboard → Settings → API → service_role key';
    RAISE NOTICE '=============================================================================';
END $$;
