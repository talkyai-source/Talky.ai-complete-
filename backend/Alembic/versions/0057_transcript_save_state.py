"""Distinguish a final saved transcript from partial or unavailable evidence."""
from alembic import op

revision = "0057_transcript_save_state"
down_revision = "0056_billing_refund_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""ALTER TABLE public.calls ADD COLUMN IF NOT EXISTS transcript_save_state TEXT NOT NULL DEFAULT 'unknown'
                  CHECK (transcript_save_state IN ('unknown','partial','complete','failed'))""")
    op.execute("""COMMENT ON COLUMN public.calls.transcript_save_state IS
        'Completeness of persisted transcript snapshot, not transcription accuracy or proof of hearing. Legacy unknown is not backfilled as complete.'""")


def downgrade():
    # Evidence must survive code rollback; removing it would turn partial saves
    # into apparently complete historical records.
    pass
