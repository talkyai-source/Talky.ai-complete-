"""add voice_tuning JSONB column to tenant_ai_configs

The /ai-options frontend has been sending per-tenant voice_tuning blobs
(prosody / SSML hint dict resolved by app.domain.services.voice_tuning)
but the database table had no column to store them. The dead shadow
file backend/app/api/v1/endpoints/ai_options.py referenced the column,
which would have crashed on every save — but Python imports the live
ai_options/ package instead, which never read or wrote it, so the
frontend's tuning input has been silently discarded on every save.

This migration backfills the missing column so the value can actually
land in the DB once the read/write paths in ai_options/_shared.py are
updated to handle it.

Revision ID: 0008_tenant_voice_tuning
Revises: 0007_mfa_challenge_attempts
Create Date: 2026-05-19 19:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

revision: str = "0008_tenant_voice_tuning"
down_revision: Union[str, None] = "0007_mfa_challenge_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE tenant_ai_configs "
        "ADD COLUMN IF NOT EXISTS voice_tuning JSONB NOT NULL DEFAULT '{}'::jsonb"
    ))


def downgrade() -> None:
    op.execute(text(
        "ALTER TABLE tenant_ai_configs DROP COLUMN IF EXISTS voice_tuning"
    ))
