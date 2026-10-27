"""Retain bounded supplier-source evidence and separate operator reviews.

No price model, supplier API polling, customer charge or financial approval.
"""
from alembic import op

revision = "0079_supplier_evidence"
down_revision = "0078_admin_billing_merge"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE public.supplier_statement_evidence (
            id uuid PRIMARY KEY,
            request_id uuid NOT NULL UNIQUE,
            supplier text NOT NULL CHECK (supplier = 'deepgram'),
            source_kind text NOT NULL CHECK (source_kind = 'deepgram_billing_breakdown_v1'),
            account_id uuid NOT NULL,
            reference text NOT NULL CHECK (length(reference) BETWEEN 1 AND 120),
            period_start date NOT NULL,
            period_end date NOT NULL CHECK (period_end >= period_start),
            currency text NOT NULL CHECK (currency = 'USD'),
            reported_charges numeric(24,8),
            source_bytes bytea NOT NULL CHECK (octet_length(source_bytes) BETWEEN 1 AND 1048576),
            source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
            normalized jsonb NOT NULL CHECK (jsonb_typeof(normalized) = 'object'),
            imported_by uuid NOT NULL,
            imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            supersedes_id uuid UNIQUE REFERENCES public.supplier_statement_evidence(id) ON DELETE RESTRICT,
            UNIQUE (supplier, account_id, reference)
        )
    """)
    op.execute("""
        CREATE INDEX supplier_evidence_period ON public.supplier_statement_evidence
            (period_start, period_end, imported_at DESC, id)
    """)
    op.execute("""
        CREATE TABLE public.supplier_statement_reviews (
            id uuid PRIMARY KEY,
            request_id uuid NOT NULL UNIQUE,
            statement_id uuid NOT NULL REFERENCES public.supplier_statement_evidence(id) ON DELETE RESTRICT,
            stage text NOT NULL CHECK (stage IN ('reviewed', 'reconciled')),
            source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            review_reference text NOT NULL CHECK (length(review_reference) BETWEEN 1 AND 120),
            notes text NOT NULL CHECK (length(notes) BETWEEN 10 AND 2000),
            reviewer_id uuid NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            comparison jsonb NOT NULL CHECK (jsonb_typeof(comparison) = 'object'),
            UNIQUE (statement_id, stage)
        )
    """)
    op.execute("""
        CREATE FUNCTION public.reject_supplier_evidence_rewrite() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Supplier evidence and reviews are append-only; retain corrections'
                USING ERRCODE = '55000';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER supplier_evidence_immutable BEFORE UPDATE OR DELETE
            ON public.supplier_statement_evidence FOR EACH ROW
            EXECUTE FUNCTION public.reject_supplier_evidence_rewrite()
    """)
    op.execute("""
        CREATE TRIGGER supplier_review_immutable BEFORE UPDATE OR DELETE
            ON public.supplier_statement_reviews FOR EACH ROW
            EXECUTE FUNCTION public.reject_supplier_evidence_rewrite()
    """)
    # Backup uses the same explicit existing principal context. Worker bypass
    # alone is insufficient for private supplier evidence, as for saved ACKs.
    principal = """EXISTS (SELECT 1 FROM public.user_profiles up
        WHERE up.id=NULLIF(current_setting('app.current_user_id',TRUE),'')::uuid
          AND up.role='platform_admin' AND up.is_active IS TRUE AND up.is_verified IS TRUE)"""
    for table in ("supplier_statement_evidence", "supplier_statement_reviews"):
        actor = "imported_by" if table == "supplier_statement_evidence" else "reviewer_id"
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_read ON public.{table} FOR SELECT USING ({principal})")
        op.execute(f"""CREATE POLICY {table}_insert ON public.{table} FOR INSERT
            WITH CHECK ({principal} AND {actor}=NULLIF(current_setting('app.current_user_id',TRUE),'')::uuid)""")


def downgrade():
    raise RuntimeError("Supplier evidence must be retained; use a reviewed forward migration")
