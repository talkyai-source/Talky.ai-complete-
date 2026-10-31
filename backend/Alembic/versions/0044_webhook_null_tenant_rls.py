"""Make unowned webhook rows platform-only without deleting global records.

Revision ID: 0044_webhook_null_tenant_rls
Revises: 0043_campaign_direction_lock

Nullable ownership is not permission for every tenant to read or mutate a row.
Platform workers retain access through acquire_with_tenant(pool, None).
"""
from alembic import op
from sqlalchemy import text

revision = "0044_webhook_null_tenant_rls"
down_revision = "0043_campaign_direction_lock"
branch_labels = None
depends_on = None

TABLES = ("webhook_endpoints", "webhook_deliveries")
SCOPE = """COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
    OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"""


def _apply(scope: str) -> None:
    for table in TABLES:
        op.execute(text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON public.{table}"))
        op.execute(text(f"CREATE POLICY {table}_tenant_isolation ON public.{table} FOR ALL "
                        f"USING ({scope}) WITH CHECK ({scope})"))
        op.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))


def upgrade() -> None:
    # These tables originated in the manual 20260706 SQL migration, not the
    # Alembic baseline. Own that schema here so clean installs and existing
    # deployments converge; skipping absent tables would leave the API broken.
    op.execute(text("""CREATE TABLE IF NOT EXISTS public.webhook_endpoints (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
        url TEXT NOT NULL, events JSONB NOT NULL DEFAULT '[]'::jsonb,
        active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )"""))
    op.execute(text("""CREATE TABLE IF NOT EXISTS public.webhook_deliveries (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), webhook_id UUID, tenant_id UUID,
        event TEXT, status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )"""))
    for table, column, suffix in (("webhook_endpoints", "tenant_id", "tenant"),
                                  ("webhook_deliveries", "tenant_id", "tenant"),
                                  ("webhook_deliveries", "webhook_id", "webhook")):
        op.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{table}_{suffix} ON public.{table} ({column})"))
    _apply(SCOPE)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade 0044_webhook_null_tenant_rls: production migrations "
        "are forward-only; ship a compensating migration instead"
    )
