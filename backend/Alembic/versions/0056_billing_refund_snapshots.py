"""Retain verified refund observations separately from accounting movements."""

from alembic import op
from sqlalchemy import text

revision = "0056_billing_refund_snapshots"
down_revision = "0055_invoice_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(
        text(
            "ALTER TABLE public.topup_orders ADD CONSTRAINT topup_orders_id_tenant_unique UNIQUE(id,tenant_id)"
        )
    )
    op.execute(
        text(
            """CREATE TABLE public.billing_refund_snapshots (
        id BIGSERIAL PRIMARY KEY,
        order_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        source_event_id TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        projection JSONB NOT NULL CHECK (jsonb_typeof(projection)='object'),
        captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(order_id,source_event_id,content_hash),
        FOREIGN KEY(order_id,tenant_id) REFERENCES public.topup_orders(id,tenant_id)
    )"""
        )
    )
    op.execute(
        text(
            """CREATE INDEX billing_refund_snapshot_latest
        ON public.billing_refund_snapshots(tenant_id,order_id,id DESC)"""
        )
    )
    op.execute(
        text(
            """CREATE FUNCTION public.reject_billing_refund_snapshot_change()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Billing refund observations are append-only'; END $$"""
        )
    )
    op.execute(
        text(
            """CREATE TRIGGER billing_refund_snapshots_immutable
        BEFORE UPDATE OR DELETE ON public.billing_refund_snapshots
        FOR EACH ROW EXECUTE FUNCTION public.reject_billing_refund_snapshot_change()"""
        )
    )
    op.execute(text("ALTER TABLE public.billing_refund_snapshots ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE public.billing_refund_snapshots FORCE ROW LEVEL SECURITY"))
    bypass = "COALESCE(NULLIF(current_setting('app.bypass_rls',TRUE),'')::boolean,FALSE)"
    tenant = "tenant_id=NULLIF(current_setting('app.current_tenant_id',TRUE),'')::uuid"
    op.execute(
        text(
            f"""CREATE POLICY billing_refund_snapshots_read
        ON public.billing_refund_snapshots FOR SELECT USING ({bypass} OR {tenant})"""
        )
    )
    op.execute(
        text(
            f"""CREATE POLICY billing_refund_snapshots_insert
        ON public.billing_refund_snapshots FOR INSERT WITH CHECK ({bypass})"""
        )
    )


def downgrade():
    raise RuntimeError("Refund observations must be retained; use a reviewed forward migration")
