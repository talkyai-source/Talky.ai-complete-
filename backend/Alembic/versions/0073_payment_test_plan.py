"""Team-only $1 plan that checks the live money flow end to end.

Owner decision 2026-10-27: before customers pay, the team makes one real $1
purchase and follows it through checkout, the webhook, the invoice, the plan
and its minutes, then cancels or refunds it.

* ``payment_test`` costs $1/month for 2 AI minutes. Never 0: a plan with zero
  minutes reads as unlimited to the meter and the billing page.
* Only platform admins and STRIPE_TEST_CHECKOUT_EMAILS see or buy it
  (``billing_mode.TEAM_ONLY_PLAN_IDS``); the public catalog never lists it.
* Its Stripe price and ``plan_price_options`` row come from
  ``scripts/stripe_catalog_setup.py``, like the other plans.

No tenant row changes. Idempotent. Downgrade is refused: purchase terms are
retained history.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0073_payment_test_plan"
down_revision: Union[str, None] = "0072_allowance_anchor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FEATURES = (
    '["Team only: one real $1 charge to check payments", "Included AI minutes: 2/month", '
    '"Cancel it in Manage subscription after the check"]'
)

STATEMENTS = (
    "SET LOCAL lock_timeout = '5s'",
    f"""INSERT INTO public.plans
        (id, name, price, description, minutes, agents, concurrent_calls, features, not_included, popular)
        VALUES ('payment_test', 'Payment test', 1, 'Team only: checks real payments with a $1 charge.',
                2, 1, 1, '{FEATURES}'::jsonb, '[]'::jsonb, FALSE)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, price = EXCLUDED.price,
            description = EXCLUDED.description, minutes = EXCLUDED.minutes, agents = EXCLUDED.agents,
            concurrent_calls = EXCLUDED.concurrent_calls, features = EXCLUDED.features,
            not_included = EXCLUDED.not_included, popular = EXCLUDED.popular""",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError("Purchase terms are retained history; use a reviewed forward migration")
