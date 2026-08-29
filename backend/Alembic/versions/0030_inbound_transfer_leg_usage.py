"""add durable usage settlement for inbound transfer legs

Revision ID: 0030_inbound_transfer_leg_usage
Revises: 0029_trunk_runtime_status
Create Date: 2026-08-27 00:00:00.000000

The inbound usage ledger originally allowed exactly one reservation and one
terminal transaction per parent call.  A controlled inbound transfer creates a
second, independently billable PSTN leg, so the ledger subject must be explicit
without weakening the existing parent-call guarantees.

``call_leg_id IS NULL`` continues to mean the parent inbound call.  A non-null
value identifies one durable child leg and is constrained to belong to the same
``call_id``.  Parent and child subjects each retain one reserve and one
finalize/release transaction, while the existing tenant idempotency key and
append-only trigger remain authoritative.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0030_inbound_transfer_leg_usage"
down_revision: str | None = "0029_trunk_runtime_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            """
            ALTER TABLE call_legs
                ADD COLUMN IF NOT EXISTS billing_status VARCHAR(16)
                    NOT NULL DEFAULT 'none',
                ADD COLUMN IF NOT EXISTS reserved_seconds INTEGER
                    NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS cost NUMERIC(12, 6),
                ADD COLUMN IF NOT EXISTS currency VARCHAR(3)
            """
        )
    )
    op.execute(
        text(
            """
            DO $migration$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid='call_legs'::regclass
                      AND conname='call_legs_billing_status_valid'
                ) THEN
                    ALTER TABLE call_legs
                    ADD CONSTRAINT call_legs_billing_status_valid
                    CHECK (billing_status IN (
                        'none','reserved','held','finalized','released','reversed'
                    ));
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid='call_legs'::regclass
                      AND conname='call_legs_reserved_seconds_nonnegative'
                ) THEN
                    ALTER TABLE call_legs
                    ADD CONSTRAINT call_legs_reserved_seconds_nonnegative
                    CHECK (reserved_seconds >= 0);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid='call_legs'::regclass
                      AND conname='call_legs_cost_nonnegative'
                ) THEN
                    ALTER TABLE call_legs
                    ADD CONSTRAINT call_legs_cost_nonnegative
                    CHECK (cost IS NULL OR cost >= 0);
                END IF;
            END;
            $migration$;
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_call_legs_id_call "
            "ON call_legs (id, call_id)"
        )
    )

    op.execute(
        text(
            "ALTER TABLE inbound_usage_transactions "
            "ADD COLUMN IF NOT EXISTS call_leg_id UUID"
        )
    )
    op.execute(
        text(
            """
            DO $migration$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid='inbound_usage_transactions'::regclass
                      AND conname='inbound_usage_call_leg_call_fk'
                ) THEN
                    ALTER TABLE inbound_usage_transactions
                    ADD CONSTRAINT inbound_usage_call_leg_call_fk
                    FOREIGN KEY (call_leg_id, call_id)
                    REFERENCES call_legs(id, call_id)
                    ON DELETE RESTRICT;
                END IF;
            END;
            $migration$;
            """
        )
    )

    # Preserve the original one-parent-reservation/settlement contract while
    # allowing the same parent call to own independently settled transfer legs.
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_usage_reserve_per_call"))
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_usage_settlement_per_call"))
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_reserve_per_call "
            "ON inbound_usage_transactions (call_id) "
            "WHERE transaction_type='reserve' AND call_leg_id IS NULL"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_settlement_per_call "
            "ON inbound_usage_transactions (call_id) "
            "WHERE transaction_type IN ('finalize','release') "
            "AND call_leg_id IS NULL"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_reserve_per_leg "
            "ON inbound_usage_transactions (call_leg_id) "
            "WHERE transaction_type='reserve' AND call_leg_id IS NOT NULL"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_settlement_per_leg "
            "ON inbound_usage_transactions (call_leg_id) "
            "WHERE transaction_type IN ('finalize','release') "
            "AND call_leg_id IS NOT NULL"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_inbound_usage_call_leg_created "
            "ON inbound_usage_transactions (call_leg_id, created_at) "
            "WHERE call_leg_id IS NOT NULL"
        )
    )

    op.execute(
        text(
            "COMMENT ON COLUMN inbound_usage_transactions.call_leg_id IS "
            "'NULL for the parent inbound call; otherwise the independently reserved and settled transfer leg'"
        )
    )
    op.execute(
        text(
            "COMMENT ON COLUMN call_legs.cost IS "
            "'Actual leg cost when supplied by an authoritative carrier CDR/rate source; NULL means unavailable, never zero'"
        )
    )


def downgrade() -> None:
    # Multiple immutable child ledger subjects cannot be collapsed back into
    # 0022's one-row-per-parent shape without deleting billing evidence. Fail
    # before any DDL instead of producing a partial or lossy downgrade.
    op.execute(
        text(
            "LOCK TABLE call_legs, inbound_usage_transactions "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    op.execute(
        text(
            """
            DO $migration$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM inbound_usage_transactions
                    WHERE call_leg_id IS NOT NULL
                ) THEN
                    RAISE EXCEPTION
                        '0030 downgrade refused: transfer-leg usage ledger is non-empty';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM call_legs
                    WHERE billing_status <> 'none'
                       OR reserved_seconds <> 0
                       OR cost IS NOT NULL
                       OR currency IS NOT NULL
                ) THEN
                    RAISE EXCEPTION
                        '0030 downgrade refused: transfer-leg billing evidence is non-empty';
                END IF;
            END;
            $migration$;
            """
        )
    )
    op.execute(text("DROP INDEX IF EXISTS idx_inbound_usage_call_leg_created"))
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_usage_settlement_per_leg"))
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_usage_reserve_per_leg"))
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_usage_settlement_per_call"))
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_usage_reserve_per_call"))
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_reserve_per_call "
            "ON inbound_usage_transactions (call_id) "
            "WHERE transaction_type='reserve'"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_settlement_per_call "
            "ON inbound_usage_transactions (call_id) "
            "WHERE transaction_type IN ('finalize','release')"
        )
    )
    op.execute(
        text(
            "ALTER TABLE inbound_usage_transactions "
            "DROP CONSTRAINT IF EXISTS inbound_usage_call_leg_call_fk"
        )
    )
    op.execute(
        text(
            "ALTER TABLE inbound_usage_transactions "
            "DROP COLUMN IF EXISTS call_leg_id"
        )
    )
    op.execute(text("DROP INDEX IF EXISTS uq_call_legs_id_call"))
    op.execute(
        text(
            """
            ALTER TABLE call_legs
                DROP CONSTRAINT IF EXISTS call_legs_cost_nonnegative,
                DROP CONSTRAINT IF EXISTS call_legs_reserved_seconds_nonnegative,
                DROP CONSTRAINT IF EXISTS call_legs_billing_status_valid,
                DROP COLUMN IF EXISTS currency,
                DROP COLUMN IF EXISTS cost,
                DROP COLUMN IF EXISTS reserved_seconds,
                DROP COLUMN IF EXISTS billing_status
            """
        )
    )
