"""add tenant_telephony_credentials + tenants.active_telephony_provider

Lets a tenant store their own Twilio or Vonage credentials and pick which
provider (Twilio / Vonage / local SIP trunk) the dialer worker should use
for their outbound calls. `tenant_sip_trunks` is already in place and
covers the SIP option, so this migration only adds the cloud-provider
credential table and the active-provider pointer on `tenants`.

Credentials are stored as a JSON string then encrypted with the shared
`TokenEncryptionService` (Fernet) — the column type is TEXT to match the
service's base64 output, matching the existing connector_accounts pattern.

Revision ID: 0005_tenant_telephony_creds
Revises: 0004_security_events_alert_type
Create Date: 2026-05-15 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

revision: str = "0005_tenant_telephony_creds"
down_revision: Union[str, None] = "0004_security_events_alert_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS tenant_telephony_credentials (
            id                    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id             UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            provider              TEXT        NOT NULL
                                  CHECK (provider IN ('twilio','vonage')),
            label                 TEXT,
            credentials_encrypted TEXT        NOT NULL,
            from_number           TEXT,
            status                TEXT        NOT NULL DEFAULT 'inactive'
                                  CHECK (status IN ('active','inactive','failed')),
            last_tested_at        TIMESTAMPTZ,
            last_test_result      JSONB,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, provider)
        )
    """))

    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ttc_tenant "
        "ON tenant_telephony_credentials (tenant_id)"
    ))

    op.execute(text("ALTER TABLE tenant_telephony_credentials ENABLE ROW LEVEL SECURITY"))
    op.execute(text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'tenant_telephony_credentials'
                AND policyname = 'ttc_tenant_isolation'
            ) THEN
                CREATE POLICY ttc_tenant_isolation ON tenant_telephony_credentials
                USING (tenant_id::text = current_setting('app.current_tenant_id', true));
            END IF;
        END $$
    """))

    op.execute(text("""
        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS active_telephony_provider TEXT
            DEFAULT 'none'
    """))
    op.execute(text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.constraint_column_usage
                WHERE table_name = 'tenants'
                AND column_name = 'active_telephony_provider'
                AND constraint_name = 'tenants_active_telephony_provider_check'
            ) THEN
                ALTER TABLE tenants
                ADD CONSTRAINT tenants_active_telephony_provider_check
                CHECK (active_telephony_provider IN ('twilio','vonage','sip','none'));
            END IF;
        END $$
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_active_telephony_provider_check"))
    op.execute(text("ALTER TABLE tenants DROP COLUMN IF EXISTS active_telephony_provider"))
    op.execute(text("DROP TABLE IF EXISTS tenant_telephony_credentials CASCADE"))
