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
        op.execute(text(f"ALTER POLICY {table}_tenant_isolation ON public.{table} "
                        f"USING ({scope}) WITH CHECK ({scope})"))
        op.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))


def upgrade() -> None:
    _apply(SCOPE)


def downgrade() -> None:
    # Restore the previous policy semantics; no ownership or row data changes.
    _apply(SCOPE + " OR tenant_id IS NULL")
