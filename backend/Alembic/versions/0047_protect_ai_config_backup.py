"""Put the leftover AI-config backup table behind the same tenant policy.

`verify_rls.py --posture` on production, 2026-09-22:

    tables with tenant_id : 82
    RLS enabled           : 81
    FAIL  every tenant-scoped table has RLS enabled
          [1 without: tenant_ai_configs_backup_20260907]

That table was taken during the 2026-09-07 model migration, when every tenant
row storing a retired model id was moved to cerebras/gpt-oss-120b. It holds
three rows covering three different tenants, has no foreign keys pointing at
it, and nothing in the application reads it. It is operational debris, and it
is the only tenant-scoped table on the database with no row-level security at
all: any tenant-scoped connection could read all three tenants' AI settings.

The right long-term answer is to drop it, but dropping someone's only copy of a
pre-migration state is not this migration's call to make. Protecting it closes
the isolation gap now and costs nothing, and recovery still works because the
canonical policy honours ``app.bypass_rls`` the way admin tooling already does.

Deliberately tolerant: the table may already have been dropped by hand, in
which case every statement here is a no-op rather than a failed deploy.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0047_protect_ai_config_backup"
down_revision: Union[str, None] = "0046_inbound_extension_bindings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "tenant_ai_configs_backup_20260907"
_POLICY = "tenant_ai_configs_backup_20260907_tenant_isolation"

# Byte-for-byte the expression 0038 installs on every other tenant-scoped
# table. Keeping it identical is the point: verify_rls.py counts DISTINCT
# policy shapes, and a near-miss here would read as policy sprawl.
_TENANT_POLICY = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = "
    "NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)


def _table_exists(conn) -> bool:
    return bool(
        conn.execute(
            text("SELECT to_regclass('public.' || :t) IS NOT NULL"),
            {"t": _TABLE},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn):
        return

    op.execute(text(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY"))
    # FORCE as well, or the owning role keeps reading straight past the policy.
    op.execute(text(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY"))
    op.execute(text(f"DROP POLICY IF EXISTS {_POLICY} ON public.{_TABLE}"))
    op.execute(
        text(
            f"CREATE POLICY {_POLICY} ON public.{_TABLE} "
            f"FOR ALL USING ({_TENANT_POLICY}) WITH CHECK ({_TENANT_POLICY})"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn):
        return
    op.execute(text(f"DROP POLICY IF EXISTS {_POLICY} ON public.{_TABLE}"))
    op.execute(text(f"ALTER TABLE public.{_TABLE} NO FORCE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE public.{_TABLE} DISABLE ROW LEVEL SECURITY"))
