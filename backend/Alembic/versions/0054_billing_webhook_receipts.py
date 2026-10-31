"""Recoverable webhook receipts and separate delivery/review records.

The old processed_at value records a claim, not proved business completion.
Never infer completed work or replay permission from those retained rows.
"""

from alembic import op
from sqlalchemy import text

revision = "0054_billing_webhook_receipts"
down_revision = "0053_billing_price_options"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    # This table used to be installed by a standalone SQL migration. Fresh
    # Alembic installs need it too; existing claim identities must survive.
    op.execute(text("""CREATE TABLE IF NOT EXISTS public.processed_webhook_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT,
        processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )"""))
    op.execute(text("""ALTER TABLE public.processed_webhook_events
        ADD COLUMN state TEXT NOT NULL DEFAULT 'legacy_unverified'
            CHECK (state IN ('legacy_unverified','pending','failed','completed','needs_review')),
        ADD COLUMN event_payload JSONB,
        ADD COLUMN legacy_claim BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN payload_hash TEXT,
        ADD COLUMN provider_mode TEXT CHECK (provider_mode IN ('test','live')),
        ADD COLUMN received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ADD COLUMN completed_at TIMESTAMPTZ,
        ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        ADD COLUMN last_error_code TEXT,
        ADD COLUMN tenant_id UUID
    """))
    op.execute(text("""CREATE INDEX billing_webhook_receipt_recovery
        ON public.processed_webhook_events (state, received_at)
        WHERE state <> 'completed'"""))
    op.execute(text("""CREATE TABLE public.billing_webhook_notifications (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        delivery_key TEXT NOT NULL UNIQUE,
        event_id TEXT NOT NULL REFERENCES public.processed_webhook_events(event_id),
        event_type TEXT NOT NULL,
        tenant_id UUID,
        kind TEXT NOT NULL,
        recipient TEXT,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','sending','accepted','failed_before_send','unknown','recipient_missing','superseded')),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        last_error_code TEXT,
        provider_message_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at TIMESTAMPTZ,
        accepted_at TIMESTAMPTZ
    )"""))
    op.execute(text("""CREATE INDEX billing_webhook_notification_pending
        ON public.billing_webhook_notifications (status, created_at)
        WHERE status IN ('pending','failed_before_send')"""))
    op.execute(text("""CREATE TABLE public.billing_webhook_review_log (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_id TEXT NOT NULL REFERENCES public.processed_webhook_events(event_id),
        operator TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )"""))
    op.execute(text("""CREATE INDEX billing_webhook_review_event
        ON public.billing_webhook_review_log (event_id, created_at)"""))
    bypass = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    for table in (
        "processed_webhook_events", "billing_webhook_notifications", "billing_webhook_review_log",
    ):
        op.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        op.execute(text(f"""CREATE POLICY {table}_platform_only ON public.{table}
            FOR ALL USING ({bypass}) WITH CHECK ({bypass})"""))


def downgrade():
    raise RuntimeError(
        "Billing event, delivery and review identities must be retained; use a reviewed forward migration"
    )
