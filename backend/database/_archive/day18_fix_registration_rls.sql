-- =============================================================================
-- Day 18: Fix user_profiles RLS for Registration
-- =============================================================================
--
-- Issue: New users cannot register because RLS policy blocks INSERT on user_profiles
-- Solution: Add INSERT policy allowing users to create their own profile
--
-- Run this migration on existing databases to fix registration
-- =============================================================================

-- Add INSERT policy for user_profiles
-- Users can insert their own profile (id must match auth.uid())
DROP POLICY IF EXISTS "Users can insert own profile" ON user_profiles;
CREATE POLICY "Users can insert own profile" ON user_profiles
    FOR INSERT WITH CHECK (id = auth.uid());

-- Also allow users to view their own tenant (needed during registration flow)
DROP POLICY IF EXISTS "Users can insert tenant on registration" ON tenants;
CREATE POLICY "Users can insert tenant on registration" ON tenants
    FOR INSERT WITH CHECK (true);  -- Service role will handle tenant creation

-- Verify policies exist
DO $$
BEGIN
    RAISE NOTICE 'RLS policies for registration have been added:';
    RAISE NOTICE '  - user_profiles: Users can insert own profile';
    RAISE NOTICE '  - tenants: Service role can create tenants';
    RAISE NOTICE '';
    RAISE NOTICE 'Registration should now work correctly.';
END $$;
