"""Persist tenant-owned call notes and post-call forms (QA F1-26).

Downgrade refuses to discard notes. The parent-key index is retained because
later relations may use it; it changes no business data.
"""
from alembic import op

revision = "0086_call_workflows"
down_revision = "0085_legal_acceptance"
branch_labels = None
depends_on = None


def upgrade():
    # calls.id is already globally unique. The composite key makes ownership
    # part of the FK even for a role that bypasses RLS.
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_workflow_owner
        ON calls (tenant_id, id)""")
    op.execute("""CREATE TABLE IF NOT EXISTS call_workflows (
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        call_id UUID NOT NULL,
        lead_type TEXT NOT NULL DEFAULT 'cold'
            CHECK (lead_type IN ('cold', 'warm', 'hot', 'follow_up')),
        notes TEXT NOT NULL DEFAULT '' CHECK (char_length(notes) <= 4000),
        contact TEXT NOT NULL DEFAULT '' CHECK (char_length(contact) <= 160),
        interest TEXT NOT NULL DEFAULT '' CHECK (char_length(interest) <= 1000),
        next_step TEXT NOT NULL DEFAULT '' CHECK (char_length(next_step) <= 1000),
        completed BOOLEAN NOT NULL DEFAULT FALSE,
        updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (tenant_id, call_id),
        FOREIGN KEY (tenant_id, call_id) REFERENCES calls (tenant_id, id) ON DELETE CASCADE
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_call_workflows_updated_by
        ON call_workflows (updated_by) WHERE updated_by IS NOT NULL""")
    op.execute("ALTER TABLE call_workflows ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE call_workflows FORCE ROW LEVEL SECURITY")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_policy
                       WHERE polrelid = to_regclass('public.call_workflows')
                         AND polname = 'call_workflows_tenant') THEN
            CREATE POLICY call_workflows_tenant ON call_workflows
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
        IF to_regclass('public.call_workflows') IS NOT NULL THEN
            IF EXISTS (SELECT 1 FROM call_workflows LIMIT 1) THEN
                RAISE EXCEPTION 'Call workflow data exists; use a reviewed forward migration';
            END IF;
            DROP TABLE call_workflows;
        END IF;
    END $$""")
