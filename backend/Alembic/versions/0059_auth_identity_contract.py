"""Make existing verified-signup columns part of the migrated contract.

Historical verification is never inferred. Existing manually provisioned
columns and values are retained; operators must reconcile missing evidence.
"""

from alembic import op

revision = "0059_auth_identity_contract"
down_revision = "0058_crm_contact_effect"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """ALTER TABLE user_profiles
        ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS verification_token TEXT,
        ADD COLUMN IF NOT EXISTS verification_token_expires_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ"""
    )
    op.execute(
        """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='user_profiles'::regclass AND conname='chk_email_verification_consistency') THEN
            ALTER TABLE user_profiles ADD CONSTRAINT chk_email_verification_consistency
                CHECK (NOT is_verified OR (verification_token IS NULL AND email_verified_at IS NOT NULL)) NOT VALID;
        END IF;
    END $$"""
    )
    op.execute("ALTER TABLE user_profiles VALIDATE CONSTRAINT chk_email_verification_consistency")
    op.execute(
        "ALTER TABLE mfa_challenges ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='mfa_challenges'::regclass AND conname='mfa_challenges_attempts_bounds') THEN
            ALTER TABLE mfa_challenges ADD CONSTRAINT mfa_challenges_attempts_bounds
                CHECK (attempts >= 0 AND attempts <= 100) NOT VALID;
        END IF;
    END $$"""
    )
    op.execute("ALTER TABLE mfa_challenges VALIDATE CONSTRAINT mfa_challenges_attempts_bounds")


def downgrade():
    # Keep verification evidence and its invariant on code rollback. upgrade
    # is idempotent if the Alembic version marker is later moved forward.
    pass
