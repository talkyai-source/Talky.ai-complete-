"""Keep source and confirmation evidence with captured lead details."""
from alembic import op

revision = "0049_lead_detail_evidence"
down_revision = "0048_crm_deliveries"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE call_lead_details ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS lead_details_status TEXT NOT NULL DEFAULT 'pending'")
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS summary_transcript_hash TEXT")
    op.execute("""UPDATE calls SET lead_details_status='not_processed'
                  WHERE summary_transcript_hash IS NULL AND lead_details_status='pending'
                    AND status IN ('completed','failed','no_answer','busy','cancelled')""")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS latest_analysis_note TEXT")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS latest_analysis_call_id UUID")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS latest_analysis_at TIMESTAMPTZ")


def downgrade():
    # Evidence remains useful to the preceding version; do not erase audit data.
    pass
