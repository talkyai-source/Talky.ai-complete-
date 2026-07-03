-- Contact Lists: group uploaded contacts into named, toggleable lists.
--
-- One list per upload (a CSV file or a paste). A campaign can turn a whole
-- list on/off for dialing and "call this list". Existing leads keep
-- list_id = NULL and are treated as an always-active "Ungrouped" bucket, so
-- nothing that exists today changes behaviour.
--
-- Additive + idempotent (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS / CREATE INDEX IF NOT EXISTS). Applied manually via psql on prod
-- (no auto-runner) — mirrors the other migrations in this directory.

-- 1) The lists themselves.
CREATE TABLE IF NOT EXISTS contact_lists (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL,
    tenant_id   UUID,
    name        TEXT NOT NULL,
    -- Where the list came from: 'csv' | 'paste' | 'manual'.
    source      TEXT NOT NULL DEFAULT 'manual',
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast "show me this campaign's lists" lookup.
CREATE INDEX IF NOT EXISTS idx_contact_lists_campaign
    ON contact_lists (campaign_id);

-- 2) Tag every lead with the list it belongs to (nullable — existing rows
--    stay NULL = Ungrouped = always eligible).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS list_id UUID;

-- Index the tag so the dialer's active-list filter and the per-list contact
-- count stay index-backed (no hot-path scan). Partial: only rows that
-- actually belong to a list — NULL/Ungrouped leads never hit this index.
CREATE INDEX IF NOT EXISTS idx_leads_list_id
    ON leads (list_id) WHERE list_id IS NOT NULL;

-- 3) RLS: contact_lists is tenant-scoped exactly like the rest of the schema.
--    The app also filters by tenant_id in every query (defense in depth); this
--    policy is the backstop. Mirrors the tenant_recording_policy style: the
--    bypass_rls escape hatch lets the adapter's admin/worker path see rows the
--    same way it does for leads/campaigns. Idempotent via DROP ... IF EXISTS.
ALTER TABLE contact_lists ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contact_lists_tenant_isolation ON contact_lists;
CREATE POLICY contact_lists_tenant_isolation ON contact_lists
    FOR ALL
    USING (
        current_setting('app.bypass_rls', TRUE) = 'true'
        OR tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
    )
    WITH CHECK (
        current_setting('app.bypass_rls', TRUE) = 'true'
        OR tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
    );

COMMENT ON TABLE contact_lists IS
    'Named, toggleable groups of leads (one per CSV upload or paste). '
    'leads.list_id points here; NULL = Ungrouped = always active. The dialer '
    'excludes leads whose list is is_active=false.';
