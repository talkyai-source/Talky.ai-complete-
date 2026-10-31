"""Align the two historical DNC shapes without discarding opt-out evidence.

Source remains free text, matching the existing service/API contract. Unknown
constraints, conflicting indexes and duplicate evidence require review rather
than guessing which compliance record should survive.
"""
from alembic import op

revision = "0061_dnc_runtime_contract"
down_revision = "0060_dialer_active_job_owner"
branch_labels = None
depends_on = None

DNC_RUNTIME_CONTRACT = r"""
DO $dnc_contract$
DECLARE
    target oid := 'public.dnc_entries'::regclass;
    source_att smallint;
    existing record;
    spec record;
    named_index oid;
    expression text;
    source_values text[];
BEGIN
    PERFORM set_config('lock_timeout','5s',true);
    -- ALTERs need this lock too; take it once before preflight/data checks.
    LOCK TABLE public.dnc_entries IN ACCESS EXCLUSIVE MODE;
    SELECT attnum INTO source_att FROM pg_attribute
      WHERE attrelid=target AND attname='source' AND NOT attisdropped;
    IF source_att IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid=target AND attname='source'
          AND atttypid IN ('text'::regtype,'varchar'::regtype) AND attnotnull
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid=target AND attname='normalized_number'
          AND atttypid IN ('text'::regtype,'varchar'::regtype) AND attnotnull
    ) THEN
        RAISE EXCEPTION '0061: unsupported DNC source/number shape; inspect before retrying; no evidence removed';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=target AND NOT attisdropped AND (
        (attname='phone_number' AND atttypid NOT IN ('text'::regtype,'varchar'::regtype)) OR
        (attname='added_by' AND atttypid<>'uuid'::regtype) OR
        (attname='updated_at' AND atttypid<>'timestamptz'::regtype))) THEN
        RAISE EXCEPTION '0061: incompatible DNC compatibility column type; inspect before retrying; no evidence rewritten';
    END IF;

    -- Remove only the exact obsolete six-value source constraint. Other
    -- source restrictions require an explicit operator decision.
    FOR existing IN SELECT conname,pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE conrelid=target AND contype='c'
          AND source_att=ANY(conkey)
    LOOP
        expression := regexp_replace(existing.definition,
            '::(character varying|text)(\[\])?', '', 'g');
        expression := regexp_replace(expression, '[[:space:]()]', '', 'g');
        SELECT array_agg(m[1] ORDER BY m[1]) INTO source_values
          FROM regexp_matches(expression, '''([a-z_]+)''', 'g') AS m;
        IF regexp_replace(expression, '''[a-z_]+''', '', 'g') !~ '^CHECKsource=ANYARRAY\[,*\]$'
           OR source_values IS DISTINCT FROM ARRAY[
              'abuse_prevention','customer_request','government_list',
              'internal_list','litigation','manual']::text[] THEN
            RAISE EXCEPTION '0061: unrecognized DNC source constraint; inspect before retrying; no evidence removed';
        END IF;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',target::regclass,existing.conname);
    END LOOP;

    -- Source-specific keys allow a permanent caller opt-out alongside an
    -- independent manual/regulatory record. NULL tenant means global.
    FOR spec IN SELECT * FROM (VALUES
        ('uq_dnc_entries_tenant_number_source', ARRAY['tenant_id','normalized_number','source']::text[], 'tenant_idISNOTNULL'),
        ('uq_dnc_entries_global_number_source', ARRAY['normalized_number','source']::text[], 'tenant_idISNULL')
    ) AS definitions(name,keys,predicate)
    LOOP
        SELECT c.oid INTO named_index FROM pg_class c
          WHERE c.relnamespace=(SELECT relnamespace FROM pg_class WHERE oid=target)
            AND c.relname=spec.name;
        IF named_index IS NOT NULL THEN
            SELECT i.*,am.amname,
                   ARRAY(SELECT pg_get_indexdef(i.indexrelid,n,true)
                         FROM generate_series(1,i.indnkeyatts) AS n) AS keys,
                   regexp_replace(pg_get_expr(i.indpred,i.indrelid),'[[:space:]()]','','g') AS predicate
              INTO existing FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
              JOIN pg_am am ON am.oid=c.relam WHERE i.indexrelid=named_index;
            IF NOT FOUND OR existing.indrelid<>target OR NOT existing.indisunique
               OR NOT existing.indisvalid OR NOT existing.indisready OR NOT existing.indislive
               OR NOT existing.indimmediate OR existing.indnatts<>existing.indnkeyatts
               OR existing.keys IS DISTINCT FROM spec.keys OR existing.amname<>'btree'
               OR existing.predicate IS DISTINCT FROM spec.predicate THEN
                RAISE EXCEPTION '0061: named DNC index is invalid or incompatible; inspect before retrying; no evidence removed';
            END IF;
        END IF;
    END LOOP;

    FOR existing IN
        SELECT i.*,c.relname,k.conname,k.condeferrable,am.amname,
               ARRAY(SELECT pg_get_indexdef(i.indexrelid,n,true)
                     FROM generate_series(1,i.indnkeyatts) AS n) AS keys
          FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
          JOIN pg_am am ON am.oid=c.relam
          LEFT JOIN pg_constraint k ON k.conindid=i.indexrelid AND k.contype='u'
         WHERE i.indrelid=target AND i.indisunique AND NOT i.indisprimary
           AND c.relname NOT IN ('uq_dnc_entries_tenant_number_source','uq_dnc_entries_global_number_source')
    LOOP
        IF existing.conname IS DISTINCT FROM 'dnc_entries_tenant_id_normalized_number_key'
           OR existing.keys IS DISTINCT FROM ARRAY['tenant_id','normalized_number']::text[]
           OR existing.indpred IS NOT NULL OR existing.indexprs IS NOT NULL
           OR existing.indnatts<>2 OR NOT existing.indisvalid OR NOT existing.indisready
           OR NOT existing.indislive OR NOT existing.indimmediate OR existing.condeferrable
           OR existing.amname<>'btree' THEN
            RAISE EXCEPTION '0061: unrecognized DNC unique restriction; inspect before retrying; no evidence removed';
        END IF;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',target::regclass,existing.conname);
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.dnc_entries
        GROUP BY tenant_id,normalized_number,source HAVING count(*)>1) THEN
        RAISE EXCEPTION '0061: duplicate DNC source records require evidence review before retrying; no rows merged or deleted';
    END IF;

    -- Retain original created_by and all existing data/FKs/RLS. The added
    -- legacy phone column mirrors the authoritative stored normalized value.
    ALTER TABLE public.dnc_entries ADD COLUMN IF NOT EXISTS phone_number varchar(50);
    UPDATE public.dnc_entries SET phone_number=normalized_number WHERE phone_number IS NULL;
    ALTER TABLE public.dnc_entries ALTER COLUMN phone_number SET NOT NULL;
    IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=target
        AND attname='source' AND atttypid<>'text'::regtype) THEN
        ALTER TABLE public.dnc_entries ALTER COLUMN source TYPE text;
    END IF;
    ALTER TABLE public.dnc_entries ADD COLUMN IF NOT EXISTS added_by uuid
        REFERENCES public.user_profiles(id) ON DELETE SET NULL;
    ALTER TABLE public.dnc_entries ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
    -- If an existing timestamp column is nullable, retain its NULL evidence.
    -- New/bootstrap columns are NOT NULL; no historical update time is guessed.

    CREATE UNIQUE INDEX IF NOT EXISTS uq_dnc_entries_tenant_number_source
        ON public.dnc_entries(tenant_id,normalized_number,source) WHERE tenant_id IS NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dnc_entries_global_number_source
        ON public.dnc_entries(normalized_number,source) WHERE tenant_id IS NULL;
END $dnc_contract$;
"""


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(DNC_RUNTIME_CONTRACT)


def downgrade():
    # Older code can continue reading the retained records. Restoring the
    # source-blind key would require deleting independent compliance evidence.
    pass
