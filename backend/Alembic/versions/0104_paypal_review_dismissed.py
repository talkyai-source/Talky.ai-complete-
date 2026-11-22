"""Add the `dismissed` PayPal receipt state and a review log for operator decisions.

`dismissed` means a person reviewed a PayPal receipt stuck in `needs_review` (or
failed/received for more than 4 days) and closed it WITHOUT any business effect:
no minutes, plan or refund moves. `completed` keeps meaning that effects were
applied. The decision (operator, reason, the attempt count the operator saw) is
written to paypal_webhook_review_log, which holds PayPal decisions because the
Stripe log's event_id is a foreign key to the Stripe receipt table (0054).

Row security is enabled and forced with the platform-only policy 0101 gives the
receipt table; no GRANT is issued (the runtime role's rights come from
runtime_role_grants.sql).
"""
from alembic import op

revision = "0104_paypal_review_dismissed"
down_revision = "0103_recording_notice_state"
branch_labels = None
depends_on = None

BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"


def _drop_state_check():
    # The 0101 CHECK was declared inline on the column, so find it by content
    # rather than relying on the generated name.
    op.execute("""
    DO $q104$
    DECLARE con text;
    BEGIN
        FOR con IN
            SELECT conname FROM pg_catalog.pg_constraint
            WHERE conrelid = 'public.paypal_webhook_events'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%needs_review%'
        LOOP
            EXECUTE format('ALTER TABLE public.paypal_webhook_events DROP CONSTRAINT %I', con);
        END LOOP;
    END $q104$;
    """)


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    _drop_state_check()
    op.execute(
        "ALTER TABLE public.paypal_webhook_events ADD CONSTRAINT paypal_webhook_events_state_check "
        "CHECK (state IN ('received', 'completed', 'ignored', 'needs_review', 'failed', 'dismissed'))"
    )
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.paypal_webhook_review_log (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_id TEXT NOT NULL REFERENCES public.paypal_webhook_events(event_id),
        operator TEXT NOT NULL CHECK (length(operator) BETWEEN 1 AND 200),
        operator_id UUID,
        decision TEXT NOT NULL CHECK (decision IN ('dismiss')),
        reason TEXT NOT NULL CHECK (length(reason) BETWEEN 12 AND 2000),
        attempt_count_seen INTEGER NOT NULL CHECK (attempt_count_seen >= 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_paypal_webhook_review_log_event "
        "ON public.paypal_webhook_review_log (event_id, created_at)"
    )
    op.execute("ALTER TABLE public.paypal_webhook_review_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.paypal_webhook_review_log FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS paypal_webhook_review_log_platform_only ON public.paypal_webhook_review_log")
    op.execute(
        "CREATE POLICY paypal_webhook_review_log_platform_only ON public.paypal_webhook_review_log "
        f"FOR ALL USING ({BYPASS}) WITH CHECK ({BYPASS})"
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("UPDATE public.paypal_webhook_events SET state = 'needs_review' WHERE state = 'dismissed'")
    _drop_state_check()
    op.execute(
        "ALTER TABLE public.paypal_webhook_events ADD CONSTRAINT paypal_webhook_events_state_check "
        "CHECK (state IN ('received', 'completed', 'ignored', 'needs_review', 'failed'))"
    )
    op.execute("DROP POLICY IF EXISTS paypal_webhook_review_log_platform_only ON public.paypal_webhook_review_log")
    # 0101's downgrade drops the parent table, which this table references.
    op.execute("DROP TABLE IF EXISTS public.paypal_webhook_review_log")
