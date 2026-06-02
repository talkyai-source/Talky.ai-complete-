-- =============================================================================
-- 20260507_fix_rls_empty_setting_cast.sql
--
-- Bug: the day4 RLS policies cast `current_setting('app.bypass_rls', true)`
-- directly to BOOLEAN and `current_setting('app.current_tenant_id', true)`
-- to UUID. With `missing_ok = true`, an unset GUC returns the empty string
-- '', and Postgres errors with:
--
--     invalid input syntax for type boolean: ""
--     invalid input syntax for type uuid: ""
--
-- This blows up every query that hits one of these tables on a raw pool
-- connection (i.e. one acquired without explicitly running
-- `SET LOCAL app.current_tenant_id` first). Symptoms in production:
-- compute_tenant_minutes_used, fetch_campaign_transcripts, list_calls all
-- fail with the cryptic boolean cast error.
--
-- Fix: wrap each `current_setting()` in `NULLIF(..., '')` so an unset GUC
-- becomes NULL instead of '', and re-order the OR so the cheap bypass
-- check runs first. Behaviour is preserved: when neither GUC is set, the
-- policy returns NULL/FALSE → no rows → fail-closed.
-- =============================================================================

BEGIN;

-- campaigns ----------------------------------------------------------------
DROP POLICY IF EXISTS campaigns_tenant_isolation ON campaigns;
CREATE POLICY campaigns_tenant_isolation ON campaigns
    USING (
        COALESCE(NULLIF(current_setting('app.bypass_rls', true), ''), 'false')::BOOLEAN = TRUE
        OR tenant_id::text = NULLIF(current_setting('app.current_tenant_id', true), '')
    );

-- leads --------------------------------------------------------------------
DROP POLICY IF EXISTS leads_tenant_isolation ON leads;
CREATE POLICY leads_tenant_isolation ON leads
    USING (
        COALESCE(NULLIF(current_setting('app.bypass_rls', true), ''), 'false')::BOOLEAN = TRUE
        OR tenant_id::text = NULLIF(current_setting('app.current_tenant_id', true), '')
    );

-- calls --------------------------------------------------------------------
DROP POLICY IF EXISTS calls_tenant_isolation ON calls;
CREATE POLICY calls_tenant_isolation ON calls
    USING (
        COALESCE(NULLIF(current_setting('app.bypass_rls', true), ''), 'false')::BOOLEAN = TRUE
        OR tenant_id::text = NULLIF(current_setting('app.current_tenant_id', true), '')
    );

-- conversations ------------------------------------------------------------
DROP POLICY IF EXISTS conversations_tenant_isolation ON conversations;
CREATE POLICY conversations_tenant_isolation ON conversations
    USING (
        COALESCE(NULLIF(current_setting('app.bypass_rls', true), ''), 'false')::BOOLEAN = TRUE
        OR tenant_id::text = NULLIF(current_setting('app.current_tenant_id', true), '')
    );

-- connectors ---------------------------------------------------------------
DROP POLICY IF EXISTS connectors_tenant_isolation ON connectors;
CREATE POLICY connectors_tenant_isolation ON connectors
    USING (
        COALESCE(NULLIF(current_setting('app.bypass_rls', true), ''), 'false')::BOOLEAN = TRUE
        OR tenant_id::text = NULLIF(current_setting('app.current_tenant_id', true), '')
    );

COMMIT;
