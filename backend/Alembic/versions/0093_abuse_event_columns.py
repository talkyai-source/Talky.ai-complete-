"""Give abuse_events the columns the abuse code writes (QA F14-02).

On the production lineage (the June 2026 baseline), abuse_events lacks the
ten columns app/domain/services/abuse_detection.py inserts and the resolve
paths update, so no abuse event can be recorded or resolved. Fresh installs
already have them. Each missing column is added as nullable or with a constant
default (no table rewrite); an existing column of the same type is adopted,
any other type is refused. Downgrade removes only the columns and CHECK this
revision created, and only while abuse_events is empty.
"""
from alembic import op

revision = "0093_abuse_event_columns"
down_revision = "0092_drop_broken_touch_triggers"
branch_labels = None
depends_on = None
OWNER = "created by 0093_abuse_event_columns"

# Column, format_type() spelling, DDL after the type: exactly the columns
# app/domain/services/abuse_detection.py inserts and resolves with.
ABUSE_COLUMNS = (
    ("partner_id", "uuid", ""),
    ("trigger_value", "numeric", ""),
    ("threshold_value", "numeric", ""),
    ("phone_number_called", "character varying(50)", ""),
    ("call_id", "uuid", ""),
    ("action_taken", "text", "NOT NULL DEFAULT 'flagged'"),
    ("action_details", "jsonb", "DEFAULT '{}'::jsonb"),
    ("destination_country", "text", ""),
    ("resolution_notes", "text", ""),
    ("false_positive", "boolean", ""),
)
ACTION_CHECK = "abuse_events_action_taken_check"
ACTIONS = ("flagged", "warned", "throttled", "blocked", "suspended")


def _literal(value):
    # Fixed migration source only, never input.
    return "'" + value.replace("'", "''") + "'"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    for column, kind, extra in ABUSE_COLUMNS:
        op.execute(f"""
        DO $q93$
        DECLARE target oid; existing text;
        BEGIN
            target := to_regclass('public.abuse_events');
            IF target IS NULL THEN
                RAISE EXCEPTION '0093 expected abuse_events table is missing';
            END IF;
            SELECT pg_catalog.format_type(atttypid, atttypmod) INTO existing
                FROM pg_catalog.pg_attribute
                WHERE attrelid=target AND attname={_literal(column)} AND NOT attisdropped;
            IF existing IS NULL THEN
                ALTER TABLE public.abuse_events ADD COLUMN {column} {kind} {extra};
                COMMENT ON COLUMN public.abuse_events.{column} IS {_literal(OWNER)};
            ELSIF existing <> {_literal(kind)} THEN
                RAISE EXCEPTION '0093 incompatible abuse_events.% type %', {_literal(column)}, existing;
            END IF;
        END $q93$;
        """)
    op.execute(f"""
    DO $q93$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                       WHERE conrelid='public.abuse_events'::regclass AND conname={_literal(ACTION_CHECK)}) THEN
            ALTER TABLE public.abuse_events ADD CONSTRAINT {ACTION_CHECK}
                CHECK (action_taken IN ({",".join(map(_literal, ACTIONS))}));
            COMMENT ON CONSTRAINT {ACTION_CHECK} ON public.abuse_events IS {_literal(OWNER)};
        END IF;
    END $q93$;
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(f"""
    DO $q93$
    DECLARE target oid; owned_column text;
    BEGIN
        target := to_regclass('public.abuse_events');
        IF target IS NULL THEN
            RETURN;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_attribute
                   WHERE attrelid=target AND attnum > 0 AND NOT attisdropped
                     AND pg_catalog.col_description(target, attnum)={_literal(OWNER)})
           AND EXISTS (SELECT 1 FROM public.abuse_events) THEN
            RAISE EXCEPTION 'Refusing to remove abuse event columns while abuse_events has rows. Use a reviewed forward migration.';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                   WHERE conrelid=target AND conname={_literal(ACTION_CHECK)}
                     AND pg_catalog.obj_description(oid, 'pg_constraint')={_literal(OWNER)}) THEN
            ALTER TABLE public.abuse_events DROP CONSTRAINT {ACTION_CHECK};
        END IF;
        FOR owned_column IN
            SELECT attname FROM pg_catalog.pg_attribute
            WHERE attrelid=target AND attnum > 0 AND NOT attisdropped
              AND pg_catalog.col_description(target, attnum)={_literal(OWNER)}
        LOOP
            EXECUTE format('ALTER TABLE public.abuse_events DROP COLUMN %I', owned_column);
        END LOOP;
    END $q93$;
    """)
