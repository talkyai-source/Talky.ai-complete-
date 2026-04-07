"""baseline: mark existing schema as applied

This is a BASELINE migration. It does NOT create tables — the database
was already created from complete_schema.sql. This migration simply
establishes the starting point so Alembic can track future changes.

To use on a fresh database, run:
  1. psql $DATABASE_URL -f database/complete_schema.sql
  2. alembic upgrade head   (applies this baseline + any newer migrations)

To use on an existing database that already has the schema applied:
  1. alembic stamp head     (marks this revision as current without running SQL)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Baseline migration: verify core tables exist, create recordings_s3 table.

    We use CREATE TABLE IF NOT EXISTS so this is safe to run on a database
    that was bootstrapped from complete_schema.sql.

    The only new object here is `recordings_s3` — the S3-backed recording
    metadata table added as part of the S3 migration upgrade.
    """
    # Verify baseline tables exist (will raise if schema is missing)
    conn = op.get_bind()
    result = conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'tenants'"
    ))
    count = result.scalar()
    if count == 0:
        raise RuntimeError(
            "Baseline table 'tenants' not found. "
            "Apply complete_schema.sql first:\n"
            "  psql $DATABASE_URL -f database/complete_schema.sql"
        )

    # Create recordings_s3 table for object-storage-backed recordings
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS recordings_s3 (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            call_id         UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            campaign_id     UUID,
            s3_bucket       VARCHAR(255) NOT NULL,
            s3_key          VARCHAR(1024) NOT NULL,
            s3_region       VARCHAR(64) NOT NULL DEFAULT 'us-east-1',
            storage_provider VARCHAR(32) NOT NULL DEFAULT 's3',
            file_size_bytes BIGINT,
            duration_seconds INTEGER,
            mime_type       VARCHAR(64) NOT NULL DEFAULT 'audio/wav',
            status          VARCHAR(32) NOT NULL DEFAULT 'uploaded'
                            CHECK (status IN ('uploading','uploaded','failed','deleted')),
            upload_started_at  TIMESTAMPTZ,
            upload_finished_at TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(s3_bucket, s3_key)
        )
    """))

    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_recordings_s3_call_id
        ON recordings_s3(call_id)
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_recordings_s3_tenant_id
        ON recordings_s3(tenant_id)
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_recordings_s3_status
        ON recordings_s3(status)
    """))

    # Row-level security for tenant isolation
    op.execute(text("ALTER TABLE recordings_s3 ENABLE ROW LEVEL SECURITY"))
    op.execute(text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'recordings_s3'
                AND policyname = 'recordings_s3_tenant_isolation'
            ) THEN
                CREATE POLICY recordings_s3_tenant_isolation ON recordings_s3
                USING (tenant_id::text = current_setting('app.current_tenant_id', true));
            END IF;
        END $$
    """))


def downgrade() -> None:
    """Remove only the objects added in this migration (recordings_s3)."""
    op.execute(text("DROP TABLE IF EXISTS recordings_s3 CASCADE"))
