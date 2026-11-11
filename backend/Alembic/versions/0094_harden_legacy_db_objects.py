"""Close two legacy security gaps in the production lineage (QA F14-02 review).

- log_audit_event(...) is SECURITY DEFINER without a pinned search_path and
  inserts into audit_logs columns that do not exist, so it cannot work and
  nothing calls it. A definer-rights function with an unpinned search_path can
  be steered by whoever controls that path, so it is dropped.
- Every view in public (active_suspensions, billable_calls, expiring_secrets,
  open_security_events, user_effective_permissions, user_tenant_roles in
  production) is a plain view: it runs with its owner's rights and bypasses the
  row-level security of the tables beneath it (expiring_secrets returns
  encrypted secret columns). The application reads the tables directly. Every
  view now runs with the reader's rights (security_invoker), so the tables'
  policies apply; tests/integration/test_legacy_object_hardening_pg.py fails if
  a migrated view does not.
Fresh installs are hardened the same way.
"""
from alembic import op

revision = "0094_harden_legacy_db_objects"
down_revision = "0093_abuse_event_columns"
branch_labels = None
depends_on = None

DEFINER_FUNCTION = ("log_audit_event(uuid, uuid, character varying, character varying, uuid, text, jsonb, "
                    "inet, text)")


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP FUNCTION IF EXISTS public.{DEFINER_FUNCTION}")
    op.execute("""
    DO $q94$
    DECLARE view_name text;
    BEGIN
        FOR view_name IN
            SELECT relname FROM pg_catalog.pg_class
            WHERE relkind = 'v' AND relnamespace = 'public'::regnamespace ORDER BY relname
        LOOP
            EXECUTE format('ALTER VIEW public.%I SET (security_invoker = true)', view_name);
        END LOOP;
    END $q94$;
    """)


def downgrade():
    # The views go back to owner rights; the broken definer function is not
    # restored, since it could only reopen the gap.
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("""
    DO $q94$
    DECLARE view_name text;
    BEGIN
        FOR view_name IN
            SELECT relname FROM pg_catalog.pg_class
            WHERE relkind = 'v' AND relnamespace = 'public'::regnamespace ORDER BY relname
        LOOP
            EXECUTE format('ALTER VIEW public.%I RESET (security_invoker)', view_name);
        END LOOP;
    END $q94$;
    """)
