"""Close two legacy security gaps in the production lineage (QA F14-02 review).

- log_audit_event(...) is SECURITY DEFINER without a pinned search_path and
  inserts into audit_logs columns that do not exist, so it cannot work and
  nothing calls it. A definer-rights function with an unpinned search_path can
  be steered by whoever controls that path, so it is dropped.
- active_suspensions, expiring_secrets and open_security_events are plain
  views: they run with their owner's rights and bypass the row-level security
  of the tables beneath them (expiring_secrets returns encrypted secret
  columns). The application reads the tables directly. The views now run with
  the reader's rights (security_invoker), so the tables' policies apply.
Fresh installs without these objects are unchanged.
"""
from alembic import op

revision = "0094_harden_legacy_db_objects"
down_revision = "0093_abuse_event_columns"
branch_labels = None
depends_on = None

DEFINER_FUNCTION = ("log_audit_event(uuid, uuid, character varying, character varying, uuid, text, jsonb, "
                    "inet, text)")
OWNER_RIGHTS_VIEWS = ("active_suspensions", "expiring_secrets", "open_security_events")


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP FUNCTION IF EXISTS public.{DEFINER_FUNCTION}")
    for view in OWNER_RIGHTS_VIEWS:
        op.execute(f"""
        DO $q94$
        BEGIN
            IF (SELECT relkind FROM pg_catalog.pg_class WHERE oid = to_regclass('public.{view}')) = 'v' THEN
                ALTER VIEW public.{view} SET (security_invoker = true);
            END IF;
        END $q94$;
        """)


def downgrade():
    # The views go back to owner rights; the broken definer function is not
    # restored, since it could only reopen the gap.
    op.execute("SET LOCAL lock_timeout='5s'")
    for view in OWNER_RIGHTS_VIEWS:
        op.execute(f"""
        DO $q94$
        BEGIN
            IF (SELECT relkind FROM pg_catalog.pg_class WHERE oid = to_regclass('public.{view}')) = 'v' THEN
                ALTER VIEW public.{view} RESET (security_invoker);
            END IF;
        END $q94$;
        """)
