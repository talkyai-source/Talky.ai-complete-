"""Record observed provider key validity without certifying existing keys.

NULL/NULL means not checked. Activation and encrypted material are unchanged.
Only objects created here carry the ownership marker used by downgrade.
"""
from alembic import op


revision = "0089_provider_key_validation"
down_revision = "0088_qa_data_constraints"
branch_labels = None
depends_on = None
OWNER = "created by 0089_provider_key_validation"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    for column, type_name in (("validation_status", "text"), ("last_checked_at", "timestamp with time zone")):
        op.execute(f"""
        DO $q34$
        DECLARE target oid; existing record;
        BEGIN
            target := to_regclass('public.tenant_ai_credentials');
            IF target IS NULL THEN
                RAISE EXCEPTION '0089 expected tenant_ai_credentials table is missing';
            END IF;
            LOCK TABLE public.tenant_ai_credentials IN ACCESS EXCLUSIVE MODE;
            SELECT a.atttypid,a.attnotnull,d.oid AS default_oid INTO existing
                FROM pg_catalog.pg_attribute a
                LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=target AND a.attname='{column}' AND NOT a.attisdropped;
            IF FOUND THEN
                IF existing.atttypid<>to_regtype('{type_name}') OR existing.attnotnull
                   OR existing.default_oid IS NOT NULL THEN
                    RAISE EXCEPTION '0089 incompatible metadata column: {column}';
                END IF;
            ELSE
                ALTER TABLE public.tenant_ai_credentials
                    ADD COLUMN IF NOT EXISTS {column} {type_name};
                COMMENT ON COLUMN public.tenant_ai_credentials.{column} IS '{OWNER}';
            END IF;
        END $q34$;
        """)
    op.execute(f"""
    DO $q34$
    DECLARE target oid; expected text; actual record;
    BEGIN
        target := to_regclass('public.tenant_ai_credentials');
        CREATE TEMP TABLE q34_validation_reference
            (validation_status text,last_checked_at timestamptz) ON COMMIT DROP;
        ALTER TABLE pg_temp.q34_validation_reference ADD CONSTRAINT q34_expected
            CHECK ((validation_status IS NULL AND last_checked_at IS NULL)
                OR (validation_status IS NOT NULL AND validation_status IN ('valid','invalid') AND last_checked_at IS NOT NULL)) NOT VALID;
        SELECT pg_get_expr(conbin,conrelid) INTO expected FROM pg_catalog.pg_constraint
            WHERE conrelid=to_regclass('pg_temp.q34_validation_reference') AND conname='q34_expected';
        SELECT contype,connoinherit,pg_get_expr(conbin,conrelid) AS expression INTO actual
            FROM pg_catalog.pg_constraint
            WHERE conrelid=target AND conname='tenant_ai_credentials_validation_observation';
        IF FOUND THEN
            IF actual.contype<>'c' OR actual.connoinherit OR actual.expression IS DISTINCT FROM expected THEN
                RAISE EXCEPTION '0089 incompatible validation observation constraint';
            END IF;
        ELSE
            ALTER TABLE public.tenant_ai_credentials ADD CONSTRAINT tenant_ai_credentials_validation_observation
                CHECK ((validation_status IS NULL AND last_checked_at IS NULL)
                    OR (validation_status IS NOT NULL AND validation_status IN ('valid','invalid') AND last_checked_at IS NOT NULL)) NOT VALID;
            COMMENT ON CONSTRAINT tenant_ai_credentials_validation_observation ON public.tenant_ai_credentials
                IS '{OWNER}';
        END IF;
        DROP TABLE pg_temp.q34_validation_reference;
    END $q34$;
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    op.execute(f"""
    DO $q34$
    DECLARE target oid;
    BEGIN
        target := to_regclass('public.tenant_ai_credentials');
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid=target
                   AND conname='tenant_ai_credentials_validation_observation'
                   AND obj_description(oid,'pg_constraint')='{OWNER}') THEN
            ALTER TABLE public.tenant_ai_credentials DROP CONSTRAINT tenant_ai_credentials_validation_observation;
        END IF;
    END $q34$;
    """)
    for column in ("last_checked_at", "validation_status"):
        op.execute(f"""
        DO $q34$
        DECLARE target oid;
        BEGIN
            target := to_regclass('public.tenant_ai_credentials');
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=target
                       AND attname='{column}' AND NOT attisdropped
                       AND col_description(attrelid,attnum)='{OWNER}') THEN
                ALTER TABLE public.tenant_ai_credentials DROP COLUMN {column};
            END IF;
        END $q34$;
        """)
