"""Never queue CRM deliveries for Test Agent calls.

Bug hunt 2026-10-10 (campaigns-contacts-crm-1): the 0048 trigger enqueues a
crm_deliveries row for every terminal call, including Test Agent sessions
(calls.is_test), so a tenant's CRM received junk leads and tasks carrying the
test transcript. The application now parks such rows, but only after the
drain worker has picked them once; this stops them at the source. The body is
0048's function with one extra early return.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0066_crm_skip_test_calls"
down_revision: Union[str, None] = "0065_stream_events_call_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BODY = """
CREATE OR REPLACE FUNCTION public.queue_call_crm_deliveries() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS NULL{test_guard} OR (
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
"""
FUNCTION_SQL = _BODY.replace("{test_guard}", " OR COALESCE(NEW.is_test, FALSE)")
ORIGINAL_FUNCTION_SQL = _BODY.replace("{test_guard}", "")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(FUNCTION_SQL)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(ORIGINAL_FUNCTION_SQL)
