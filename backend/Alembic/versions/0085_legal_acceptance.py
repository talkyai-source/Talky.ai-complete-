"""Append-only legal-version acceptance receipts (QA F12-10).

Legal supplies current versions and text; signup enforcement stays off until
the frontend ships. Downgrade refuses to destroy populated acceptance evidence.
"""
from alembic import op

revision = "0085_legal_acceptance"
down_revision = "0084_call_failure_lead_signals"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS legal_acceptances (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE RESTRICT,
        terms_version VARCHAR(128) NOT NULL,
        privacy_version VARCHAR(128) NOT NULL,
        accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ip_address VARCHAR(64),
        user_agent VARCHAR(512)
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_legal_acceptances_user
        ON legal_acceptances (tenant_id, user_id, accepted_at DESC, id DESC)""")
    op.execute("""CREATE OR REPLACE FUNCTION reject_legal_acceptance_mutation()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Legal acceptance receipts are append-only' USING ERRCODE = '55000';
        END $$""")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_trigger
                       WHERE tgrelid = to_regclass('public.legal_acceptances')
                         AND tgname = 'legal_acceptances_immutable') THEN
            CREATE TRIGGER legal_acceptances_immutable
            BEFORE UPDATE OR DELETE OR TRUNCATE ON legal_acceptances
            FOR EACH STATEMENT EXECUTE FUNCTION reject_legal_acceptance_mutation();
        END IF;
    END $$""")
    op.execute("ALTER TABLE legal_acceptances ENABLE ROW LEVEL SECURITY")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_policy
                       WHERE polrelid = to_regclass('public.legal_acceptances')
                         AND polname = 'legal_acceptances_tenant') THEN
            CREATE POLICY legal_acceptances_tenant ON legal_acceptances
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
                OR COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
            ) WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
                OR COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
            );
        END IF;
    END $$""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF to_regclass('public.legal_acceptances') IS NOT NULL THEN
            IF EXISTS (SELECT 1 FROM legal_acceptances LIMIT 1) THEN
                RAISE EXCEPTION 'Legal acceptance evidence exists; use a reviewed forward migration';
            END IF;
            DROP TABLE legal_acceptances;
        END IF;
    END $$""")
    op.execute("DROP FUNCTION IF EXISTS reject_legal_acceptance_mutation()")
