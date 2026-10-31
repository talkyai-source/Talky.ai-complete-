"""Allow inbound and PBX-originated calls without a campaign.

Revision ID: 0040_calls_campaign_nullable
Revises: 0039_contact_capture_audit
Create Date: 2026-09-02

The archived raw-SQL migration and canonical complete schema have always
allowed ``calls.campaign_id`` to be NULL.  Alembic revision 0018 carried only
the matching ``lead_id`` change, so installations upgraded from the preserved
baseline still rejected the campaign-less call rows used by inbound and PBX
paths.  Widening NOT NULL to NULL preserves every existing row and leaves the
foreign key in force whenever a campaign is supplied.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0040_calls_campaign_nullable"
down_revision: str | None = "0039_contact_capture_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(
        text(
            """
            DO $nullable$
            BEGIN
                IF EXISTS (
                    SELECT 1
                      FROM pg_attribute
                     WHERE attrelid = 'public.calls'::regclass
                       AND attname = 'campaign_id'
                       AND attnum > 0
                       AND NOT attisdropped
                       AND attnotnull
                ) THEN
                    ALTER TABLE public.calls
                    ALTER COLUMN campaign_id DROP NOT NULL;
                END IF;
            END
            $nullable$;
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN public.calls.campaign_id IS
            'Campaign that originated the call, when one exists. NULL for '
            'inbound, manual, and PBX-originated calls outside a campaign.'
            """
        )
    )
    op.execute(
        text(
            """
            DO $postcondition$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                      FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND table_name = 'calls'
                       AND column_name = 'campaign_id'
                       AND is_nullable = 'YES'
                ) THEN
                    RAISE EXCEPTION
                        '0040 calls.campaign_id nullable postcondition failed';
                END IF;
            END
            $postcondition$;
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade 0040: campaign-less calls make NOT NULL destructive"
    )
