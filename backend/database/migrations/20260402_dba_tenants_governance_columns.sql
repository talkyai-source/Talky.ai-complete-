-- =============================================================================
-- DBA Migration: Add Governance Columns to tenants table
-- Date:      2026-04-02
-- Purpose:   Add status, suspension, partner, and subscription expiry columns.
--
-- REQUIRES:  Must be run as the postgres superuser (table owner).
--            Example:  sudo -u postgres psql -d talkyai -f <this_file>
--
-- These columns are used by CallGuard._check_tenant_active(),
-- _check_subscription(), and _get_partner_id(). Until this migration
-- is applied, those methods gracefully fall back to subscription_status
-- or return None for partner_id.
-- =============================================================================

BEGIN;

-- status — overall tenant lifecycle (active/inactive/suspended)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tenants' AND column_name = 'status'
    ) THEN
        ALTER TABLE tenants ADD COLUMN status VARCHAR(50) DEFAULT 'active';
        CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
    END IF;
END $$;

-- suspended_at — when tenant was last suspended
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tenants' AND column_name = 'suspended_at'
    ) THEN
        ALTER TABLE tenants ADD COLUMN suspended_at TIMESTAMPTZ;
    END IF;
END $$;

-- suspension_reason — why tenant was suspended
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tenants' AND column_name = 'suspension_reason'
    ) THEN
        ALTER TABLE tenants ADD COLUMN suspension_reason TEXT;
    END IF;
END $$;

-- subscription_expires_at — hard expiry date for subscription
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tenants' AND column_name = 'subscription_expires_at'
    ) THEN
        ALTER TABLE tenants ADD COLUMN subscription_expires_at TIMESTAMPTZ;
    END IF;
END $$;

-- partner_id — self-referencing FK for partner/reseller hierarchy
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tenants' AND column_name = 'partner_id'
    ) THEN
        ALTER TABLE tenants ADD COLUMN partner_id UUID REFERENCES tenants(id);
        CREATE INDEX IF NOT EXISTS idx_tenants_partner_id
            ON tenants(partner_id) WHERE partner_id IS NOT NULL;
    END IF;
END $$;

-- Grant the app user access to the new columns
GRANT SELECT, INSERT, UPDATE ON tenants TO talkyai;

COMMIT;

DO $$
BEGIN
    RAISE NOTICE 'DBA migration applied: tenants governance columns added.';
END $$;
