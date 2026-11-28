"""Basic plan line "Recording retention: 30 days" becomes "90 days" (B6).

No code gives a Basic account 30 days: retention is a per-account setting
(`tenant_recording_policy.retention_days`) that defaults to 90 for every plan,
and the purge reads only that setting. The plan card's line was a claim the
product does not honour, so the one line of the `basic` plan changes to the
default every plan really has. No other line, plan, price, limit or id changes.

Idempotent and staff-safe: only a `basic` row whose `features` list still holds
the line with exactly the old text is rewritten; a line staff edited to any
other text is left alone.
Downgrade puts the old text back, only in `basic` and only where the line still
says exactly what this migration wrote, so a line it did not write is never
touched.
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0109_plan_retention_line"
down_revision = "0108_telephony_number_requests"
branch_labels = None
depends_on = None

OLD_LINE = "Recording retention: 30 days"  # as 0071 wrote it for Basic
NEW_LINE = "Recording retention: 90 days"


def updated_features(items):
    """Upgrade: the old line becomes the new one; order and every other line kept."""
    return [NEW_LINE if item == OLD_LINE else item for item in items]


def restored_features(items):
    """Downgrade: the new line becomes the old one; every other line kept."""
    return [OLD_LINE if item == NEW_LINE else item for item in items]


def _load(value):
    return json.loads(value) if isinstance(value, str) else list(value or [])


def _rewrite(line, transform):
    conn = op.get_bind()
    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '60s'"))
    rows = conn.execute(text(
        "SELECT id, features FROM public.plans "
        "WHERE id = 'basic' AND features @> CAST(:line AS jsonb) FOR UPDATE"),
        {"line": json.dumps([line])}).mappings().all()
    for row in rows:
        items = _load(row["features"])
        changed = transform(items)
        if changed != items:
            conn.execute(
                text("UPDATE public.plans SET features = CAST(:value AS jsonb) WHERE id = :id"),
                {"id": row["id"], "value": json.dumps(changed)})


def upgrade():
    _rewrite(OLD_LINE, updated_features)


def downgrade():
    _rewrite(NEW_LINE, restored_features)
