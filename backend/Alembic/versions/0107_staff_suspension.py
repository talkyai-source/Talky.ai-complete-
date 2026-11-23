"""Record that a suspension was placed by staff, so a won payment dispute cannot lift it.

Two nullable columns on `tenants`: `staff_suspended_at` and `staff_suspended_by`. Staff Suspend
sets them; staff Resume and Archive clear them. A won or closed dispute lifts only a suspension
that carries no staff mark. No default, no backfill, no constraint: every existing row keeps its
meaning, and code that does not know the columns ignores them, so rolling the code back without
this downgrade is safe.
"""
from alembic import op

revision = "0107_staff_suspension"
down_revision = "0106_outbound_notice_placement"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS staff_suspended_at TIMESTAMPTZ")
    op.execute("ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS staff_suspended_by UUID")


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("ALTER TABLE public.tenants DROP COLUMN IF EXISTS staff_suspended_by")
    op.execute("ALTER TABLE public.tenants DROP COLUMN IF EXISTS staff_suspended_at")
