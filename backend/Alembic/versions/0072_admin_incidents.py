"""Provide the durable incident store already required by Admin controls.

Rollback refuses to discard incident history. Export/retain records and keep
this additive table when rolling back application code after incidents exist.
"""
from alembic import op

revision = "0072_admin_incidents"
down_revision = "0071_connector_lifecycle"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = (
    """CREATE TABLE incidents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        title text NOT NULL,
        severity text NOT NULL CHECK (severity IN ('critical','warning','info')),
        status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved')),
        description text,
        triggered_at timestamptz NOT NULL DEFAULT NOW(),
        acknowledged_at timestamptz,
        acknowledged_by uuid,
        resolved_at timestamptz,
        resolved_by uuid,
        CONSTRAINT incident_ack_actor_time CHECK ((acknowledged_at IS NULL) = (acknowledged_by IS NULL)),
        CONSTRAINT incident_resolve_actor_time CHECK ((resolved_at IS NULL) = (resolved_by IS NULL)),
        CONSTRAINT incident_ack_receipt CHECK (status <> 'acknowledged' OR acknowledged_at IS NOT NULL),
        CONSTRAINT incident_resolve_receipt CHECK (status <> 'resolved' OR resolved_at IS NOT NULL)
    )""",
    "CREATE INDEX incidents_status_time_idx ON incidents (status, triggered_at DESC, id DESC)",
    "CREATE INDEX incidents_severity_status_idx ON incidents (severity, status)",
    "ALTER TABLE incidents ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE incidents FORCE ROW LEVEL SECURITY",
    """CREATE POLICY incidents_platform_only ON incidents
        USING (COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false))
        WITH CHECK (COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false))""",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE incidents IN ACCESS EXCLUSIVE MODE")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM incidents) THEN
            RAISE EXCEPTION 'Refusing to drop durable incident history; retain this additive table during application rollback';
        END IF;
    END $$""")
    op.execute("DROP TABLE incidents")
