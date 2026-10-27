"""Retain the reviewed version of existing enquiry handling decisions."""
from alembic import op

revision = "0074_contact_enquiry_revision"
down_revision = "0073_alertmanager_incidents"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""ALTER TABLE public.public_contact_enquiries
        ADD COLUMN revision INTEGER NOT NULL DEFAULT 0
        CHECK (revision >= 0)""")


def downgrade():
    raise RuntimeError("Enquiry decision versions must be retained; use a reviewed forward migration")
