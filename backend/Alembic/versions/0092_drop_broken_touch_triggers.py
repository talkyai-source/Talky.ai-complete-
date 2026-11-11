"""Drop touch triggers that set a column their table does not have (QA F14-02).

On the production lineage (the June 2026 baseline), assistant_actions and
reminders have BEFORE UPDATE triggers that set NEW.updated_at, but neither
table has an updated_at column, so every UPDATE of either table raises
'record "new" has no field "updated_at"'. Fresh installs have no such
trigger. A trigger is dropped only where its table lacks the column.
Downgrade does not restore them: they could only make every UPDATE fail again.
"""
from alembic import op

revision = "0092_drop_broken_touch_triggers"
down_revision = "0091_call_history_cursor"
branch_labels = None
depends_on = None

BROKEN_TOUCH_TRIGGERS = (
    ("assistant_actions", "update_assistant_actions_updated_at"),
    ("reminders", "update_reminders_updated_at"),
)


def _literal(value):
    # Fixed migration source only, never input.
    return "'" + value.replace("'", "''") + "'"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    for table, trigger in BROKEN_TOUCH_TRIGGERS:
        op.execute(f"""
        DO $q92$
        DECLARE target oid;
        BEGIN
            target := to_regclass({_literal('public.' + table)});
            IF target IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_catalog.pg_trigger
                           WHERE tgrelid=target AND tgname={_literal(trigger)} AND NOT tgisinternal)
               AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute
                               WHERE attrelid=target AND attname='updated_at' AND NOT attisdropped) THEN
                DROP TRIGGER {trigger} ON public.{table};
            END IF;
        END $q92$;
        """)


def downgrade():
    # Restoring the triggers would only break every UPDATE again.
    pass
