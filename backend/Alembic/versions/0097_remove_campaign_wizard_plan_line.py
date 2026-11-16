"""Remove the "Automatic campaign wizard" plan line (D5-2).

Business and Pay as you go listed it as a feature and Basic listed it as not
included, but no code makes the campaign wizard differ by plan: every plan
creates campaigns the same way. The line was a claim the product does not
honour, so it leaves every plan's `features` and `not_included` list. No other
line, price, limit or id changes.

Idempotent: only rows whose lists still carry the line are rewritten.
Downgrade puts the line back at the end of the list where 0071 / 0083 wrote it
(it was the last item in each): basic.not_included, business.features,
payg.features. Those three places are the only ones the line ever appeared in,
so the restore is exact for the values those migrations stored.
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0097_remove_wizard_plan_line"
down_revision = "0096_contact_list_permission"
branch_labels = None
depends_on = None

LINE = "Automatic campaign wizard"
# (plan id, column) pairs that carried the line as the last item (0071, 0083).
_WAS_LAST_IN = {("basic", "not_included"), ("business", "features"), ("payg", "features")}


def without_line(items):
    """The list without any copy of the line; order of the rest is kept."""
    return [item for item in items if item != LINE]


def restore_line(plan_id, column, items):
    """Downgrade: append the line where the plan had it; unchanged elsewhere."""
    if (plan_id, column) not in _WAS_LAST_IN or LINE in items:
        return list(items)
    return [*items, LINE]


def _load(value):
    return json.loads(value) if isinstance(value, str) else list(value or [])


def _set(conn, column, plan_id, items):
    # Fixed statement text per column; the value is always a bound parameter.
    sql = {
        "features": "UPDATE public.plans SET features = CAST(:value AS jsonb) WHERE id = :id",
        "not_included": "UPDATE public.plans SET not_included = CAST(:value AS jsonb) WHERE id = :id",
    }[column]
    conn.execute(text(sql), {"id": plan_id, "value": json.dumps(items)})


def upgrade():
    conn = op.get_bind()
    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '60s'"))
    rows = conn.execute(text(
        "SELECT id, features, not_included FROM public.plans "
        "WHERE features @> CAST(:line AS jsonb) OR not_included @> CAST(:line AS jsonb) "
        "ORDER BY id FOR UPDATE"), {"line": json.dumps([LINE])}).mappings().all()
    for row in rows:
        for column in ("features", "not_included"):
            items = _load(row[column])
            if LINE in items:
                _set(conn, column, row["id"], without_line(items))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '60s'"))
    rows = conn.execute(text(
        "SELECT id, features, not_included FROM public.plans "
        "WHERE id IN ('basic', 'business', 'payg') ORDER BY id FOR UPDATE")).mappings().all()
    for row in rows:
        for column in ("features", "not_included"):
            items = _load(row[column])
            restored = restore_line(row["id"], column, items)
            if restored != items:
                _set(conn, column, row["id"], restored)
