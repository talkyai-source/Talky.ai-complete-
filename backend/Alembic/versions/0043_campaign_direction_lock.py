"""serialize every campaign direction change with the canonical advisory lock

Revision ID: 0043_campaign_direction_lock
Revises: 0042_dialer_origination_guard
Create Date: 2026-09-04

Documented application writers acquire this advisory lock *before* locking the
campaign row.  The row trigger is a database safety net for future or ad-hoc
writers that omit that explicit step; it prevents an un-serialized direction
change from committing.  A writer that already took the row lock in the wrong
order can still be selected as a PostgreSQL deadlock victim, so this trigger is
not permission to remove the application-side lock or reverse its ordering.
"""

from alembic import op
from sqlalchemy import text


revision = "0043_campaign_direction_lock"
down_revision = "0042_dialer_origination_guard"
branch_labels = None
depends_on = None


_FUNCTION_NAME = "public.talky_lock_campaign_direction_update"
_TRIGGER_NAME = "campaigns_direction_advisory_lock"


def upgrade() -> None:
    op.execute(
        text(
            f"""
            CREATE OR REPLACE FUNCTION {_FUNCTION_NAME}()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $$
            BEGIN
                PERFORM pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended('talky:campaign-direction:' || NEW.id::text, 0)
                );
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        text(
            f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON public.campaigns"
        )
    )
    op.execute(
        text(
            f"""
            CREATE TRIGGER {_TRIGGER_NAME}
            BEFORE UPDATE OF direction ON public.campaigns
            FOR EACH ROW
            WHEN (OLD.direction IS DISTINCT FROM NEW.direction)
            EXECUTE FUNCTION {_FUNCTION_NAME}()
            """
        )
    )
    op.execute(
        text(
            f"""
            COMMENT ON FUNCTION {_FUNCTION_NAME}() IS
            'Database backstop for campaign direction serialization. '
            'Application writers must acquire the matching advisory lock '
            'before any campaign row lock.'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON public.campaigns"
        )
    )
    op.execute(text(f"DROP FUNCTION IF EXISTS {_FUNCTION_NAME}()"))
