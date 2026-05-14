"""add refresh_tokens table for OAuth-style rotation + reuse detection

Adds the long-lived opaque refresh token store that backs the new
httpOnly cookie auth flow. Each row represents one refresh token in a
rotation chain. The chain shares a family_id; reusing a row whose
used_at is already set indicates token theft and revokes the entire
family.

Revision ID: 0002_add_refresh_tokens
Revises: 0001_baseline
Create Date: 2026-05-13 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

revision: str = "0002_add_refresh_tokens"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            family_id       UUID        NOT NULL,
            user_id         UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
            tenant_id       UUID,
            token_hash      TEXT        NOT NULL UNIQUE,
            parent_id       UUID        REFERENCES refresh_tokens(id) ON DELETE SET NULL,
            issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at      TIMESTAMPTZ NOT NULL,
            used_at         TIMESTAMPTZ,
            revoked_at      TIMESTAMPTZ,
            revoked_reason  TEXT
                            CHECK (revoked_reason IS NULL OR revoked_reason IN
                                   ('rotated','reuse_detected','logout','admin','expired')),
            ip              INET,
            user_agent      TEXT,
            CONSTRAINT chk_rt_expires_after_issued CHECK (expires_at > issued_at)
        )
    """))

    op.execute(text("CREATE INDEX IF NOT EXISTS idx_rt_family ON refresh_tokens (family_id)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_rt_user_active ON refresh_tokens (user_id, expires_at) WHERE revoked_at IS NULL"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_rt_token_lookup ON refresh_tokens (token_hash) WHERE revoked_at IS NULL"))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS refresh_tokens CASCADE"))
