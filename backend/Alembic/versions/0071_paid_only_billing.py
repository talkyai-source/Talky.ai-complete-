"""Paid-only billing: the plans and top-up packs on the public pricing page.

Owner decision 2026-10-27: no free minutes for anyone from now on, without
taking away what existing accounts already have.

* The free price option is retired (``active = FALSE``). New accounts start
  with no plan and no minutes (``signup.py``), and checkout refuses the free
  kind in code as well, so this row only stops the catalog listing it.
* ``basic`` and a new ``business`` plan carry the pricing page's terms:
  $99/month for 300 AI minutes and $299/month for 1,000. Their Stripe prices
  (monthly, and yearly at 17% off) are created by
  ``scripts/stripe_catalog_setup.py``, which also writes their
  ``plan_price_options`` rows; prices are provider objects, not migration data.
  ``professional`` and ``enterprise`` keep their rows for history and drop out
  of the catalog because they have no price option.
* The GBP top-up packs are deactivated and the pricing page's USD packs added:
  $50 for 125, $70 for 175 and $100 for 250 minutes, never expiring (top-up
  minutes are used once, after the plan's minutes; see ``topup_usage``).

No tenant row changes: allowances, plans and statuses stay as they are.
Idempotent. Downgrade is refused: purchase terms are retained history.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0071_paid_only_billing"
down_revision: Union[str, None] = "0070_trunk_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BASIC_FEATURES = (
    '["Included AI minutes: 300/month", "Concurrent calls: 3", "Campaigns: 2", '
    '"Voice recording storage: 2 GB", "Recording retention: 30 days", '
    '"Live support: up to 1 hour/month"]'
)
BASIC_NOT_INCLUDED = '["CRM connectors", "Automatic campaign wizard"]'
BUSINESS_FEATURES = (
    '["Included AI minutes: 1,000/month", "Concurrent calls: 10", "Campaigns: 10", '
    '"Voice recording storage: 4 GB", "Recording retention: 90 days", '
    '"Live support: up to 3 hours/month", "HubSpot, Salesforce and supported CRM connectors", '
    '"Automatic campaign wizard"]'
)

# One statement per op.execute: the asyncpg dialect refuses several at once.
STATEMENTS = (
    "SET LOCAL lock_timeout = '5s'",
    "UPDATE public.plan_price_options SET active = FALSE, updated_at = NOW() WHERE kind = 'free' AND active",
    f"""UPDATE public.plans SET name = 'Basic', price = 99,
        description = 'For teams starting AI calling.', minutes = 300, agents = 2,
        concurrent_calls = 3, features = '{BASIC_FEATURES}'::jsonb,
        not_included = '{BASIC_NOT_INCLUDED}'::jsonb, popular = FALSE
        WHERE id = 'basic'""",
    f"""INSERT INTO public.plans
        (id, name, price, description, minutes, agents, concurrent_calls, features, not_included, popular)
        VALUES ('business', 'Business', 299, 'For teams running several campaigns.', 1000, 10, 10,
                '{BUSINESS_FEATURES}'::jsonb, '[]'::jsonb, TRUE)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, price = EXCLUDED.price,
            description = EXCLUDED.description, minutes = EXCLUDED.minutes, agents = EXCLUDED.agents,
            concurrent_calls = EXCLUDED.concurrent_calls, features = EXCLUDED.features,
            not_included = EXCLUDED.not_included, popular = EXCLUDED.popular""",
    "UPDATE public.plans SET popular = FALSE WHERE id IN ('professional', 'enterprise')",
    "UPDATE public.topup_packages SET is_active = FALSE WHERE code IN ('mins_250', 'mins_600', 'mins_1500')",
    """INSERT INTO public.topup_packages (code, name, minutes, price_cents, currency, expires_days, is_active, sort_order)
        VALUES ('usd_125', '125 minutes', 125, 5000, 'USD', NULL, TRUE, 11),
               ('usd_175', '175 minutes', 175, 7000, 'USD', NULL, TRUE, 12),
               ('usd_250', '250 minutes', 250, 10000, 'USD', NULL, TRUE, 13)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, minutes = EXCLUDED.minutes,
            price_cents = EXCLUDED.price_cents, currency = EXCLUDED.currency,
            expires_days = EXCLUDED.expires_days, is_active = TRUE, sort_order = EXCLUDED.sort_order""",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError("Purchase terms are retained history; use a reviewed forward migration")
