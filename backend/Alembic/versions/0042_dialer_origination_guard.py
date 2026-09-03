"""pin one durable calls row to each dialer job attempt

Revision ID: 0042_dialer_origination_guard
Revises: 0041_tenant_campaign_fk
Create Date: 2026-09-03

Historical rows are deliberately left NULL. A dialer job may have produced
several calls before attempts were recorded on ``calls``; guessing from the
job's current counter would collapse legitimate history onto one unique key.
Only the new durable-before-provider path writes the column.
"""

from alembic import op
from sqlalchemy import text


revision = "0042_dialer_origination_guard"
down_revision = "0041_tenant_campaign_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE public.calls "
            "ADD COLUMN IF NOT EXISTS dialer_attempt_number INTEGER"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.calls DROP CONSTRAINT IF EXISTS "
            "calls_dialer_attempt_number_positive"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.calls "
            "ADD CONSTRAINT calls_dialer_attempt_number_positive "
            "CHECK (dialer_attempt_number IS NULL OR dialer_attempt_number >= 1)"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_calls_dialer_job_attempt "
            "ON public.calls (dialer_job_id, dialer_attempt_number) "
            "WHERE dialer_job_id IS NOT NULL "
            "AND dialer_attempt_number IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(
        text("DROP INDEX IF EXISTS public.uq_calls_dialer_job_attempt")
    )
    op.execute(
        text(
            "ALTER TABLE public.calls DROP CONSTRAINT IF EXISTS "
            "calls_dialer_attempt_number_positive"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.calls "
            "DROP COLUMN IF EXISTS dialer_attempt_number"
        )
    )
