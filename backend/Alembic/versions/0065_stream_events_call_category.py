"""Allow the 'call' category in stream_events on every database lineage.

Bug hunt 2026-10-10 (workers-jobs-ops-9): event_emitter writes call-lifecycle
events with category 'call'. Production has that value only because
database/migrations/20260609_call_failure_and_lead_signals.sql was applied by
hand; a database built through Alembic keeps the 5-value CHECK, so every call
event is rejected and silently dropped there.

Idempotent and a no-op on production: it changes nothing when the constraint
already admits 'call' or when the table is absent (documented bootstrap
floor). The new constraint is added NOT VALID and validated separately, so the
table is never locked for a full scan under ACCESS EXCLUSIVE.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0065_stream_events_call_category"
down_revision: Union[str, None] = "0064_dnc_phone_number_default"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One statement per op.execute (asyncpg prepared statements refuse several).
WIDEN_SQL = """
DO $$
BEGIN
    IF to_regclass('public.stream_events') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.stream_events'::regclass
          AND conname = 'stream_events_category_check'
          AND pg_get_constraintdef(oid) LIKE '%''call''%'
    ) THEN
        RETURN;
    END IF;
    ALTER TABLE public.stream_events DROP CONSTRAINT IF EXISTS stream_events_category_check;
    ALTER TABLE public.stream_events ADD CONSTRAINT stream_events_category_check
        CHECK (category = ANY (ARRAY['campaign', 'system', 'alert', 'user_action', 'milestone', 'call']))
        NOT VALID;
END $$
"""
VALIDATE_SQL = """
DO $$
BEGIN
    IF to_regclass('public.stream_events') IS NOT NULL AND EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.stream_events'::regclass
          AND conname = 'stream_events_category_check' AND NOT convalidated
    ) THEN
        ALTER TABLE public.stream_events VALIDATE CONSTRAINT stream_events_category_check;
    END IF;
END $$
"""
UPGRADE_STATEMENTS = (WIDEN_SQL, VALIDATE_SQL)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # The widened CHECK is a superset of the old one. Narrowing it would need
    # deleting or rewriting stored call events, so the downgrade keeps it.
    pass
