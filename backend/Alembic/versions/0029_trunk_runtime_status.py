"""track live Asterisk trunk runtime evidence in the Alembic chain

Revision ID: 0029_trunk_runtime_status
Revises: 0028_call_terminal_settle_cas
Create Date: 2026-08-27 00:00:00.000000

These columns were once introduced by two hand-applied SQL files dated
2026-07-03.  Alembic is the repository's sole deployment path, so fresh
databases never received them even though the API selected them.  The DDL is
idempotent to converge both historical production and clean installations.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0029_trunk_runtime_status"
down_revision: str | None = "0028_call_terminal_settle_cas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            """
            ALTER TABLE tenant_sip_trunks
                ADD COLUMN IF NOT EXISTS live_registration_status TEXT,
                ADD COLUMN IF NOT EXISTS live_status_detail TEXT,
                ADD COLUMN IF NOT EXISTS live_status_checked_at TIMESTAMPTZ
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN tenant_sip_trunks.live_registration_status IS
                'Fresh Asterisk runtime state: registered, loaded, rejected, '
                'unregistered, registering, stopped, failed, missing_config, '
                'inactive, or unknown. Written by trunk_live_status_updater.'
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN tenant_sip_trunks.live_status_detail IS
                'Bounded operator-facing reason associated with the live state.'
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN tenant_sip_trunks.live_status_checked_at IS
                'UTC time when Asterisk runtime state was last queried.'
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_tenant_sip_trunks_active_live_status
            ON tenant_sip_trunks (tenant_id, live_status_checked_at DESC)
            WHERE is_active = TRUE
            """
        )
    )


def downgrade() -> None:
    # The live-status columns predate Alembic in historical installations and
    # the rollback application already reads them. Their origin cannot be
    # reconstructed safely at downgrade time, so retain the compatible columns
    # and remove only this revision's derived index. This mirrors 0028's
    # treatment of the pre-existing calls.dialer_job_id column.
    op.execute(text("DROP INDEX IF EXISTS idx_tenant_sip_trunks_active_live_status"))
