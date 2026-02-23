-- =============================================================================
-- Stripe Billing Integration Migration
-- =============================================================================
-- 
-- This migration adds Stripe billing support including:
--   1. stripe_price_id and stripe_product_id columns to plans table
--   2. stripe_customer_id and subscription tracking to tenants table
--   3. New subscriptions table for detailed subscription state
--   4. New invoices table for invoice history
--   5. New usage_records table for metered billing
--
-- Run this AFTER the base schema.sql has been applied.
-- 
-- Created: December 31, 2025
-- Project: Talky.ai Voice Dialer - Stripe Billing
-- =============================================================================

-- =============================================================================
-- SECTION 1: ALTER EXISTING TABLES
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1.1 Add Stripe columns to PLANS table
-- -----------------------------------------------------------------------------
ALTER TABLE plans ADD COLUMN IF NOT EXISTS stripe_price_id VARCHAR(100);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS stripe_product_id VARCHAR(100);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS billing_period VARCHAR(20) DEFAULT 'monthly';

COMMENT ON COLUMN plans.stripe_price_id IS 'Stripe Price ID for this plan (e.g., price_xxx)';
COMMENT ON COLUMN plans.stripe_product_id IS 'Stripe Product ID for this plan (e.g., prod_xxx)';
COMMENT ON COLUMN plans.billing_period IS 'Billing period: monthly, yearly';

-- -----------------------------------------------------------------------------
-- 1.2 Add Stripe columns to TENANTS table
-- -----------------------------------------------------------------------------
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(100);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(100);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(50) DEFAULT 'inactive';

