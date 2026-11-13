"""Support tenant-owned call history continuation without offset scanning."""
from alembic import op

revision = "0091_call_history_cursor"
down_revision = "0090_campaign_alerts"
branch_labels = None
depends_on = None
OWNER = "created by 0091_call_history_cursor"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(f"""
    DO $q39$
    DECLARE target oid; existing oid; expected smallint[];
    BEGIN
        target := to_regclass('public.calls');
        IF target IS NULL THEN
            RAISE EXCEPTION '0091 expected calls table is missing';
        END IF;
        LOCK TABLE public.calls IN SHARE ROW EXCLUSIVE MODE;
        SELECT array_agg(attnum ORDER BY array_position(ARRAY['tenant_id','created_at','id'],attname::text))
            INTO expected FROM pg_catalog.pg_attribute
            WHERE attrelid=target AND attname IN ('tenant_id','created_at','id') AND NOT attisdropped;
        IF cardinality(expected) IS DISTINCT FROM 3 THEN
            RAISE EXCEPTION '0091 expected call cursor columns are missing';
        END IF;
        existing := to_regclass('public.idx_calls_tenant_created_id');
        IF existing IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_index i
                JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
                JOIN pg_catalog.pg_am am ON am.oid=c.relam
                WHERE i.indexrelid=existing AND i.indrelid=target
                  AND am.amname='btree' AND i.indisvalid AND i.indisready
                  AND i.indpred IS NULL AND i.indexprs IS NULL
                  AND i.indnkeyatts=3 AND i.indnatts=3
                  AND ARRAY(SELECT a FROM unnest(i.indkey) WITH ORDINALITY k(a,n) ORDER BY n)=expected
                  AND ARRAY(SELECT a FROM unnest(i.indoption) WITH ORDINALITY k(a,n) ORDER BY n)=ARRAY[0,3,3]::smallint[]
            ) THEN
                RAISE EXCEPTION '0091 incompatible call cursor index';
            END IF;
        ELSE
            CREATE INDEX IF NOT EXISTS idx_calls_tenant_created_id
                ON public.calls (tenant_id, created_at DESC, id DESC);
            COMMENT ON INDEX public.idx_calls_tenant_created_id IS '{OWNER}';
        END IF;
    END $q39$;
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"""
    DO $q39$
    BEGIN
        IF obj_description(to_regclass('public.idx_calls_tenant_created_id'),'pg_class')='{OWNER}' THEN
            DROP INDEX public.idx_calls_tenant_created_id;
        END IF;
    END $q39$;
    """)
