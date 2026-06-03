"""campaign knowledge tree (vectorless RAG)

Adds the per-campaign knowledge layer: an uploaded .md/.txt is parsed into a
hierarchical node tree (markdown headings) and LLM-enriched, then retrieved
vectorlessly (Postgres FTS + pg_trgm) into the agent's system prompt.

Tables:
  - campaign_knowledge_sources : one row per uploaded document (raw md + status)
  - campaign_knowledge_nodes   : the heading tree (parent_id), enriched + indexed
campaigns gains: knowledge_mode (none|inline|map_retrieve|retrieve, set by
ingest from the model-aware budget) and knowledge_model (optional larger-context
model for knowledge-heavy campaigns).

Idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS) so `alembic upgrade head` is
safe on prod and correct on a fresh DB. See
docs/superpowers/plans/2026-06-03-vectorless-rag-campaign-knowledge.md.

Revision ID: 0010_campaign_knowledge
Revises: 0009_dialer_jobs_failure_classification
Create Date: 2026-06-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0010_campaign_knowledge"
down_revision: Union[str, None] = "0009_dialer_failure_class"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Fuzzy matching for STT-misheard transcripts (hybrid FTS + trigram).
    op.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    op.execute(text(
        "ALTER TABLE campaigns "
        "ADD COLUMN IF NOT EXISTS knowledge_mode TEXT NOT NULL DEFAULT 'none', "
        "ADD COLUMN IF NOT EXISTS knowledge_model TEXT"
    ))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_knowledge_sources (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            campaign_id   UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            tenant_id     UUID NOT NULL,
            filename      TEXT,
            raw_md        TEXT NOT NULL,
            token_count   INTEGER NOT NULL DEFAULT 0,
            version       INTEGER NOT NULL DEFAULT 1,
            status        TEXT NOT NULL DEFAULT 'processing'
                          CHECK (status IN ('processing','ready','failed')),
            error         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_cks_campaign "
        "ON campaign_knowledge_sources (campaign_id, status)"
    ))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_knowledge_nodes (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            campaign_id       UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            tenant_id         UUID NOT NULL,
            source_id         UUID NOT NULL REFERENCES campaign_knowledge_sources(id) ON DELETE CASCADE,
            parent_id         UUID REFERENCES campaign_knowledge_nodes(id) ON DELETE CASCADE,
            depth             INTEGER NOT NULL DEFAULT 0,
            path              TEXT NOT NULL,
            position          INTEGER NOT NULL DEFAULT 0,
            heading           TEXT NOT NULL,
            content           TEXT NOT NULL DEFAULT '',
            summary           TEXT,
            voice_answer      TEXT,
            keywords          TEXT[],
            example_questions TEXT[],
            search_text       TEXT NOT NULL DEFAULT '',
            search_tsv        tsvector,
            priority          SMALLINT NOT NULL DEFAULT 0,
            hit_count         BIGINT NOT NULL DEFAULT 0,
            enabled           BOOLEAN NOT NULL DEFAULT TRUE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ckn_fts "
        "ON campaign_knowledge_nodes USING gin(search_tsv)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ckn_trgm "
        "ON campaign_knowledge_nodes USING gin(search_text gin_trgm_ops)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ckn_campaign "
        "ON campaign_knowledge_nodes (campaign_id, enabled)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ckn_tree "
        "ON campaign_knowledge_nodes (campaign_id, parent_id, position)"
    ))

    # RLS — tenant sees only its own rows (matches tenant_ai_credentials).
    for tbl in ("campaign_knowledge_sources", "campaign_knowledge_nodes"):
        op.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"DROP POLICY IF EXISTS {tbl}_isolation ON {tbl}"))
        op.execute(text(
            f"CREATE POLICY {tbl}_isolation ON {tbl} FOR ALL "
            "USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)"
        ))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS campaign_knowledge_nodes"))
    op.execute(text("DROP TABLE IF EXISTS campaign_knowledge_sources"))
    op.execute(text(
        "ALTER TABLE campaigns "
        "DROP COLUMN IF EXISTS knowledge_mode, "
        "DROP COLUMN IF EXISTS knowledge_model"
    ))
