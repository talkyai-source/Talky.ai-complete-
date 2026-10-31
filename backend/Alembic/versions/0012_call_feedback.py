"""durable call feedback audio and transcription state

One audio feedback note belongs to one reviewed call. The audio object is stored
before this row is inserted; Deepgram is called only after the INSERT transaction
commits. ``transcript_status`` makes provider failures explicit and retryable.

Revision ID: 0012_call_feedback
Revises: 0011_campaign_tts_provider
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0012_call_feedback"
down_revision: str | None = "0011_campaign_tts_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        CREATE TABLE IF NOT EXISTS call_feedback (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL
                REFERENCES tenants(id) ON DELETE CASCADE,
            call_id UUID NOT NULL
                REFERENCES calls(id) ON DELETE CASCADE,
            created_by UUID
                REFERENCES user_profiles(id) ON DELETE SET NULL,

            audio_storage_provider VARCHAR(16) NOT NULL,
            audio_bucket VARCHAR(255) NOT NULL,
            audio_key VARCHAR(1024) NOT NULL,
            audio_mime_type VARCHAR(100) NOT NULL,
            audio_size_bytes BIGINT NOT NULL,
            audio_sha256 VARCHAR(64) NOT NULL,
            duration_seconds DOUBLE PRECISION,

            transcript TEXT,
            transcript_status VARCHAR(16) NOT NULL DEFAULT 'pending',
            transcript_error TEXT,
            transcription_attempts INTEGER NOT NULL DEFAULT 0,
            transcript_provider VARCHAR(32) NOT NULL DEFAULT 'deepgram',
            transcript_provider_request_id VARCHAR(128),
            transcription_started_at TIMESTAMPTZ,
            transcribed_at TIMESTAMPTZ,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT call_feedback_one_note_per_call UNIQUE (call_id),
            CONSTRAINT call_feedback_audio_object_unique
                UNIQUE (audio_bucket, audio_key),
            CONSTRAINT call_feedback_audio_size_positive
                CHECK (audio_size_bytes > 0),
            CONSTRAINT call_feedback_sha256_valid
                CHECK (audio_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT call_feedback_duration_nonnegative
                CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
            CONSTRAINT call_feedback_attempts_nonnegative
                CHECK (transcription_attempts >= 0),
            CONSTRAINT call_feedback_status_valid
                CHECK (transcript_status IN ('pending', 'done', 'failed')),
            CONSTRAINT call_feedback_storage_provider_valid
                CHECK (audio_storage_provider IN ('s3', 'local'))
        )
    """)
    )

    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_call_feedback_tenant_created "
            "ON call_feedback (tenant_id, created_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_call_feedback_retryable "
            "ON call_feedback (tenant_id, updated_at) "
            "WHERE transcript_status IN ('pending', 'failed')"
        )
    )

    op.execute(text("ALTER TABLE call_feedback ENABLE ROW LEVEL SECURITY"))
    op.execute(text("DROP POLICY IF EXISTS call_feedback_tenant_isolation ON call_feedback"))
    op.execute(
        text("""
        CREATE POLICY call_feedback_tenant_isolation
        ON call_feedback FOR ALL
        USING (
            tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
        )
        WITH CHECK (
            tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
        )
    """)
    )

    op.execute(
        text("""
        COMMENT ON TABLE call_feedback IS
        'Durable reviewer audio notes and their synchronous best-effort '
        'prerecorded transcription state; one note per call.'
    """)
    )


def downgrade() -> None:
    # Metadata only. Object-store deletion is an explicit operational step so a
    # schema rollback can never silently destroy customer audio.
    op.execute(text("DROP TABLE IF EXISTS call_feedback"))
