"""Append-only provider invoice observations; no synthetic historical backfill."""

from sqlalchemy import text

from alembic import op

revision = "0055_invoice_snapshots"
down_revision = "0054_billing_webhook_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(
        text(
            "ALTER TABLE public.invoices ADD COLUMN notification_history_known BOOLEAN NOT NULL DEFAULT FALSE"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.invoices ALTER COLUMN amount_due TYPE BIGINT, ALTER COLUMN amount_paid TYPE BIGINT"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.invoices ADD CONSTRAINT invoices_id_tenant_unique UNIQUE(id,tenant_id)"
        )
    )
    op.execute(
        text(
            """CREATE TABLE public.invoice_snapshots (
        id BIGSERIAL PRIMARY KEY,
        invoice_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        content_hash TEXT NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        projection JSONB NOT NULL CHECK(jsonb_typeof(projection)='object'),
        UNIQUE(invoice_id,content_hash),
        FOREIGN KEY(invoice_id,tenant_id) REFERENCES public.invoices(id,tenant_id)
    )"""
        )
    )
    op.execute(
        text(
            "CREATE INDEX invoice_snapshots_latest ON public.invoice_snapshots(invoice_id,id DESC)"
        )
    )
    op.execute(text("ALTER TABLE public.invoice_snapshots ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE public.invoice_snapshots FORCE ROW LEVEL SECURITY"))
    bypass = "COALESCE(NULLIF(current_setting('app.bypass_rls',TRUE),'')::boolean,FALSE)"
    tenant = "tenant_id=NULLIF(current_setting('app.current_tenant_id',TRUE),'')::uuid"
    op.execute(
        text(
            f"CREATE POLICY invoice_snapshots_read ON public.invoice_snapshots FOR SELECT USING ({bypass} OR {tenant})"
        )
    )
    op.execute(
        text(
            f"CREATE POLICY invoice_snapshots_insert ON public.invoice_snapshots FOR INSERT WITH CHECK ({bypass})"
        )
    )
    op.execute(
        text(
            """CREATE FUNCTION public.reject_invoice_snapshot_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Invoice snapshots are append-only'; END $$"""
        )
    )
    op.execute(
        text(
            """CREATE TRIGGER invoice_snapshots_immutable BEFORE UPDATE OR DELETE ON public.invoice_snapshots
        FOR EACH ROW EXECUTE FUNCTION public.reject_invoice_snapshot_mutation()"""
        )
    )


def downgrade():
    raise RuntimeError("Invoice observations must be retained; use a reviewed forward migration")
