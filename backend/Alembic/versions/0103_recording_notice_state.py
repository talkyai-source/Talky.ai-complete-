"""Recordings carry the state of their spoken notice, and whether a transfer is in them.

Owner decision, 18 November 2026: a captured recording is kept even when the
delivery receipt for the spoken recording notice is missing, and it is marked.

* ``notice_state``: ``delivered`` (a transport receipt exists), ``not_required``
  (the policy needs no notice) or ``unconfirmed`` (required, no receipt).
* ``transfer_part``: for a call handed to a person, ``included`` or ``missing``
  (the person's side of the call is, or is not, in the file).

Both columns are NULL for rows written before this revision (recorded under
the old rule). No default and no backfill.
"""
from alembic import op

revision = "0103_recording_notice_state"
down_revision = "0102_paypal_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("ALTER TABLE public.recordings_s3 ADD COLUMN IF NOT EXISTS notice_state TEXT")
    op.execute("ALTER TABLE public.recordings_s3 ADD COLUMN IF NOT EXISTS transfer_part TEXT")
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'recordings_s3_notice_state_check'
              AND conrelid = 'public.recordings_s3'::regclass
        ) THEN
            ALTER TABLE public.recordings_s3
                ADD CONSTRAINT recordings_s3_notice_state_check
                CHECK (notice_state IS NULL OR notice_state IN ('delivered', 'not_required', 'unconfirmed'));
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'recordings_s3_transfer_part_check'
              AND conrelid = 'public.recordings_s3'::regclass
        ) THEN
            ALTER TABLE public.recordings_s3
                ADD CONSTRAINT recordings_s3_transfer_part_check
                CHECK (transfer_part IS NULL OR transfer_part IN ('included', 'missing'));
        END IF;
    END
    $$
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("ALTER TABLE public.recordings_s3 DROP CONSTRAINT IF EXISTS recordings_s3_transfer_part_check")
    op.execute("ALTER TABLE public.recordings_s3 DROP CONSTRAINT IF EXISTS recordings_s3_notice_state_check")
    op.execute("ALTER TABLE public.recordings_s3 DROP COLUMN IF EXISTS transfer_part")
    op.execute("ALTER TABLE public.recordings_s3 DROP COLUMN IF EXISTS notice_state")
