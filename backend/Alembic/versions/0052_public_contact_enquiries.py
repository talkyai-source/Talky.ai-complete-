"""Own the durable public enquiry intake; its content is platform-only."""
from sqlalchemy import text

from alembic import op

revision = "0052_public_contact_enquiries"
down_revision = "0051_crm_destination_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(text("""CREATE TABLE IF NOT EXISTS public.public_contact_enquiries (
        id UUID PRIMARY KEY,
        payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
        name TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
        email TEXT NOT NULL CHECK (char_length(email) BETWEEN 3 AND 254),
        company TEXT NOT NULL DEFAULT '' CHECK (char_length(company) <= 120),
        message TEXT NOT NULL CHECK (char_length(btrim(message)) BETWEEN 1 AND 500),
        status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'handled')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        handled_at TIMESTAMPTZ,
        handled_by UUID,
        CHECK ((status = 'new' AND handled_at IS NULL AND handled_by IS NULL)
            OR (status = 'handled' AND handled_at IS NOT NULL AND handled_by IS NOT NULL))
    )"""))
    op.execute(text("""CREATE INDEX IF NOT EXISTS idx_public_contact_enquiries_status_created
        ON public.public_contact_enquiries (status, created_at DESC, id DESC)"""))
    op.execute(text("ALTER TABLE public.public_contact_enquiries ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE public.public_contact_enquiries FORCE ROW LEVEL SECURITY"))
    op.execute(text("DROP POLICY IF EXISTS public_contact_enquiries_platform_only ON public.public_contact_enquiries"))
    op.execute(text("""CREATE POLICY public_contact_enquiries_platform_only
        ON public.public_contact_enquiries FOR ALL
        USING (COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE))
        WITH CHECK (COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE))"""))


def downgrade():
    raise RuntimeError("Public enquiry receipts must be retained; use a reviewed forward migration")
