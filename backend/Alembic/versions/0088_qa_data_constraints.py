"""Bound proven minute/status values and index missing foreign-key prefixes.

Historical invalid rows are retained: all new CHECKs remain NOT VALID. Only
objects created by this revision receive its ownership comment; downgrade
does not remove compatible constraints/indexes that predated this revision.
"""
from alembic import op


revision = "0088_qa_data_constraints"
down_revision = "0087_bootstrap_manual_parity"
branch_labels = None
depends_on = None

OWNER = "created by 0088_qa_data_constraints"
CAMPAIGN_STATES = ("draft", "scheduled", "running", "paused", "completed", "cancelled", "stopped", "deleted")
LEAD_STATES = ("pending", "calling", "called", "contacted", "completed", "failed", "dnc", "deleted", "skipped")


def _literal(value):
    # All identifiers/expressions below are fixed migration source, never input.
    return "'" + value.replace("'", "''") + "'"


CHECKS = (
    ("plans", "minutes", "q31_plans_minutes_nonnegative", "minutes >= 0"),
    ("tenants", "minutes_allocated", "q31_tenants_minutes_allocated_nonnegative", "minutes_allocated >= 0"),
    ("tenants", "minutes_used", "q31_tenants_minutes_used_nonnegative", "minutes_used >= 0"),
    ("tenant_call_limits", "monthly_minutes_allocated", "q31_limits_minutes_allocated_nonnegative", "monthly_minutes_allocated >= 0"),
    ("tenant_call_limits", "monthly_minutes_used", "q31_limits_minutes_used_nonnegative", "monthly_minutes_used >= 0"),
    ("campaigns", "status", "q31_campaigns_status_valid", "status IN (" + ",".join(map(_literal, CAMPAIGN_STATES)) + ")"),
    ("leads", "status", "q31_leads_status_valid", "status IN (" + ",".join(map(_literal, LEAD_STATES)) + ")"),
)

# Equality on every FK column may use any permutation of that leading set.
# INCLUDE attributes, partial/expression indexes and invalid indexes cannot
# establish full coverage for the parent-delete/FK equality lookup.
_COVERED = """
    SELECT 1 FROM pg_catalog.pg_index i
    JOIN pg_catalog.pg_class ix ON ix.oid=i.indexrelid
    JOIN pg_catalog.pg_am am ON am.oid=ix.relam
    WHERE i.indrelid=f.conrelid AND am.amname='btree'
      AND i.indisvalid AND i.indisready AND i.indislive
      AND i.indpred IS NULL AND i.indexprs IS NULL
      AND i.indnkeyatts >= cardinality(f.conkey)
      AND ARRAY(SELECT k.att FROM unnest(i.indkey) WITH ORDINALITY k(att,pos)
                WHERE k.pos <= cardinality(f.conkey) ORDER BY k.att)
          = ARRAY(SELECT a FROM unnest(f.conkey) a ORDER BY a)
"""

