"""Paid plans renew their minutes monthly from the purchase, not on the 1st.

``tenants.allowance_anchor_at`` is set when a paid plan activates
(``apply_plan_allocation``). The meter counts usage from the start of the
current month measured from that moment, so a customer who subscribes on the
25th no longer receives a second full allowance on the 1st. NULL keeps the
calendar month, so no existing account changes. Additive and idempotent.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0072_allowance_anchor"
down_revision: Union[str, None] = "0071_paid_only_billing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(text("ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS allowance_anchor_at timestamptz"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE public.tenants DROP COLUMN IF EXISTS allowance_anchor_at"))
