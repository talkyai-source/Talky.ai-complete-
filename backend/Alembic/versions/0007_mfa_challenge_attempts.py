"""add attempts column to mfa_challenges for per-challenge brute-force cap

The /auth/mfa/verify endpoint had no per-challenge attempt limit. With a
6-digit TOTP space (1M combinations) and 100 req/min global IP rate
limit, an attacker spreading across a small botnet could exhaust the
search space within the 5-minute challenge TTL.

This migration adds a counter to each challenge row. The verify endpoint
increments it on every wrong submission and invalidates the challenge
once it reaches 5, forcing the attacker to start over (which requires
another `/auth/login` round-trip — gated by the per-account login
lockout that's already in place).

Revision ID: 0007_mfa_challenge_attempts
Revises: 0006_sip_trunk_test_result
Create Date: 2026-05-18 17:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

revision: str = "0007_mfa_challenge_attempts"
down_revision: Union[str, None] = "0006_sip_trunk_test_result"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE mfa_challenges "
        "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
    ))
    # CHECK keeps the counter sane in case any future code path inserts
    # a negative or absurdly large value.
    op.execute(text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.constraint_column_usage
                WHERE table_name = 'mfa_challenges'
                AND column_name = 'attempts'
                AND constraint_name = 'mfa_challenges_attempts_bounds'
            ) THEN
                ALTER TABLE mfa_challenges
                ADD CONSTRAINT mfa_challenges_attempts_bounds
                CHECK (attempts >= 0 AND attempts <= 100);
            END IF;
        END $$
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE mfa_challenges DROP CONSTRAINT IF EXISTS mfa_challenges_attempts_bounds"))
    op.execute(text("ALTER TABLE mfa_challenges DROP COLUMN IF EXISTS attempts"))
