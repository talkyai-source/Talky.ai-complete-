"""Fold the hand-run call-failure and lead-signal columns into Alembic (QA F14-02).

database/migrations/20260609_call_failure_and_lead_signals.sql was applied by
hand on production and never reached Alembic or complete_schema.sql. A
database built from the repository therefore lacked calls.failure_reason /
failure_category and the leads lead-signal columns: adding a contact whose
number already exists returned 500 ("column is_lead does not exist"), and
every query reading calls.failure_reason failed there.

Every statement is guarded, so production, which already has all of it, is
unchanged. The stream_events CHECK is rewritten only where the table exists
and its constraint does not yet allow 'call', so production is not rescanned.

Revision ID: 0084_call_failure_lead_signals
Revises: 0083_pay_as_you_go
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0084_call_failure_lead_signals"
down_revision: Union[str, None] = "0083_pay_as_you_go"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS failure_reason TEXT")
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS failure_category VARCHAR(40)")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_lead BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS follow_up_note TEXT")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS qualified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS qualified_call_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS idx_leads_is_lead ON leads (campaign_id) WHERE is_lead")
    op.execute(
        """
        DO $$
        BEGIN
          -- to_regclass, never a ::regclass cast: the cast is resolved when
          -- the statement is planned and fails where the table is absent.
          IF to_regclass('public.stream_events') IS NOT NULL THEN
            IF NOT EXISTS (
              SELECT 1 FROM pg_constraint
               WHERE conrelid = to_regclass('public.stream_events')
                 AND conname = 'stream_events_category_check'
                 AND pg_get_constraintdef(oid) LIKE '%''call''%'
            ) THEN
              ALTER TABLE stream_events DROP CONSTRAINT IF EXISTS stream_events_category_check;
              ALTER TABLE stream_events ADD CONSTRAINT stream_events_category_check
                CHECK (category = ANY (ARRAY[
                  'campaign'::text, 'system'::text, 'alert'::text,
                  'user_action'::text, 'milestone'::text, 'call'::text
                ]));
            END IF;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Forward-only: production carried these columns long before this
    # revision existed, and code on either side of it reads them.
    pass