FK_INDEX_GAPS_SQL = f"""
    SELECT f.oid AS constraint_oid, f.conrelid AS table_oid,
           n.nspname AS schema_name, t.relname AS table_name,
           f.conname AS constraint_name,
           ARRAY(SELECT a.attname::text FROM unnest(f.conkey) WITH ORDINALITY k(att,pos)
                 JOIN pg_catalog.pg_attribute a ON a.attrelid=f.conrelid AND a.attnum=k.att
                 WHERE NOT a.attisdropped ORDER BY k.pos) AS column_names
    FROM pg_catalog.pg_constraint f
    JOIN pg_catalog.pg_class t ON t.oid=f.conrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
    WHERE f.contype='f' AND f.conparentid=0
      AND n.nspname='public' AND t.relkind IN ('r','p')
      AND NOT EXISTS ({_COVERED})
    ORDER BY n.nspname,t.relname,f.conname
"""


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    for table, column, name, expression in CHECKS:
        op.execute(f"""
        DO $q31$
        DECLARE target oid; actual record; expected text;
        BEGIN
            target := to_regclass({_literal('public.' + table)});
            IF target IS NULL OR NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute
                WHERE attrelid=target AND attname={_literal(column)} AND NOT attisdropped
            ) THEN
                RAISE EXCEPTION '0088 expected table/column missing: {table}.{column}';
            END IF;
            LOCK TABLE public."{table}" IN ACCESS EXCLUSIVE MODE;
            -- Parse the expected expression with the real column types/order.
            -- Compare catalog expressions rather than whitespace or CHECK names.
            CREATE TEMP TABLE q31_check_reference (LIKE public."{table}") ON COMMIT DROP;
            ALTER TABLE pg_temp.q31_check_reference ADD CONSTRAINT q31_expected
                CHECK ({expression}) NOT VALID;
            SELECT pg_get_expr(conbin,conrelid) INTO expected FROM pg_catalog.pg_constraint
                WHERE conrelid=to_regclass('pg_temp.q31_check_reference') AND conname='q31_expected';
            SELECT contype,connoinherit,pg_get_expr(conbin,conrelid) AS expression
                INTO actual FROM pg_catalog.pg_constraint
                WHERE conrelid=target AND conname={_literal(name)};
            IF FOUND THEN
                IF actual.contype<>'c' OR actual.connoinherit
                   OR actual.expression IS DISTINCT FROM expected THEN
                    RAISE EXCEPTION '0088 incompatible constraint: {table}.{name}';
                END IF;
            ELSE
                ALTER TABLE public."{table}" ADD CONSTRAINT "{name}"
                    CHECK ({expression}) NOT VALID;
                COMMENT ON CONSTRAINT "{name}" ON public."{table}" IS {_literal(OWNER)};
            END IF;
            DROP TABLE pg_temp.q31_check_reference;
        END $q31$;
        """)
    op.execute(f"""
    DO $q31$
    DECLARE candidate record; index_name text; columns_sql text;
    BEGIN
        FOR candidate IN {FK_INDEX_GAPS_SQL} LOOP
            -- Serialize index DDL for this table while allowing ordinary reads.
            -- This prevents marking another writer's concurrent index as ours.
            EXECUTE format('LOCK TABLE %I.%I IN SHARE ROW EXCLUSIVE MODE',
                           candidate.schema_name,candidate.table_name);
            IF EXISTS (
                SELECT 1 FROM pg_catalog.pg_constraint f
                WHERE f.oid=candidate.constraint_oid AND NOT EXISTS ({_COVERED})
            ) THEN
                index_name := 'q31_fk_' || substr(md5(candidate.schema_name || '.' ||
                    candidate.table_name || ':' || array_to_string(candidate.column_names,',')),1,24);
                IF to_regclass(format('%I.%I',candidate.schema_name,index_name)) IS NOT NULL THEN
                    RAISE EXCEPTION '0088 incompatible index name: %.%',candidate.schema_name,index_name;
                END IF;
                SELECT string_agg(format('%I',col),',' ORDER BY pos) INTO columns_sql
                    FROM unnest(candidate.column_names) WITH ORDINALITY c(col,pos);
                EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.%I USING btree (%s)',
                    index_name,candidate.schema_name,candidate.table_name,columns_sql);
                -- A same-name object on a different table is never accepted.
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_index i
                    WHERE i.indexrelid=to_regclass(format('%I.%I',candidate.schema_name,index_name))
                      AND i.indrelid=candidate.table_oid AND i.indisvalid AND i.indisready
                ) THEN
                    RAISE EXCEPTION '0088 incompatible index collision: %.%',candidate.schema_name,index_name;
                END IF;
                EXECUTE format('COMMENT ON INDEX %I.%I IS %L',
                    candidate.schema_name,index_name,{_literal(OWNER)});
            END IF;
        END LOOP;
    END $q31$;
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    for table, _, name, _ in reversed(CHECKS):
        op.execute(f"""
        DO $q31$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                       WHERE conrelid=to_regclass({_literal('public.' + table)})
                         AND conname={_literal(name)}
                         AND obj_description(oid,'pg_constraint')={_literal(OWNER)}) THEN
                ALTER TABLE public."{table}" DROP CONSTRAINT "{name}";
            END IF;
        END $q31$;
        """)
    op.execute(f"""
    DO $q31$
    DECLARE owned record;
    BEGIN
        FOR owned IN
            SELECT n.nspname,c.relname FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('i','I')
              AND starts_with(c.relname,'q31_fk_')
              AND obj_description(c.oid,'pg_class')={_literal(OWNER)}
        LOOP
            EXECUTE format('DROP INDEX %I.%I',owned.nspname,owned.relname);
        END LOOP;
    END $q31$;
    """)
