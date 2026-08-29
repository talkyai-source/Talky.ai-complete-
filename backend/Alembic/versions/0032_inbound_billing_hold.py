"""persist and recover reasoned inbound billing holds

Revision ID: 0032_inbound_billing_hold
Revises: 0031_inbound_lease_safety
Create Date: 2026-08-28 00:00:00.000000

Terminal calls held only because the global settlement switch was disabled
must converge after that switch is reopened. Calls whose measured duration
exceeded their reservation must remain held for explicit reconciliation. A
provider Answer whose result is ambiguous across a process crash must likewise
remain held for carrier/CDR reconciliation. A durable reason distinguishes
these states without mutating the immutable usage ledger.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0032_inbound_billing_hold"
down_revision: str | None = "0031_inbound_lease_safety"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "calls_billing_hold_reason_valid"
_INDEX = "idx_calls_inbound_switch_billing_hold"


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS "
            "billing_hold_reason VARCHAR(64)"
        )
    )
    # Released lease rows are durable evidence for holds created before this
    # column existed. Unknown historical holds stay NULL and therefore remain
    # manual; they must never be guessed into the automatic settlement path.
    op.execute(
        text(
            """
            UPDATE calls AS call
               SET billing_hold_reason = CASE lease.release_reason
                   WHEN 'settlement_held' THEN 'settlement_switch_disabled'
                   WHEN 'usage_exceeded_reservation' THEN
                       'usage_exceeded_reservation'
                   ELSE NULL
               END,
                   updated_at = NOW()
              FROM tenant_telephony_concurrency_leases AS lease
             WHERE call.concurrency_lease_id = lease.id
               AND call.direction = 'inbound'
               AND call.billing_status = 'held'
               AND call.billing_hold_reason IS NULL
               AND lease.release_reason IN (
                   'settlement_held', 'usage_exceeded_reservation'
               )
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
                    WHERE conrelid = 'calls'::regclass
                      AND conname = '{_CONSTRAINT}'
                ) THEN
                    ALTER TABLE calls
                    ADD CONSTRAINT {_CONSTRAINT}
                    CHECK (
                        billing_hold_reason IS NULL
                        OR (
                            direction = 'inbound'
                            AND billing_status = 'held'
                            AND billing_hold_reason IN (
                                'settlement_switch_disabled',
                                'usage_exceeded_reservation',
                                'provider_answer_ambiguous'
                            )
                        )
                    ) NOT VALID;
                END IF;
            END;
            $migration$;
            """
        )
    )
    op.execute(text(f"ALTER TABLE calls VALIDATE CONSTRAINT {_CONSTRAINT}"))
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {_INDEX}
                ON calls (updated_at)
             WHERE direction = 'inbound'
               AND billing_status = 'held'
               AND billing_hold_reason = 'settlement_switch_disabled'
            """
        )
    )


def downgrade() -> None:
    op.execute(text("LOCK TABLE calls IN ACCESS EXCLUSIVE MODE"))
    retained = int(
        op.get_bind()
        .execute(
            text(
                "SELECT count(*) FROM calls "
                "WHERE billing_hold_reason IS NOT NULL"
            )
        )
        .scalar()
        or 0
    )
    if retained:
        raise RuntimeError(
            "Refusing to downgrade 0032: "
            f"{retained} call row(s) retain billing-hold evidence"
        )
    op.execute(text(f"DROP INDEX IF EXISTS {_INDEX}"))
    op.execute(
        text(
            f"ALTER TABLE calls DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"
        )
    )
    op.execute(text("ALTER TABLE calls DROP COLUMN IF EXISTS billing_hold_reason"))
