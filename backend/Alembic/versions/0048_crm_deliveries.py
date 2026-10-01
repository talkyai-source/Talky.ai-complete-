"""Durable per-provider CRM receipts, queued in the call transaction."""
from alembic import op
from sqlalchemy import text

revision = "0048_crm_deliveries"
down_revision = "0047_protect_ai_config_backup"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(text("""
        CREATE TABLE crm_deliveries (
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            provider TEXT NOT NULL CHECK (provider IN ('hubspot', 'salesforce')),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'succeeded', 'failed', 'unknown', 'skipped')),
            phase TEXT NOT NULL DEFAULT 'pending',
            desired_key TEXT,
            completed_key TEXT,
            remote_contact_id TEXT,
            remote_call_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_token UUID,
            lease_expires_at TIMESTAMPTZ,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, call_id, provider)
        )
    """))
    op.execute(text("CREATE INDEX crm_deliveries_due ON crm_deliveries (next_attempt_at) WHERE status IN ('pending','unknown','processing')"))
    policy = (
        "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
        " OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
    )
    op.execute(text("ALTER TABLE crm_deliveries ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE crm_deliveries FORCE ROW LEVEL SECURITY"))
    op.execute(text(f"CREATE POLICY crm_deliveries_tenant_isolation ON crm_deliveries FOR ALL USING ({policy}) WITH CHECK ({policy})"))
    op.execute(text("""
        CREATE FUNCTION queue_call_crm_deliveries() RETURNS TRIGGER
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.tenant_id IS NULL OR (
                NEW.ended_at IS NULL AND NEW.status NOT IN
                ('completed','failed','no_answer','busy','cancelled','canceled','rejected','unreachable')
            ) THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND ROW(NEW.status, NEW.outcome, NEW.duration_seconds,
                NEW.transcript, NEW.summary_json, NEW.recording_url, NEW.ended_at)
                IS NOT DISTINCT FROM ROW(OLD.status, OLD.outcome, OLD.duration_seconds,
                OLD.transcript, OLD.summary_json, OLD.recording_url, OLD.ended_at) THEN
                RETURN NEW;
            END IF;
            INSERT INTO crm_deliveries (tenant_id, call_id, provider, status, phase, last_error)
                SELECT DISTINCT NEW.tenant_id, NEW.id, c.provider,
                    CASE WHEN NEW.crm_call_id IS NULL THEN 'pending' ELSE 'unknown' END,
                    CASE WHEN NEW.crm_call_id IS NULL THEN 'pending' ELSE 'legacy_unverified' END,
                    CASE WHEN NEW.crm_call_id IS NULL THEN NULL ELSE 'Legacy shared CRM ID requires provider ownership review' END
                FROM connectors c
                WHERE c.tenant_id = NEW.tenant_id AND c.type = 'crm' AND c.status = 'active'
                  AND c.provider IN ('hubspot','salesforce')
            ON CONFLICT (tenant_id, call_id, provider) DO UPDATE SET
                desired_key = NULL,
                status = CASE WHEN crm_deliveries.status IN ('processing','unknown')
                              THEN crm_deliveries.status ELSE 'pending' END,
                attempts = CASE WHEN crm_deliveries.status IN ('processing','unknown')
                                THEN crm_deliveries.attempts ELSE 0 END,
                next_attempt_at = NOW(), updated_at = NOW();
            RETURN NEW;
        END $$
    """))
    op.execute(text("""
        CREATE TRIGGER calls_queue_crm_deliveries AFTER INSERT OR UPDATE OF
            status, outcome, duration_seconds, transcript, summary_json, recording_url, ended_at
        ON calls FOR EACH ROW EXECUTE FUNCTION queue_call_crm_deliveries()
    """))
    # Historical shared IDs are deliberately not copied to every destination.
    # Only new terminal/revised calls enqueue; prior ambiguous IDs need review.


def downgrade():
    op.execute(text("DROP TRIGGER IF EXISTS calls_queue_crm_deliveries ON calls"))
    op.execute(text("DROP FUNCTION IF EXISTS queue_call_crm_deliveries()"))
    op.execute(text("DROP TABLE IF EXISTS crm_deliveries"))
