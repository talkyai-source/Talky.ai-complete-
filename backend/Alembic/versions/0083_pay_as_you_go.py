"""Pay as you go: prepaid minutes without a subscription.

Owner decision 31 October 2026: customers may buy minutes without a monthly or
yearly plan. Prepaid only (calls stop at a zero balance), prices kept in the
database so they can change without a release, and no plan limits beyond what
the account pays for (the most premium plan's capabilities).

* ``plans.billing_model``: ``subscription`` (every existing plan) or
  ``prepaid``. A plan with zero included minutes is the "unlimited" sentinel
  only for ``subscription``; ``prepaid`` zero means "only what was bought".
* Plan ``payg`` "Pay as you go": no monthly price, zero included minutes,
  Business capabilities. Never offered through Stripe subscriptions, so the
  subscription catalogue does not list it.
* ``topup_packages.audience``: ``subscriber`` bundles top up a paid plan (the
  existing USD bundles); ``payg`` bundles are bought without one. Initial
  pay-as-you-go bundles equal today's top-up prices ($0.40/min) until the owner
  sets the rate through the Admin API.

No tenant row changes. Downgrade refuses while any account is on the prepaid
plan or any pay-as-you-go order exists.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0083_pay_as_you_go"
down_revision: Union[str, None] = "0082_support_system"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FEATURES = (
    '["No monthly fee: buy minutes when you need them", "Minutes carry over until used", '
    '"Concurrent calls: 10", "Campaigns: 10", "Voice recording storage: 4 GB", '
    '"Recording retention: 90 days", "HubSpot, Salesforce and supported CRM connectors", '
    '"Automatic campaign wizard"]'
)

STATEMENTS = (
    "SET LOCAL lock_timeout = '5s'",
    """ALTER TABLE public.plans ADD COLUMN IF NOT EXISTS billing_model VARCHAR(16) NOT NULL DEFAULT 'subscription'""",
    """ALTER TABLE public.plans DROP CONSTRAINT IF EXISTS plans_billing_model_check""",
    """ALTER TABLE public.plans ADD CONSTRAINT plans_billing_model_check
        CHECK (billing_model IN ('subscription', 'prepaid'))""",
    """ALTER TABLE public.topup_packages ADD COLUMN IF NOT EXISTS audience VARCHAR(16) NOT NULL DEFAULT 'subscriber'""",
    """ALTER TABLE public.topup_packages DROP CONSTRAINT IF EXISTS topup_packages_audience_check""",
    """ALTER TABLE public.topup_packages ADD CONSTRAINT topup_packages_audience_check
        CHECK (audience IN ('subscriber', 'payg'))""",
    f"""INSERT INTO public.plans
        (id, name, price, description, minutes, agents, concurrent_calls, features, not_included, popular,
         billing_model)
        VALUES ('payg', 'Pay as you go', 0, 'No monthly fee. Buy minutes when you need them.',
                0, 10, 10, '{FEATURES}'::jsonb, '[]'::jsonb, FALSE, 'prepaid')
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, price = EXCLUDED.price,
            description = EXCLUDED.description, minutes = EXCLUDED.minutes, agents = EXCLUDED.agents,
            concurrent_calls = EXCLUDED.concurrent_calls, features = EXCLUDED.features,
            not_included = EXCLUDED.not_included, popular = EXCLUDED.popular,
            billing_model = EXCLUDED.billing_model""",
    """INSERT INTO public.topup_packages
        (code, name, minutes, price_cents, currency, expires_days, is_active, sort_order, audience)
        VALUES ('payg_125', '125 minutes', 125, 5000, 'USD', NULL, TRUE, 21, 'payg'),
               ('payg_175', '175 minutes', 175, 7000, 'USD', NULL, TRUE, 22, 'payg'),
               ('payg_250', '250 minutes', 250, 10000, 'USD', NULL, TRUE, 23, 'payg')
        ON CONFLICT (code) DO NOTHING""",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(text(statement))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("SET LOCAL app.bypass_rls = 'on'"))
    in_use = conn.execute(text(
        """SELECT EXISTS (SELECT 1 FROM public.tenants WHERE plan_id = 'payg')
               OR EXISTS (SELECT 1 FROM public.topup_orders o
                          JOIN public.topup_packages p ON p.code = o.package_code
                          WHERE p.audience = 'payg')""")).scalar()
    if in_use:
        raise RuntimeError(
            "Refusing to remove pay as you go: accounts or orders use it. "
            "Move them with a reviewed forward migration first.")
    for statement in (
        "SET LOCAL lock_timeout = '5s'",
        "DELETE FROM public.topup_packages WHERE audience = 'payg'",
        "DELETE FROM public.plans WHERE id = 'payg'",
        "ALTER TABLE public.topup_packages DROP CONSTRAINT IF EXISTS topup_packages_audience_check",
        "ALTER TABLE public.topup_packages DROP COLUMN IF EXISTS audience",
        "ALTER TABLE public.plans DROP CONSTRAINT IF EXISTS plans_billing_model_check",
        "ALTER TABLE public.plans DROP COLUMN IF EXISTS billing_model",
    ):
        conn.execute(text(statement))
