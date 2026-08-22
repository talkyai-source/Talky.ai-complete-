"""store prompt bodies so a version can actually be rolled back to

goals.md §6: "Add rollback to the previous approved prompt version."

WHY A TABLE AND NOT JUST A FLAG
-------------------------------
`cc19971a` gave every version an identity — a name and a content hash — which is
enough to say *which* prompt a call ran on. It is not enough to go back to one.
The prompt bodies live in Python modules, so the only route to `lead_gen@1` after
`lead_gen@2` shipped was a git revert and a redeploy. That is the thing rollback
exists to avoid, and it fails at exactly the moment it is needed: a QA batch goes
badly at 9pm and the fix requires a release.

So the body is captured the first time a version composes. `lead_gen@1`'s text is
already gone from HEAD — that one is unrecoverable and honestly so — but from now
on every version that runs is retrievable, and pinning a campaign to an older one
is a database write rather than a deploy.

WHAT IS STORED
--------------
The raw template body, with its ``{agent_name}`` / ``{company_name}``
placeholders intact — NOT a composed prompt. A composed prompt is per-campaign
(different company, different slots); the template is the thing a version names.
Rolling back substitutes the template and lets composition proceed normally, so a
pinned campaign still gets its own company name and slots.

``approved`` exists because §3's Safe Improvement Loop says a prompt is deployed
"only after human approval and canary testing". A version being *recorded* is a
fact about what ran; a version being *approved* is a judgement about whether it
should be rolled back to. They are not the same and the table keeps them apart.

Revision ID: 0016_prompt_version_rollback
Revises: 0015_conversation_reviews
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0016_prompt_version_rollback"
down_revision: str | None = "0015_conversation_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        CREATE TABLE IF NOT EXISTS prompt_template_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            persona_type VARCHAR(32) NOT NULL,
            template VARCHAR(64) NOT NULL,
            version VARCHAR(64) NOT NULL,
            -- The raw template, placeholders intact. Not a composed prompt.
            body TEXT NOT NULL,
            body_sha VARCHAR(64) NOT NULL,
            -- Recorded != approved. See the module docstring.
            approved BOOLEAN NOT NULL DEFAULT TRUE,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT prompt_template_versions_version_unique UNIQUE (version),
            CONSTRAINT prompt_template_versions_body_not_empty
                CHECK (length(body) > 0)
        )
    """)
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_prompt_template_versions_persona "
            "ON prompt_template_versions (persona_type, recorded_at DESC)"
        )
    )

    # Platform-global on purpose: the prompt templates are product code, shared
    # by every tenant. No tenant_id, therefore no RLS — a tenant pins WHICH
    # version it runs (below), it does not own the versions themselves.
    op.execute(
        text("""
        COMMENT ON TABLE prompt_template_versions IS
        'Raw persona template bodies captured on first use, so a campaign can be '
        'pinned to an earlier version without a code deploy. Platform-global.'
    """)
    )

    # The pin. NULL = follow whatever the code currently ships, which is the
    # behaviour every existing campaign already has.
    op.execute(
        text("""
        ALTER TABLE campaigns
            ADD COLUMN IF NOT EXISTS prompt_version_pin VARCHAR(64)
    """)
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_campaigns_prompt_pin "
            "ON campaigns (prompt_version_pin) WHERE prompt_version_pin IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS prompt_template_versions"))
    # The pin column stays: dropping it would silently un-pin any campaign that
    # was deliberately held on an older prompt, which is a behaviour change
    # disguised as a schema rollback.
