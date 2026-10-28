"""Provide the durable incident store already required by Admin controls.

Rollback refuses to discard incident history. Export/retain records and keep
this additive table when rolling back application code after incidents exist.
The manually applied 20260706_add_admin_health_tables.sql is adopted only in its known shape;
existing identifiers and receipts must already satisfy the durable contract.
"""
from alembic import op

revision = "0072_admin_incidents"
down_revision = "0071_connector_lifecycle"
branch_labels = None
depends_on = None

CREATE_TABLE_SQL = """CREATE TABLE public.incidents (
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
    )"""

# Compare PostgreSQL-parsed catalog structure, not permissive IF NOT EXISTS or
# a version-dependent string rewrite. No existing table/owner/history is dropped.
ADOPT_OR_CREATE_SQL = """
DO $adopt$
DECLARE
    target oid := to_regclass('public.incidents');
    reference oid;
    relation oid;
    shape jsonb;
    expected jsonb;
BEGIN
    IF target IS NULL THEN
        """ + CREATE_TABLE_SQL + """;
        RETURN;
    END IF;
    LOCK TABLE public.incidents IN ACCESS EXCLUSIVE MODE;
    target := 'public.incidents'::regclass;
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid=target AND relkind='r'
        AND relpersistence='p' AND NOT relispartition AND NOT relrowsecurity
        AND NOT relforcerowsecurity AND relreplident='d' AND reloptions IS NULL)
       OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=target)
       OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=target)
       OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=target OR inhparent=target)
       OR EXISTS (SELECT 1 FROM pg_rewrite WHERE ev_class=target)
       OR EXISTS (SELECT 1 FROM pg_constraint WHERE confrelid=target)
    THEN
        RAISE EXCEPTION '0072 refuses unknown legacy incidents security or dependency shape';
    END IF;

    CREATE TEMPORARY TABLE admin_0072_incidents_reference (
        id text PRIMARY KEY, title text NOT NULL,
        severity text NOT NULL DEFAULT 'info', status text NOT NULL DEFAULT 'open',
        description text, triggered_at timestamptz DEFAULT now(),
        created_at timestamptz NOT NULL DEFAULT now(),
        acknowledged_at timestamptz, acknowledged_by text,
        resolved_at timestamptz, resolved_by text
    ) ON COMMIT DROP;
    CREATE INDEX admin_0072_status ON admin_0072_incidents_reference (status);
    CREATE INDEX admin_0072_severity ON admin_0072_incidents_reference (severity);
    CREATE INDEX admin_0072_triggered ON admin_0072_incidents_reference (triggered_at DESC);
    reference := 'pg_temp.admin_0072_incidents_reference'::regclass;
    FOREACH relation IN ARRAY ARRAY[reference, target] LOOP
        SELECT jsonb_build_object(
            'columns', (SELECT jsonb_agg(jsonb_build_array(
                a.attnum,a.attname,a.atttypid,a.atttypmod,a.attnotnull,
                a.attcollation,a.attidentity,a.attgenerated,a.attisdropped,
                pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
                FROM pg_attribute a LEFT JOIN pg_attrdef d
                  ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=relation AND a.attnum>0),
            'constraints', (SELECT jsonb_agg(value ORDER BY value) FROM (
                SELECT jsonb_build_array(contype,condeferrable,condeferred,
                    convalidated,pg_get_constraintdef(oid)) AS value
                FROM pg_constraint WHERE conrelid=relation) c),
            'indexes', (SELECT jsonb_agg(value ORDER BY value) FROM (
                SELECT jsonb_build_array(i.indisunique,i.indisprimary,i.indisexclusion,
                    i.indimmediate,i.indisvalid,i.indisready,i.indnkeyatts,i.indnatts,
                    i.indkey::text,i.indcollation::text,i.indclass::text,i.indoption::text,
                    pg_get_expr(i.indexprs,i.indrelid),pg_get_expr(i.indpred,i.indrelid),am.amname) AS value
                FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
                JOIN pg_am am ON am.oid=c.relam WHERE i.indrelid=relation) idx)
        ) INTO shape;
        IF relation=reference THEN
            expected := shape;
        ELSIF shape IS DISTINCT FROM expected THEN
            RAISE EXCEPTION '0072 refuses unknown legacy incidents columns, constraints or indexes';
        END IF;
    END LOOP;
    IF (SELECT array_agg(c.relname::text ORDER BY c.relname)
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid=target)
        IS DISTINCT FROM ARRAY['idx_incidents_severity','idx_incidents_status',
                               'idx_incidents_triggered_at','incidents_pkey']::text[]
       OR (SELECT array_agg(conname::text) FROM pg_constraint WHERE conrelid=target)
          IS DISTINCT FROM ARRAY['incidents_pkey']::text[] THEN
        RAISE EXCEPTION '0072 refuses unknown legacy incidents object identities';
    END IF;
    DROP TABLE pg_temp.admin_0072_incidents_reference;

    -- Casts reject malformed/duplicate UUID identities. NOT NULL and the same
    -- checks as the fresh path reject incomplete receipts; no values are invented.
    ALTER TABLE public.incidents
        ALTER COLUMN id TYPE uuid USING id::uuid,
        ALTER COLUMN id SET DEFAULT gen_random_uuid(),
        ALTER COLUMN acknowledged_by TYPE uuid USING acknowledged_by::uuid,
        ALTER COLUMN resolved_by TYPE uuid USING resolved_by::uuid,
        ALTER COLUMN triggered_at SET NOT NULL,
        ADD CONSTRAINT incidents_severity_check CHECK (severity IN ('critical','warning','info')),
        ADD CONSTRAINT incidents_status_check CHECK (status IN ('open','acknowledged','resolved')),
        ADD CONSTRAINT incident_ack_actor_time CHECK ((acknowledged_at IS NULL) = (acknowledged_by IS NULL)),
        ADD CONSTRAINT incident_resolve_actor_time CHECK ((resolved_at IS NULL) = (resolved_by IS NULL)),
        ADD CONSTRAINT incident_ack_receipt CHECK (status <> 'acknowledged' OR acknowledged_at IS NOT NULL),
        ADD CONSTRAINT incident_resolve_receipt CHECK (status <> 'resolved' OR resolved_at IS NOT NULL);
END;
$adopt$;
"""

UPGRADE_STATEMENTS = (
    ADOPT_OR_CREATE_SQL,
    "CREATE INDEX incidents_status_time_idx ON public.incidents (status, triggered_at DESC, id DESC)",
    "CREATE INDEX incidents_severity_status_idx ON public.incidents (severity, status)",
    "ALTER TABLE public.incidents ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.incidents FORCE ROW LEVEL SECURITY",
    """CREATE POLICY incidents_platform_only ON public.incidents
        USING (COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false))
        WITH CHECK (COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false))""",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE public.incidents IN ACCESS EXCLUSIVE MODE")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.incidents'::regclass
                   AND attname='created_at' AND NOT attisdropped) THEN
            RAISE EXCEPTION 'Refusing to drop the pre-existing July incidents table; retain it during application rollback';
        END IF;
        IF EXISTS (SELECT 1 FROM public.incidents) THEN
            RAISE EXCEPTION 'Refusing to drop durable incident history; retain this additive table during application rollback';
        END IF;
    END $$""")
    op.execute("DROP TABLE public.incidents")
