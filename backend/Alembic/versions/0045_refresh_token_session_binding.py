"""Bind each refresh-token family to the login session it was issued for.

Revision ID: 0045_refresh_token_session_binding
Revises: 0044_webhook_null_tenant_rls

Why: POST /auth/refresh re-minted the access JWT WITHOUT the ``sid`` claim,
because the refresh row did not know which login session it belonged to.
Fifteen minutes after login every access token lost its session binding.
REST accepted that; the Test-agent WebSocket (which requires a live
session-bound token) refused it with "Your session has expired" — for the
rest of the login. Storing the session on the family lets refresh carry the
binding forward and keep the session alive.

The backfill matches existing families to the session created by the same
login: login creates the security_session and the first refresh token in one
request, so the nearest session of that user within 60 s of the family's first
token is the right one. Families with no such session stay NULL and behave as
before (no sid) until the user signs in again.
"""
from alembic import op
from sqlalchemy import text

revision = "0045_refresh_token_session_binding"
down_revision = "0044_webhook_null_tenant_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS session_id UUID"))
    op.execute(text("""
        UPDATE refresh_tokens rt
        SET    session_id = m.session_id
        FROM (
            SELECT f.family_id, s.id AS session_id
            FROM (
                SELECT family_id, user_id, MIN(issued_at) AS first_issued
                FROM   refresh_tokens
                WHERE  session_id IS NULL
                GROUP  BY family_id, user_id
            ) f
            JOIN LATERAL (
                SELECT id
                FROM   security_sessions s
                WHERE  s.user_id = f.user_id
                  AND  s.created_at BETWEEN f.first_issued - INTERVAL '60 seconds'
                                        AND f.first_issued + INTERVAL '60 seconds'
                ORDER  BY ABS(EXTRACT(EPOCH FROM (s.created_at - f.first_issued)))
                LIMIT  1
            ) s ON TRUE
        ) m
        WHERE rt.family_id = m.family_id AND rt.session_id IS NULL
    """))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_rt_session ON refresh_tokens (session_id)"))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS idx_rt_session"))
    op.execute(text("ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS session_id"))
