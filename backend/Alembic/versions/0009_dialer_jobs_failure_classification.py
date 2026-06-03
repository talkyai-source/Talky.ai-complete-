"""add failure_category + failure_reason to dialer_jobs (Track 2 retry classifier)

These columns were first shipped as a raw SQL file
(database/migrations/20260522_add_dialer_jobs_failure_classification.sql)
and applied to prod by hand via psql — outside Alembic's tracking. As
part of consolidating onto a single migration system (Alembic), that
change is reintroduced here as the tracked revision.

It is written idempotently (ADD COLUMN IF NOT EXISTS / CREATE INDEX IF
NOT EXISTS) so `alembic upgrade head` is a safe no-op on prod (where the
columns already exist) and correctly creates them on a fresh database.
prod is currently stamped at 0008_tenant_voice_tuning; the next
`alembic upgrade head` advances it to here with no schema change.

Revision ID: 0009_dialer_jobs_failure_classification
Revises: 0008_tenant_voice_tuning
Create Date: 2026-06-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# NOTE: id kept <=32 chars — alembic_version.version_num is varchar(32); the
# original 39-char id ("…failure_classification") couldn't be stamped and broke
# `alembic upgrade head`. Filename unchanged (alembic keys on the id string).
revision: str = "0009_dialer_failure_class"
down_revision: Union[str, None] = "0008_tenant_voice_tuning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE dialer_jobs "
        "ADD COLUMN IF NOT EXISTS failure_category TEXT, "
        "ADD COLUMN IF NOT EXISTS failure_reason TEXT"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dialer_jobs_failure_category "
        "ON dialer_jobs (failure_category) "
        "WHERE failure_category IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS idx_dialer_jobs_failure_category"))
    op.execute(text(
        "ALTER TABLE dialer_jobs "
        "DROP COLUMN IF EXISTS failure_reason, "
        "DROP COLUMN IF EXISTS failure_category"
    ))
