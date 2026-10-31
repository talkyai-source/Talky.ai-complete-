"""Attach durable Alertmanager episodes to existing human incident records."""
from alembic import op

revision = "0073_alertmanager_incidents"
down_revision = "0072_admin_incidents"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = (
    """ALTER TABLE incidents
        ADD COLUMN source_system varchar(64),
        ADD COLUMN source_fingerprint varchar(64),
        ADD COLUMN source_started_at timestamptz,
        ADD COLUMN source_status varchar(16),
        ADD COLUMN source_recovered_at timestamptz,
        ADD COLUMN source_last_received_at timestamptz,
        ADD COLUMN source_labels jsonb NOT NULL DEFAULT '{}'::jsonb,
        ADD CONSTRAINT incident_source_shape CHECK (
            (source_system IS NULL AND source_fingerprint IS NULL AND source_started_at IS NULL
             AND source_status IS NULL AND source_recovered_at IS NULL AND source_last_received_at IS NULL)
            OR (source_system IS NOT NULL AND source_fingerprint IS NOT NULL
                AND source_fingerprint ~ '^[0-9a-f]{16,64}$'
                AND source_started_at IS NOT NULL AND source_last_received_at IS NOT NULL
                AND source_status IS NOT NULL AND source_status IN ('firing','recovered')
                AND ((source_status='firing' AND source_recovered_at IS NULL)
                     OR (source_status='recovered' AND source_recovered_at IS NOT NULL
                         AND source_recovered_at >= source_started_at))
            )
        ),
        ADD CONSTRAINT incident_source_labels_object CHECK (jsonb_typeof(source_labels)='object')""",
    """CREATE UNIQUE INDEX incidents_source_episode_unique
       ON incidents(source_system,source_fingerprint,source_started_at)
       WHERE source_system IS NOT NULL""",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("LOCK TABLE incidents IN ACCESS EXCLUSIVE MODE")
    op.execute("SET LOCAL app.bypass_rls='on'")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM incidents WHERE source_system IS NOT NULL)
        THEN RAISE EXCEPTION 'Retain Alertmanager episode history during application rollback'; END IF; END $$""")
    op.execute("DROP INDEX incidents_source_episode_unique")
    op.execute("""ALTER TABLE incidents DROP CONSTRAINT incident_source_shape,
        DROP CONSTRAINT incident_source_labels_object, DROP COLUMN source_system,
        DROP COLUMN source_fingerprint, DROP COLUMN source_started_at,
        DROP COLUMN source_status, DROP COLUMN source_recovered_at,
        DROP COLUMN source_last_received_at, DROP COLUMN source_labels""")
