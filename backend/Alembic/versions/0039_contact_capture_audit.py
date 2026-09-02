"""Add auditable contact-capture values and state.

Revision ID: 0039_contact_capture_audit
Revises: 0038_tenant_table_rls_backfill
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0039_contact_capture_audit"
down_revision: str | None = "0038_tenant_table_rls_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable/additive columns avoid a table rewrite. Only contact rows use
    # these fields; generic lead fields legitimately keep them NULL.
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    op.execute(
        text(
            """
            ALTER TABLE call_lead_details
                ADD COLUMN IF NOT EXISTS raw_value TEXT,
                ADD COLUMN IF NOT EXISTS normalized_value TEXT,
                ADD COLUMN IF NOT EXISTS validation_status VARCHAR(32),
                ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ
            """
        )
    )
    connection = op.get_bind()
    prior_bypass = str(
        connection.execute(
            text(
                "SELECT COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), ''), 'off')"
            )
        ).scalar()
        or "off"
    )
    connection.execute(text("SELECT set_config('app.bypass_rls', 'on', TRUE)"))
    # Preserve what can be established from historical rows without inventing
    # a confirmation timestamp. New live writes carry the actual timestamp.
    op.execute(
        text(
            """
            UPDATE call_lead_details
               SET raw_value = COALESCE(raw_value, value),
                   normalized_value = COALESCE(normalized_value, value),
                   validation_status = COALESCE(
                       validation_status,
                       CASE WHEN value IS NULL THEN 'cancelled'
                            WHEN confirmed THEN 'confirmed'
                            ELSE 'awaiting_confirmation' END
                   )
             WHERE field_type IN ('email', 'phone')
            """
        )
    )
    op.execute(
        text(
            """
            DO $audit$
            BEGIN
                IF EXISTS (
                    SELECT 1
                      FROM call_lead_details
                     WHERE field_type IN ('email', 'phone')
                       AND (
                           raw_value IS DISTINCT FROM value
                           OR normalized_value IS DISTINCT FROM value
                           OR validation_status IS DISTINCT FROM (
                               CASE WHEN value IS NULL THEN 'cancelled'
                                    WHEN confirmed THEN 'confirmed'
                                    ELSE 'awaiting_confirmation' END
                           )
                       )
                ) THEN
                    RAISE EXCEPTION
                        '0039 contact audit backfill postcondition failed';
                END IF;
            END;
            $audit$;
            """
        )
    )
    op.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                      FROM pg_constraint
                     WHERE conrelid = 'call_lead_details'::regclass
                       AND conname = 'call_lead_details_validation_status_valid'
                ) THEN
                    ALTER TABLE call_lead_details
                    ADD CONSTRAINT call_lead_details_validation_status_valid
                    CHECK (
                        validation_status IS NULL OR validation_status IN (
                            'needs_clarification',
                            'invalid',
                            'awaiting_confirmation',
                            'confirmed',
                            'cancelled'
                        )
                    ) NOT VALID;
                END IF;
            END $$
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE call_lead_details
            VALIDATE CONSTRAINT call_lead_details_validation_status_valid
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN call_lead_details.raw_value IS
            'Caller-stated value before canonical contact normalization.'
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN call_lead_details.normalized_value IS
            'Validated canonical value; phone values are strict E.164.'
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN call_lead_details.validation_status IS
            'needs_clarification | invalid | awaiting_confirmation | confirmed | cancelled'
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN call_lead_details.confirmed_at IS
            'Exact live confirmation time; NULL for unconfirmed or historical unknown.'
            """
        )
    )
    # Both settings are transaction-local. On an earlier failure PostgreSQL
    # aborts the migration transaction and restores them atomically; on success
    # restore the caller's explicit RLS mode before returning.
    connection.execute(
        text("SELECT set_config('app.bypass_rls', :prior, TRUE)"),
        {"prior": prior_bypass},
    )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade 0039: contact-capture audit history is forward-only"
    )