CREATE INDEX IF NOT EXISTS idx_tenants_stripe_customer ON tenants(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_tenants_subscription_status ON tenants(subscription_status);

COMMENT ON COLUMN tenants.stripe_customer_id IS 'Stripe Customer ID (e.g., cus_xxx)';
COMMENT ON COLUMN tenants.stripe_subscription_id IS 'Active Stripe Subscription ID (e.g., sub_xxx)';
COMMENT ON COLUMN tenants.subscription_status IS 'Status: inactive, trialing, active, past_due, canceled, unpaid';

-- =============================================================================
-- SECTION 2: CREATE NEW BILLING TABLES
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 2.1 SUBSCRIPTIONS TABLE - Detailed subscription tracking
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_subscription_id VARCHAR(100) NOT NULL UNIQUE,
    stripe_customer_id VARCHAR(100) NOT NULL,
    plan_id VARCHAR(50) REFERENCES plans(id),
    status VARCHAR(50) NOT NULL DEFAULT 'incomplete',
    -- Status values: incomplete, incomplete_expired, trialing, active, past_due, canceled, unpaid, paused
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at TIMESTAMPTZ,
    canceled_at TIMESTAMPTZ,
    trial_start TIMESTAMPTZ,
    trial_end TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant_id ON subscriptions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_sub_id ON subscriptions(stripe_subscription_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_period_end ON subscriptions(current_period_end);

-- Add RLS for subscriptions
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view subscriptions in their tenant" ON subscriptions;
CREATE POLICY "Users can view subscriptions in their tenant" ON subscriptions
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all subscriptions" ON subscriptions;
CREATE POLICY "Service role can manage all subscriptions" ON subscriptions
    FOR ALL USING (auth.role() = 'service_role');

COMMENT ON TABLE subscriptions IS 'Stripe subscription records with detailed state tracking';

-- Trigger for updated_at
DROP TRIGGER IF EXISTS update_subscriptions_updated_at ON subscriptions;
CREATE TRIGGER update_subscriptions_updated_at BEFORE UPDATE ON subscriptions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 2.2 INVOICES TABLE - Invoice history
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_invoice_id VARCHAR(100) NOT NULL UNIQUE,
    stripe_subscription_id VARCHAR(100),
    amount_due INTEGER NOT NULL, -- in cents
    amount_paid INTEGER NOT NULL DEFAULT 0, -- in cents
    currency VARCHAR(10) DEFAULT 'usd',
    status VARCHAR(50) NOT NULL,
    -- Status values: draft, open, paid, uncollectible, void
    invoice_pdf TEXT,
    hosted_invoice_url TEXT,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    due_date TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoices_tenant_id ON invoices(tenant_id);
CREATE INDEX IF NOT EXISTS idx_invoices_stripe_invoice_id ON invoices(stripe_invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices(created_at);

-- Add RLS for invoices
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view invoices in their tenant" ON invoices;
CREATE POLICY "Users can view invoices in their tenant" ON invoices
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all invoices" ON invoices;
CREATE POLICY "Service role can manage all invoices" ON invoices
    FOR ALL USING (auth.role() = 'service_role');

COMMENT ON TABLE invoices IS 'Stripe invoice records for billing history';

-- -----------------------------------------------------------------------------
-- 2.3 USAGE_RECORDS TABLE - Metered billing tracking
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
    usage_type VARCHAR(50) NOT NULL DEFAULT 'minutes', -- minutes, api_calls, etc.
    quantity INTEGER NOT NULL, -- e.g., minutes used
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    reported_to_stripe BOOLEAN DEFAULT false,
    stripe_usage_record_id VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_records_tenant_id ON usage_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_usage_records_subscription_id ON usage_records(subscription_id);
CREATE INDEX IF NOT EXISTS idx_usage_records_timestamp ON usage_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_records_not_reported ON usage_records(reported_to_stripe) WHERE reported_to_stripe = false;
CREATE INDEX IF NOT EXISTS idx_usage_records_type ON usage_records(usage_type);

-- Add RLS for usage_records
ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view usage in their tenant" ON usage_records;
CREATE POLICY "Users can view usage in their tenant" ON usage_records
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all usage_records" ON usage_records;
CREATE POLICY "Service role can manage all usage_records" ON usage_records
    FOR ALL USING (auth.role() = 'service_role');

COMMENT ON TABLE usage_records IS 'Usage tracking for metered billing (minutes, API calls, etc.)';
COMMENT ON COLUMN usage_records.quantity IS 'Amount of usage (e.g., minutes consumed)';
COMMENT ON COLUMN usage_records.reported_to_stripe IS 'Whether this usage has been reported to Stripe for billing';

-- =============================================================================
-- SECTION 3: UPDATE DEFAULT PLANS WITH PLACEHOLDER STRIPE IDS
-- =============================================================================
-- NOTE: Replace these placeholder IDs with actual Stripe Price IDs from your dashboard

UPDATE plans SET 
    stripe_price_id = 'price_placeholder_basic',
    stripe_product_id = 'prod_placeholder_basic',
    billing_period = 'monthly'
WHERE id = 'basic' AND stripe_price_id IS NULL;

UPDATE plans SET 
    stripe_price_id = 'price_placeholder_professional',
    stripe_product_id = 'prod_placeholder_professional',
    billing_period = 'monthly'
WHERE id = 'professional' AND stripe_price_id IS NULL;

UPDATE plans SET 
    stripe_price_id = 'price_placeholder_enterprise',
    stripe_product_id = 'prod_placeholder_enterprise',
    billing_period = 'monthly'
WHERE id = 'enterprise' AND stripe_price_id IS NULL;

-- =============================================================================
-- SUCCESS NOTIFICATION
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=============================================================================';
    RAISE NOTICE 'Stripe Billing Migration completed successfully!';
    RAISE NOTICE '';
    RAISE NOTICE 'Tables modified:';
    RAISE NOTICE '  - plans: Added stripe_price_id, stripe_product_id, billing_period';
    RAISE NOTICE '  - tenants: Added stripe_customer_id, stripe_subscription_id, subscription_status';
    RAISE NOTICE '';
    RAISE NOTICE 'Tables created:';
    RAISE NOTICE '  - subscriptions: Detailed subscription tracking';
    RAISE NOTICE '  - invoices: Invoice history';
    RAISE NOTICE '  - usage_records: Metered billing tracking';
    RAISE NOTICE '';
    RAISE NOTICE 'NEXT STEPS:';
    RAISE NOTICE '  1. Create Products and Prices in Stripe Dashboard';
    RAISE NOTICE '  2. Update plans table with actual stripe_price_id values';
    RAISE NOTICE '  3. Configure webhook endpoint in Stripe Dashboard';
    RAISE NOTICE '=============================================================================';
END $$;
