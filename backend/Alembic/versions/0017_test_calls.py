"""mark test calls so they can be reviewed but never billed

A campaign test call currently writes no ``calls`` row at all, which keeps it off
the invoice but also makes it impossible to test anything that hangs off a call:
recordings, transcripts, the prompt version on the row, feedback voice notes and
conversation reviews all reference ``calls(id)``.

So test sessions get a real row, flagged. The flag is the entire safety story.

WHY THIS IS THE DANGEROUS KIND OF CHANGE
-----------------------------------------
``minutes_quota.py`` bills like this, with no status filter of any kind:

    SELECT COALESCE(SUM(duration_seconds), 0) FROM calls
     WHERE tenant_id = $1 AND created_at >= date_trunc('month', now())

Any row added to ``calls`` is billed the moment it exists. This project has
already been burned by exactly this class of defect — a phantom call recorded as
a successful conversation put the reported connect rate at more than twice the
truth, and it was never backfilled, so historical campaign performance is still
overstated.

DEFAULT FALSE, AND EXCLUSION IS EXPLICIT
-----------------------------------------
Existing rows and every real call are ``is_test = FALSE`` without anything
changing. Only the paths that count money, capacity or abuse are taught to
exclude, and each one says ``AND NOT is_test`` in plain sight so it greps.

Per-row lookups (``WHERE id = $1``) are deliberately NOT filtered: a test call
must be openable, playable, reviewable and commentable, which is the entire
point of making it a row.

The ``billable_calls`` view exists so future billing work has an obviously-safe
thing to select from rather than remembering the predicate.

Revision ID: 0017_test_calls
Revises: 0016_prompt_version_rollback
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0017_test_calls"
down_revision: str | None = "0016_prompt_version_rollback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        ALTER TABLE calls
            ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE
    """)
    )
    # Partial index on the rare side: test rows are a small minority, and every
    # money/capacity query filters them out, so this keeps those plans cheap.
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_calls_is_test "
            "ON calls (tenant_id, created_at DESC) WHERE is_test"
        )
    )
    op.execute(
        text("""
        COMMENT ON COLUMN calls.is_test IS
        'TRUE for campaign test sessions. Never billed, never counted toward '
        'concurrency, abuse or campaign statistics; still fully openable, '
        'playable and reviewable.'
    """)
    )

    # One obviously-safe thing to select from. Not a substitute for the explicit
    # predicates — a view someone forgets to use protects nobody — but it makes
    # the safe choice the easy one for anything written later.
    op.execute(
        text("""
        CREATE OR REPLACE VIEW billable_calls AS
            SELECT * FROM calls WHERE NOT is_test
    """)
    )
    op.execute(
        text("""
        COMMENT ON VIEW billable_calls IS
        'calls minus test sessions. Use for anything that charges, meters or '
        'reports customer-facing numbers.'
    """)
    )


def downgrade() -> None:
    op.execute(text("DROP VIEW IF EXISTS billable_calls"))
    # The column stays. Dropping it would silently re-bill every test session
    # ever recorded — a behaviour change disguised as a schema rollback.
