"""Force row security on legal_acceptances.

0085 enabled row security on the table and created its tenant policy, but never
forced it, so the policy did not apply to the role that owns the table. Every
other table with a tenant_id column is forced. The clean-install test found
this: it requires every such table to be enabled AND forced.

Every reader and writer already runs inside ``acquire_with_tenant`` (the
tenant's own context in profile.py; the internal bypass at sign-up and invite
acceptance), which is exactly what the existing policy allows, so forcing it
changes nothing for the application. A table without a policy is never forced:
that would refuse every row.

Downgrade removes only the FORCE.
"""
from alembic import op

revision = "0100_force_legal_acceptance_rls"
down_revision = "0099_clean_install_tables"
branch_labels = None
depends_on = None

TABLE = "legal_acceptances"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute(f"""
    DO $f100$
    BEGIN
        IF to_regclass('public.{TABLE}') IS NULL THEN
            RAISE EXCEPTION '0100 expected {TABLE} table is missing';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = to_regclass('public.{TABLE}')) THEN
            RAISE EXCEPTION '0100 refuses to force row security on {TABLE} without a policy';
        END IF;
        ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY;
    END $f100$;
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"""
    DO $f100$
    BEGIN
        IF to_regclass('public.{TABLE}') IS NOT NULL THEN
            ALTER TABLE public.{TABLE} NO FORCE ROW LEVEL SECURITY;
        END IF;
    END $f100$;
    """)
