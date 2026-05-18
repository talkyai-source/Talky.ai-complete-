"""add alert_type column to security_events for the Alert Timeline panel

The Alert Timeline on the campaigns page categorises alerts as
Network / API / Campaign / System. `security_events` already has the
right shape for everything else (severity, status, title, description,
resolution_notes, resolved_at, sla_deadline) but lacks the category
column. This migration adds it.

Existing rows have alert_type=NULL — they're security-only events that
shouldn't surface in the user-facing Alert Timeline. The list endpoint
filters by `alert_type IS NOT NULL` so legacy security events stay
private to the admin security panel.

Revision ID: 0004_security_events_alert_type
Revises: 0003_add_stream_events
Create Date: 2026-05-15 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

revision: str = "0004_security_events_alert_type"
down_revision: Union[str, None] = "0003_add_stream_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE security_events "
        "ADD COLUMN IF NOT EXISTS alert_type TEXT "
        "CHECK (alert_type IS NULL OR alert_type IN "
        "       ('Network','API','Campaign','System'))"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_security_events_alert_type "
        "ON security_events (alert_type, created_at DESC) "
        "WHERE alert_type IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS idx_security_events_alert_type"))
    op.execute(text("ALTER TABLE security_events DROP COLUMN IF EXISTS alert_type"))
