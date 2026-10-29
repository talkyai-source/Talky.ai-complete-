"""Restore the event stream on databases built from the canonical 0008 floor.

The supported complete_schema.sql -> stamp 0008 -> upgrade path skipped the
original table creator (0003). Revision 0065 intentionally accepts an absent
table, so reaching 0072 did not ensure that event emission or retention worked.
Create the missing relation with the current category and canonical RLS
contracts. Existing installations retain their table, data, grants and policies.
"""

from alembic import op

revision = "0073_stream_events_bootstrap"
down_revision = "0072_allowance_anchor"
branch_labels = None
depends_on = None

CREATE_MISSING_SQL = """
DO $stream_events_bootstrap$
BEGIN
    IF to_regclass('public.stream_events') IS NOT NULL THEN
        RETURN;
    END IF;

    CREATE TABLE public.stream_events (
        id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
        category text NOT NULL CONSTRAINT stream_events_category_check
            CHECK (category IN ('campaign', 'system', 'alert', 'user_action', 'milestone', 'call')),
        title text NOT NULL,
        description text,
        severity text CONSTRAINT stream_events_severity_check
            CHECK (severity IS NULL OR severity IN ('info', 'warning', 'critical')),
        related_campaign_id uuid,
        related_call_id uuid,
        actor_user_id uuid,
        metadata jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL DEFAULT (now() + interval '90 days')
    );
    CREATE INDEX idx_se_tenant_recent ON public.stream_events (tenant_id, created_at DESC);
    CREATE INDEX idx_se_tenant_category ON public.stream_events (tenant_id, category, created_at DESC);
    CREATE INDEX idx_se_expires ON public.stream_events (expires_at);

    ALTER TABLE public.stream_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.stream_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY stream_events_tenant_isolation ON public.stream_events FOR ALL
        USING (
            COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false)
            OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false)
            OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        );
END
$stream_events_bootstrap$;
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(CREATE_MISSING_SQL)


def downgrade() -> None:
    # The table belongs to the older 0003 application contract. It may also
    # predate this revision and contain retained events; never drop it here.
    pass
