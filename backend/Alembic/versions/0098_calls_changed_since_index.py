"""Serve the call list's "changed since" read from an index (D5-20).

GET /calls/?changed_since= is read by the calls page on every refresh. It
filters one tenant's calls by ``updated_at`` and returns the newest change
first. The only tenant index on ``calls`` was ordered by ``created_at``, so
this read walked every call of the tenant and sorted them.

The index is in the order the statement asks for
(``ORDER BY updated_at DESC NULLS LAST, id DESC`` in calls.py). Nothing else
changes. An index of this name that this migration did not create is left
alone, on upgrade and on downgrade.
"""
from alembic import op

revision = "0098_calls_changed_since_index"
down_revision = "0097_remove_wizard_plan_line"
branch_labels = None
depends_on = None

INDEX = "idx_calls_tenant_updated_id"
OWNER = "created by 0098_calls_changed_since_index"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(f"""
    DO $d520$
    BEGIN
        IF to_regclass('public.calls') IS NULL THEN
            RAISE EXCEPTION '0098 expected calls table is missing';
        END IF;
        IF to_regclass('public.{INDEX}') IS NULL THEN
            CREATE INDEX {INDEX}
                ON public.calls (tenant_id, updated_at DESC NULLS LAST, id DESC);
            COMMENT ON INDEX public.{INDEX} IS '{OWNER}';
        END IF;
    END $d520$;
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"""
    DO $d520$
    BEGIN
        IF obj_description(to_regclass('public.{INDEX}'),'pg_class')='{OWNER}' THEN
            DROP INDEX public.{INDEX};
        END IF;
    END $d520$;
    """)
