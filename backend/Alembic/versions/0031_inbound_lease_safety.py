"""enforce a safe inbound concurrency heartbeat window

Revision ID: 0031_inbound_lease_safety
Revises: 0030_inbound_transfer_leg_usage
Create Date: 2026-08-28 00:00:00.000000

The connected-call heartbeat runs every 30 seconds. Historical policy checks
allowed a 10-second TTL plus 5 seconds of grace, so another admission could
expire a healthy live call before its first refresh. This revision normalizes
existing policies and makes a 90-second minimum window authoritative in the
database. The constraint is deliberately retained on downgrade because the
0030 application has the same heartbeat cadence and remains unsafe without it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0031_inbound_lease_safety"
down_revision: str | None = "0030_inbound_transfer_leg_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "telephony_policy_heartbeat_window_safe"
_MINIMUM_WINDOW_SECONDS = 90


def upgrade() -> None:
    op.execute(
        text(
            "LOCK TABLE tenant_telephony_concurrency_policies "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    op.execute(
        text(
            f"""
            UPDATE tenant_telephony_concurrency_policies
               SET lease_ttl_seconds = GREATEST(
                       lease_ttl_seconds,
                       {_MINIMUM_WINDOW_SECONDS} - heartbeat_grace_seconds
                   ),
                   updated_at = NOW()
             WHERE lease_ttl_seconds + heartbeat_grace_seconds
                   < {_MINIMUM_WINDOW_SECONDS}
            """
        )
    )
    op.execute(
        text(
            f"""
            DO $migration$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid =
                              'tenant_telephony_concurrency_policies'::regclass
                      AND conname = '{_CONSTRAINT}'
                ) THEN
                    ALTER TABLE tenant_telephony_concurrency_policies
                    ADD CONSTRAINT {_CONSTRAINT}
                    CHECK (
                        lease_ttl_seconds + heartbeat_grace_seconds
                        >= {_MINIMUM_WINDOW_SECONDS}
                    ) NOT VALID;
                END IF;
            END;
            $migration$;
            """
        )
    )
    op.execute(
        text(
            "ALTER TABLE tenant_telephony_concurrency_policies "
            f"VALIDATE CONSTRAINT {_CONSTRAINT}"
        )
    )


def downgrade() -> None:
    # Non-destructive compatibility downgrade: 0030 has the same 30-second
    # heartbeat and therefore requires the same minimum safety window.
    pass
