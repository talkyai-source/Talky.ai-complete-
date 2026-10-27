"""Repair the five known tenant policies skipped by a legacy false 0021 stamp.

The supported 0008 path runs 0013; the historical false-stamp repair path did
not. Recognize only the documented bootstrap or 0013 policy, never replace an
unknown policy. Already-canonical policies retain their identity and definition.
"""
from alembic import op
from sqlalchemy import text

revision = "0077_legacy_tenant_rls_repair"
down_revision = "0076_admin_user_operations"
branch_labels = None
depends_on = None

TABLES = ("calls", "campaigns", "connectors", "conversations", "leads")
LEGACY = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), ''), 'false')::BOOLEAN = TRUE"
    " OR tenant_id::text = NULLIF(current_setting('app.current_tenant_id', TRUE), '')"
)
CANONICAL = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)

# PostgreSQL parses the reference policies itself. Comparing pg_get_expr output
# avoids accepting arbitrary expressions through whitespace/parenthesis regexes
# and avoids depending on one PostgreSQL version's expression rendering.
REPAIR_SQL = f"""
DO $repair$
DECLARE
    table_name text;
    target record;
    existing record;
    policy_count integer;
    legacy_qual text;
    canonical_qual text;
    nullable_qual text;
    expected_qual text;
    repair_tables text[] := ARRAY[]::text[];
    force_tables text[] := ARRAY[]::text[];
    prior_timeout text := current_setting('lock_timeout');
BEGIN
    PERFORM set_config('lock_timeout', '5s', TRUE);
    CREATE TEMPORARY TABLE admin_0077_policy_reference (tenant_id uuid) ON COMMIT DROP;
    EXECUTE $policy$CREATE POLICY legacy ON admin_0077_policy_reference
        FOR ALL USING ({LEGACY})$policy$;
    EXECUTE $policy$CREATE POLICY canonical ON admin_0077_policy_reference
        FOR ALL USING ({CANONICAL}) WITH CHECK ({CANONICAL})$policy$;
    EXECUTE $policy$CREATE POLICY nullable_canonical ON admin_0077_policy_reference
        FOR ALL USING ({CANONICAL} OR tenant_id IS NULL)
        WITH CHECK ({CANONICAL} OR tenant_id IS NULL)$policy$;
    SELECT pg_get_expr(polqual, polrelid) INTO legacy_qual FROM pg_policy
        WHERE polrelid='pg_temp.admin_0077_policy_reference'::regclass AND polname='legacy';
    SELECT pg_get_expr(polqual, polrelid) INTO canonical_qual FROM pg_policy
        WHERE polrelid='pg_temp.admin_0077_policy_reference'::regclass AND polname='canonical';
    SELECT pg_get_expr(polqual, polrelid) INTO nullable_qual FROM pg_policy
        WHERE polrelid='pg_temp.admin_0077_policy_reference'::regclass AND polname='nullable_canonical';

    -- Validate every target before changing any policy. Fixed ordering and a
    -- bounded lock wait protect this catalog check against concurrent DDL.
    FOREACH table_name IN ARRAY ARRAY['calls','campaigns','connectors','conversations','leads']
    LOOP
        EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', table_name);
        SELECT c.oid, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
               a.atttypid, a.attnotnull INTO target
          FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
         WHERE c.oid=to_regclass('public.' || table_name)
           AND a.attname='tenant_id' AND a.attnum>0 AND NOT a.attisdropped;
        IF NOT FOUND OR target.relkind <> 'r' OR target.atttypid <> 'uuid'::regtype
           OR NOT target.relrowsecurity THEN
            RAISE EXCEPTION '0077 refuses unexpected table or tenant/RLS shape on %', table_name;
        END IF;
        SELECT count(*) INTO policy_count FROM pg_policy WHERE polrelid=target.oid;
        SELECT polname, polcmd, polpermissive, polroles,
               pg_get_expr(polqual,polrelid) AS qual,
               pg_get_expr(polwithcheck,polrelid) AS with_check INTO existing
          FROM pg_policy WHERE polrelid=target.oid;
        IF policy_count <> 1 OR existing.polname <> table_name || '_tenant_isolation'
           OR existing.polcmd <> '*' OR NOT existing.polpermissive
           OR existing.polroles <> ARRAY[0]::oid[] THEN
            RAISE EXCEPTION '0077 refuses unknown policy ownership on %', table_name;
        END IF;
        expected_qual := CASE WHEN target.attnotnull THEN canonical_qual ELSE nullable_qual END;
        IF existing.qual IS NOT DISTINCT FROM expected_qual
           AND existing.with_check IS NOT DISTINCT FROM expected_qual THEN
            -- Preserve the parsed policy and its OID, including nullable 0013
            -- semantics. Only a missing FORCE flag needs correction.
            NULL;
        ELSIF target.attnotnull AND existing.qual IS NOT DISTINCT FROM legacy_qual
              AND existing.with_check IS NULL THEN
            repair_tables := array_append(repair_tables, table_name);
        ELSE
            RAISE EXCEPTION '0077 refuses unknown policy semantics on %', table_name;
        END IF;
        IF NOT target.relforcerowsecurity THEN
            force_tables := array_append(force_tables, table_name);
        END IF;
    END LOOP;

    FOREACH table_name IN ARRAY repair_tables LOOP
        EXECUTE format('DROP POLICY %I ON public.%I', table_name || '_tenant_isolation', table_name);
        EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL USING (%s) WITH CHECK (%s)',
                       table_name || '_tenant_isolation', table_name, '{CANONICAL.replace("'", "''")}',
                       '{CANONICAL.replace("'", "''")}');
    END LOOP;
    FOREACH table_name IN ARRAY force_tables LOOP
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;
    DROP TABLE pg_temp.admin_0077_policy_reference;
    PERFORM set_config('lock_timeout', prior_timeout, TRUE);
END;
$repair$;
"""


def upgrade():
    op.execute(text(REPAIR_SQL))


def downgrade():
    raise RuntimeError("Refusing to downgrade 0077: tenant isolation repair is forward-only")
