"""per-campaign TTS provider

Adds campaigns.tts_provider so each campaign runs on its OWN TTS engine
(provider + the existing voice_id column) independent of the tenant's global AI
config. NULL = "use the tenant global provider" (back-compat for every existing
row). This is what makes "apply this config to these campaigns" truthful and
ends the account-wide provider-switch side effect.

Idempotent (ADD COLUMN IF NOT EXISTS). id kept <=32 chars — alembic_version
is varchar(32).

Revision ID: 0011_campaign_tts_provider
Revises: 0010_campaign_knowledge
Create Date: 2026-06-04 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0011_campaign_tts_provider"
down_revision: Union[str, None] = "0010_campaign_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS tts_provider TEXT"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE campaigns DROP COLUMN IF EXISTS tts_provider"))
