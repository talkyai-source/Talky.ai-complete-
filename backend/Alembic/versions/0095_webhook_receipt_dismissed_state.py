"""Add the `dismissed` receipt state (QA F13-15 / F13-05).

`dismissed` means an operator reviewed a payment receipt stuck in
`needs_review` and closed it WITHOUT replay: no business effect was applied.
`completed` must keep meaning that effects were applied, so it cannot be reused.
processed_webhook_events is created only by migration 0054 (the bootstrap
schemas do not define it), so there is no second CHECK text to keep in step.
"""
from alembic import op

revision = "0095_webhook_receipt_dismissed"
down_revision = "0094_harden_legacy_db_objects"
branch_labels = None
depends_on = None

_STATES = "'legacy_unverified','pending','failed','completed','needs_review'"


def _drop_state_check():
    # The 0054 CHECK was declared inline on the column, so find it by content
    # rather than relying on the generated name.
    op.execute("""
    DO $q95$
    DECLARE con text;
    BEGIN
        FOR con IN
            SELECT conname FROM pg_catalog.pg_constraint
            WHERE conrelid = 'public.processed_webhook_events'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%legacy_unverified%'
        LOOP
            EXECUTE format('ALTER TABLE public.processed_webhook_events DROP CONSTRAINT %I', con);
        END LOOP;
    END $q95$;
    """)


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    _drop_state_check()
    op.execute(f"""ALTER TABLE public.processed_webhook_events
        ADD CONSTRAINT processed_webhook_events_state_check
        CHECK (state IN ({_STATES},'dismissed'))""")


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    # A dismissed receipt goes back to needing review; nothing is lost because
    # the dismissal itself stays in billing_webhook_review_log.
    op.execute("UPDATE public.processed_webhook_events SET state='needs_review' WHERE state='dismissed'")
    _drop_state_check()
    op.execute(f"""ALTER TABLE public.processed_webhook_events
        ADD CONSTRAINT processed_webhook_events_state_check
        CHECK (state IN ({_STATES}))""")
