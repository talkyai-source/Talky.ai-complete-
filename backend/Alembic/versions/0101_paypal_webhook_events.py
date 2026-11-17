"""PayPal webhook receipts (ids and a hash only, never the event body).

PayPal delivers an event at least once and may repeat it. One row per PayPal
event id records what we did with it, so a repeat is answered without doing the
work twice and a stuck or suspicious event is visible to a person.

The event body carries the payer's name, email and address, so it is NOT stored:
only ids, the event type and a SHA-256 of the raw body. There is no tenant_id on
purpose: a webhook has no signed-in tenant. Row security is enabled and forced
with the same platform-only policy 0054 gives processed_webhook_events; no GRANT
is issued (the runtime role's rights come from runtime_role_grants.sql).
"""
from alembic import op

revision = "0101_paypal_webhook_events"
down_revision = "0100_force_legal_acceptance_rls"
branch_labels = None
depends_on = None

TABLE = "paypal_webhook_events"
POLICY = "paypal_webhook_events_platform_only"
INDEX = "idx_paypal_webhook_events_open"
BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.paypal_webhook_events (
        event_id      TEXT PRIMARY KEY,
        event_type    TEXT NOT NULL,
        resource_type TEXT,
        resource_id   TEXT,
        order_id      UUID,
        provider_mode TEXT NOT NULL CHECK (provider_mode IN ('test', 'live')),
        state         TEXT NOT NULL CHECK (state IN ('received', 'completed', 'ignored', 'needs_review', 'failed')),
        detail        TEXT,
        payload_hash  TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 1,
        received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at  TIMESTAMPTZ
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_paypal_webhook_events_open "
        "ON public.paypal_webhook_events (received_at) "
        "WHERE state IN ('needs_review', 'failed', 'received')"
    )
    op.execute("ALTER TABLE public.paypal_webhook_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.paypal_webhook_events FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS paypal_webhook_events_platform_only ON public.paypal_webhook_events")
    op.execute(
        "CREATE POLICY paypal_webhook_events_platform_only ON public.paypal_webhook_events "
        f"FOR ALL USING ({BYPASS}) WITH CHECK ({BYPASS})"
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("DROP POLICY IF EXISTS paypal_webhook_events_platform_only ON public.paypal_webhook_events")
    op.execute("DROP INDEX IF EXISTS public.idx_paypal_webhook_events_open")
    op.execute("DROP TABLE IF EXISTS public.paypal_webhook_events")
