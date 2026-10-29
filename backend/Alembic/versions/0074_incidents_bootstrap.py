"""Put the existing admin incident contract on the supported bootstrap path.

The incidents table previously existed only in manually applied SQL, leaving
fresh canonical-schema/Alembic installations unable to persist or list it.
Create it only when absent; preserve existing operator data and ACLs unchanged.
New tables use the same platform-only RLS boundary as other platform records.
"""

from alembic import op

revision = "0074_incidents_bootstrap"
down_revision = "0073_stream_events_bootstrap"
branch_labels = None
depends_on = None

CREATE_MISSING_SQL = """
DO $incidents_bootstrap$
BEGIN
    IF to_regclass('public.incidents') IS NOT NULL THEN
        RETURN;
    END IF;

    CREATE TABLE public.incidents (
        id text PRIMARY KEY,
        title text NOT NULL,
        severity text NOT NULL DEFAULT 'info',
        status text NOT NULL DEFAULT 'open',
        description text,
        triggered_at timestamptz DEFAULT now(),
        created_at timestamptz NOT NULL DEFAULT now(),
        acknowledged_at timestamptz,
        acknowledged_by text,
        resolved_at timestamptz,
        resolved_by text
    );
    CREATE INDEX idx_incidents_status ON public.incidents (status);
    CREATE INDEX idx_incidents_severity ON public.incidents (severity);
    CREATE INDEX idx_incidents_triggered_at ON public.incidents (triggered_at DESC);
    COMMENT ON TABLE public.incidents IS
        'Persisted platform-wide operator incidents; restricted to platform context.';

    ALTER TABLE public.incidents ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.incidents FORCE ROW LEVEL SECURITY;
    CREATE POLICY incidents_platform_only ON public.incidents FOR ALL
        USING (COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false))
        WITH CHECK (COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false));
END
$incidents_bootstrap$;
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(CREATE_MISSING_SQL)


def downgrade() -> None:
    # This is an older application contract and may contain operator evidence.
    # Never drop the table or erase its rows during a migration rollback.
    pass
