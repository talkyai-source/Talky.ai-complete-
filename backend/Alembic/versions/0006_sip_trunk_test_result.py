"""add last_tested_at + last_test_result to tenant_sip_trunks

A trunk is just an INSERTed row today — there's nothing proving the host
is reachable before someone flips ``is_active = TRUE`` and breaks
outbound calls. This migration adds two columns so the new
POST /trunks/{id}/test endpoint can persist a probe result on the row,
and the UI / activation gate can read it.

Revision ID: 0006_sip_trunk_test_result
Revises: 0005_tenant_telephony_creds
Create Date: 2026-05-15 13:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

revision: str = "0006_sip_trunk_test_result"
down_revision: Union[str, None] = "0005_tenant_telephony_creds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE tenant_sip_trunks "
        "ADD COLUMN IF NOT EXISTS last_tested_at TIMESTAMPTZ"
    ))
    op.execute(text(
        "ALTER TABLE tenant_sip_trunks "
        "ADD COLUMN IF NOT EXISTS last_test_result JSONB"
    ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE tenant_sip_trunks DROP COLUMN IF EXISTS last_test_result"))
    op.execute(text("ALTER TABLE tenant_sip_trunks DROP COLUMN IF EXISTS last_tested_at"))
