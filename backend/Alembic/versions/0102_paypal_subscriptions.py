"""PayPal plans: the PayPal plan behind each price, each subscription, each payment.

Stripe's tables are not touched. A plan bought with PayPal lives in three new
tables beside them:

* ``paypal_plan_options``: the PayPal billing plan created for one approved
  price option, with a frozen copy of the terms it was created with. Platform
  data, so the platform-only policy ``plan_price_options`` has.
* ``paypal_subscriptions``: one row per purchase a customer started. Its id is
  the browser's request id, so a retry is the same purchase. A partial unique
  index allows one live row per tenant. ``status`` is ours; ``paypal_status``
  is the last status PayPal reported.
* ``paypal_subscription_payments``: one row per PayPal payment on a
  subscription (the invoice list reads it). Ids and amounts only: no payer
  name, email or address is stored.

Row security is enabled and forced on all three. No GRANT is issued (the
runtime role's rights come from runtime_role_grants.sql).
"""
from alembic import op

revision = "0102_paypal_subscriptions"
down_revision = "0101_paypal_webhook_events"
branch_labels = None
depends_on = None

BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
LIVE = "('creating', 'unknown', 'approval_pending', 'active', 'past_due', 'suspended')"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.paypal_plan_options (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        price_option_id   UUID NOT NULL UNIQUE REFERENCES plan_price_options(id),
        provider_mode     TEXT NOT NULL CHECK (provider_mode IN ('test', 'live')),
        paypal_product_id TEXT NOT NULL CHECK (paypal_product_id ~ '^[A-Za-z0-9_-]{6,50}$'),
        paypal_plan_id    TEXT NOT NULL UNIQUE CHECK (paypal_plan_id ~ '^[A-Za-z0-9_-]{6,50}$'),
        interval          TEXT NOT NULL CHECK (interval IN ('month', 'year')),
        amount_minor      BIGINT NOT NULL CHECK (amount_minor > 0),
        currency          TEXT NOT NULL CHECK (currency ~ '^[a-z]{3}$'),
        active            BOOLEAN NOT NULL DEFAULT FALSE,
        verified_at       TIMESTAMPTZ,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (NOT active OR verified_at IS NOT NULL)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.paypal_subscriptions (
        id                     UUID PRIMARY KEY,
        tenant_id              UUID NOT NULL REFERENCES tenants(id),
        price_option_id        UUID NOT NULL REFERENCES plan_price_options(id),
        plan_id                VARCHAR(50) NOT NULL REFERENCES plans(id),
        provider_mode          TEXT NOT NULL CHECK (provider_mode IN ('test', 'live')),
        paypal_plan_id         TEXT NOT NULL,
        paypal_subscription_id TEXT UNIQUE CHECK (paypal_subscription_id ~ '^[A-Za-z0-9_-]{1,64}$'),
        snapshot               JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
        status                 TEXT NOT NULL CHECK (status IN ('creating', 'unknown', 'approval_pending',
                                   'active', 'past_due', 'suspended', 'cancelled', 'expired', 'failed')),
        paypal_status          TEXT,
        approval_url           TEXT,
        activated_at           TIMESTAMPTZ,
        current_period_start   TIMESTAMPTZ,
        current_period_end     TIMESTAMPTZ,
        cancel_at_period_end   BOOLEAN NOT NULL DEFAULT FALSE,
        cancelled_at           TIMESTAMPTZ,
        ended_at               TIMESTAMPTZ,
        last_payment_at        TIMESTAMPTZ,
        failed_payments        INTEGER NOT NULL DEFAULT 0 CHECK (failed_payments >= 0),
        checked_at             TIMESTAMPTZ,
        created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS paypal_subscriptions_one_live_per_tenant "
        f"ON public.paypal_subscriptions (tenant_id) WHERE status IN {LIVE}"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS paypal_subscriptions_tenant_created "
        "ON public.paypal_subscriptions (tenant_id, created_at DESC)"
    )
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.paypal_subscription_payments (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id             UUID NOT NULL REFERENCES tenants(id),
        subscription_id       UUID NOT NULL REFERENCES paypal_subscriptions(id),
        paypal_transaction_id TEXT NOT NULL UNIQUE CHECK (paypal_transaction_id ~ '^[A-Za-z0-9_-]{1,64}$'),
        status                TEXT NOT NULL CHECK (status IN ('completed', 'pending', 'declined', 'failed',
                                  'partially_refunded', 'refunded', 'reversed')),
        amount_minor          BIGINT NOT NULL CHECK (amount_minor >= 0),
        currency              TEXT NOT NULL CHECK (currency ~ '^[a-z]{3}$'),
        paid_at               TIMESTAMPTZ NOT NULL,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS paypal_subscription_payments_tenant_paid "
        "ON public.paypal_subscription_payments (tenant_id, paid_at DESC)"
    )
    for table in ("paypal_plan_options", "paypal_subscriptions", "paypal_subscription_payments"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS paypal_plan_options_platform_only ON public.paypal_plan_options")
    op.execute(
        "CREATE POLICY paypal_plan_options_platform_only ON public.paypal_plan_options "
        f"FOR ALL USING ({BYPASS}) WITH CHECK ({BYPASS})"
    )
    for table in ("paypal_subscriptions", "paypal_subscription_payments"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} "
            f"FOR ALL USING ({BYPASS} OR {TENANT}) WITH CHECK ({BYPASS} OR {TENANT})"
        )


def downgrade():
    raise RuntimeError(
        "PayPal subscriptions and payments are billing records and must be retained; "
        "use a reviewed forward migration"
    )
