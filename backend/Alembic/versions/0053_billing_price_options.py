"""Approved recurring price choices and durable checkout identities."""

from alembic import op
from sqlalchemy import text

revision = "0053_billing_price_options"
down_revision = "0052_public_contact_enquiries"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(
        text(
            """CREATE TABLE public.plan_price_options (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        plan_id VARCHAR(50) NOT NULL REFERENCES plans(id),
        stripe_price_id TEXT UNIQUE,
        stripe_product_id TEXT,
        kind TEXT NOT NULL CHECK (kind IN ('free', 'stripe')),
        interval TEXT NOT NULL CHECK (interval IN ('month', 'year')),
        interval_count INTEGER NOT NULL DEFAULT 1 CHECK (interval_count = 1),
        amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0),
        currency TEXT NOT NULL CHECK (currency ~ '^[a-z]{3}$'),
        currency_exponent SMALLINT NOT NULL CHECK (currency_exponent IN (0, 2, 3)),
        provider_mode TEXT NOT NULL CHECK (provider_mode IN ('free', 'test', 'live')),
        active BOOLEAN NOT NULL DEFAULT FALSE,
        verified_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (plan_id, interval, provider_mode),
        CHECK ((kind = 'free' AND amount_minor = 0 AND stripe_price_id IS NULL
            AND stripe_product_id IS NULL AND provider_mode = 'free' AND interval = 'month')
            OR (kind = 'stripe' AND amount_minor > 0 AND stripe_price_id ~ '^price_[A-Za-z0-9]+$'
            AND stripe_price_id IS NOT NULL AND stripe_product_id ~ '^prod_[A-Za-z0-9]+$'
            AND stripe_product_id IS NOT NULL AND provider_mode IN ('test', 'live'))),
        CHECK (NOT active OR verified_at IS NOT NULL)
    )"""
        )
    )
    # Legacy paid rows lack an authoritative currency/interval and are untouched.
    op.execute(
        text(
            """INSERT INTO public.plan_price_options
        (plan_id, kind, interval, amount_minor, currency, currency_exponent,
         provider_mode, active, verified_at)
        SELECT id, 'free', 'month', 0, 'usd', 2, 'free', TRUE, NOW()
        FROM plans WHERE price = 0 AND stripe_price_id IS NULL"""
        )
    )
    op.execute(
        text(
            """CREATE TABLE public.billing_checkout_attempts (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants(id),
        price_option_id UUID NOT NULL REFERENCES plan_price_options(id),
        request_hash CHAR(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
        snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
        status TEXT NOT NULL CHECK (status IN ('creating','ready','unknown','completed','expired','failed')),
        stripe_customer_id TEXT,
        stripe_session_id TEXT UNIQUE,
        checkout_url TEXT,
        provider_expires_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )"""
        )
    )
    op.execute(
        text(
            """CREATE UNIQUE INDEX billing_checkout_one_outstanding_per_tenant
        ON billing_checkout_attempts (tenant_id)
        WHERE status IN ('creating','ready','unknown')"""
        )
    )
    op.execute(
        text(
            "CREATE INDEX billing_checkout_tenant_created ON billing_checkout_attempts (tenant_id, created_at DESC)"
        )
    )
    for table in ("plan_price_options", "billing_checkout_attempts"):
        op.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
    bypass = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    op.execute(
        text(
            f"""CREATE POLICY plan_price_options_platform_only ON public.plan_price_options
        FOR ALL USING ({bypass}) WITH CHECK ({bypass})"""
        )
    )
    tenant = "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
    op.execute(
        text(
            f"""CREATE POLICY tenant_isolation ON public.billing_checkout_attempts
        FOR ALL USING ({bypass} OR {tenant}) WITH CHECK ({bypass} OR {tenant})"""
        )
    )


def downgrade():
    raise RuntimeError(
        "Billing purchase identities must be retained; use a reviewed forward migration"
    )
