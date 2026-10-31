"""per-conversation reviews, a reward ledger, and prompt identity on the call

goals.md §3 (P0: "Per-conversation review and feedback storage") plus the
prompt-identity columns §3 depends on.

WHY THE CALLS COLUMNS ARE HERE AND NOT IN A LATER MIGRATION
------------------------------------------------------------
§3 requires "Record prompt version, model, campaign and call trace with the
review", and the Safe Improvement Loop is built on "aggregate reviews by prompt
version and failure category". ``cc19971a`` computes and logs the prompt
identity per call, but logs rotate and cannot be joined. Without these three
columns every review would store NULL for the exact field the whole feature
aggregates on — the review panel would work perfectly and be useless.

ONE REVIEW PER *USER* PER CALL, NOT ONE PER CALL
------------------------------------------------
Deliberately different from ``call_feedback`` (one voice note per call). §3 says
"Allow one active review per user per call; edits update the same review", so two
reviewers can independently rate the same conversation — which is the point, if
you ever want to know whether they agree. UNIQUE (call_id, user_id) enforces it;
edits are an UPDATE of the same row.

REWARDS ARE A LEDGER, AND THE LEDGER IS WHAT MAKES EDITS SAFE
-------------------------------------------------------------
§3: "Create a review-reward ledger rather than directly changing balances" and
"Review edits preserve the original reward transaction". Both are enforced by
UNIQUE (review_id) rather than by application logic: a second award for the same
review cannot be inserted, so editing a review a hundred times still credits
once. A balance is then SUM(points) over the ledger — derived, auditable, and
impossible to double-count.

TAGS ARE CONSTRAINED, NOT FREE TEXT
------------------------------------
An unconstrained TEXT[] lets a typo become a twelfth category, and aggregation
splits silently across "response_too_long" and "response_to_long" with nothing
to notice. The CHECK pins the eleven tags §3 lists; adding a twelfth is a
migration, which is the correct amount of friction.

Revision ID: 0015_conversation_reviews
Revises: 0014_admin_media_controls
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0015_conversation_reviews"
down_revision: str | None = "0014_admin_media_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Exactly the eleven tags in goals.md §3, in the order they are listed there.
REVIEW_TAGS = (
    "agent_did_not_understand",
    "agent_interrupted_caller",
    "agent_did_not_answer_question",
    "response_too_long",
    "response_too_slow",
    "agent_repeated_itself",
    "wrong_qualification_question",
    "wrong_call_outcome",
    "poor_objection_handling",
    "incorrect_information",
    "good_conversation",
)

# Same canonical shape as 0013. New tables have to opt in explicitly — 0013
# only reached tables that already had RLS enabled when it ran.
_TENANT_POLICY = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)


def _tags_sql() -> str:
    return ", ".join(f"'{t}'" for t in REVIEW_TAGS)


def upgrade() -> None:
    # ── prompt identity on the call (task #90) ──────────────────────────────
    # Nullable: 666 completed calls already exist and cannot be back-filled,
    # since the prompt they ran on was never recorded. NULL here honestly means
    # "placed before we tracked this" rather than "no prompt".
    op.execute(
        text("""
        ALTER TABLE calls
            ADD COLUMN IF NOT EXISTS prompt_template VARCHAR(64),
            ADD COLUMN IF NOT EXISTS prompt_version  VARCHAR(64),
            ADD COLUMN IF NOT EXISTS prompt_hash     VARCHAR(32)
    """)
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_calls_prompt_version "
            "ON calls (prompt_version) WHERE prompt_version IS NOT NULL"
        )
    )

    # ── the reviews ─────────────────────────────────────────────────────────
    op.execute(
        text(f"""
        CREATE TABLE IF NOT EXISTS conversation_reviews (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
            user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,

            rating SMALLINT NOT NULL,
            review_tags TEXT[] NOT NULL DEFAULT '{{}}',
            comment TEXT,

            -- Snapshot, not a join. A prompt can be re-versioned tomorrow; this
            -- review must keep pointing at what the agent actually ran on.
            prompt_template VARCHAR(64),
            prompt_version VARCHAR(64),
            prompt_hash VARCHAR(32),
            llm_model VARCHAR(128),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT conversation_reviews_one_per_user_per_call
                UNIQUE (call_id, user_id),
            CONSTRAINT conversation_reviews_rating_range
                CHECK (rating BETWEEN 1 AND 5),
            CONSTRAINT conversation_reviews_comment_length
                CHECK (comment IS NULL OR length(comment) <= 4000),
            -- <@ is "contained by": every element must be a known tag.
            CONSTRAINT conversation_reviews_tags_known
                CHECK (review_tags <@ ARRAY[{_tags_sql()}]::TEXT[])
        )
    """)
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_tenant_created "
            "ON conversation_reviews (tenant_id, created_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_call "
            "ON conversation_reviews (call_id)"
        )
    )
    # The Safe Improvement Loop reads exactly this: rating and tags grouped by
    # prompt version. Indexed so it stays cheap as reviews accumulate.
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_prompt_version "
            "ON conversation_reviews (tenant_id, prompt_version, rating) "
            "WHERE prompt_version IS NOT NULL"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_tags "
            "ON conversation_reviews USING GIN (review_tags)"
        )
    )

    # ── the reward ledger ───────────────────────────────────────────────────
    op.execute(
        text("""
        CREATE TABLE IF NOT EXISTS review_reward_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
            review_id UUID NOT NULL
                REFERENCES conversation_reviews(id) ON DELETE CASCADE,
            points INTEGER NOT NULL,
            reason VARCHAR(64) NOT NULL DEFAULT 'conversation_review',
            awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            -- THE LOAD-BEARING CONSTRAINT. One award per review, forever. An
            -- edit cannot create a second, and a retried request cannot either,
            -- without any application code being trusted to remember.
            CONSTRAINT review_reward_once_per_review UNIQUE (review_id),
            CONSTRAINT review_reward_points_positive CHECK (points > 0)
        )
    """)
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_review_reward_user_day "
            "ON review_reward_ledger (user_id, awarded_at DESC)"
        )
    )

    # ── isolation ───────────────────────────────────────────────────────────
    for table in ("conversation_reviews", "review_reward_ledger"):
        op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
        op.execute(
            text(
                f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL "
                f"USING ({_TENANT_POLICY}) WITH CHECK ({_TENANT_POLICY})"
            )
        )

    op.execute(
        text("""
        COMMENT ON TABLE conversation_reviews IS
        'Reviewer rating, structured tags and comment for one call, one row per '
        'user per call. Prompt identity is snapshotted so aggregation by prompt '
        'version survives later prompt changes.'
    """)
    )
    op.execute(
        text("""
        COMMENT ON TABLE review_reward_ledger IS
        'Append-only reward entries. UNIQUE(review_id) makes an award idempotent, '
        'so editing a review never credits twice. Balance = SUM(points).'
    """)
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS review_reward_ledger"))
    op.execute(text("DROP TABLE IF EXISTS conversation_reviews"))
    # Leave the calls columns: they carry real recorded history that a schema
    # rollback has no business discarding, and they are additive/nullable.
