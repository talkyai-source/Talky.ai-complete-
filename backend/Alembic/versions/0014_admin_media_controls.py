"""admin media access and persistent outbound-call pause control

Revision ID: 0014_admin_media_controls
Revises: 0013_canonical_rls_policies
Create Date: 2026-08-22 00:00:00.000000

The preceding canonical-RLS migration already makes ``app.bypass_rls`` work for
every tenant-scoped table, including the recording and feedback tables used by
the Admin panel. This migration therefore only needs to add the shared runtime
control row.

The runtime-control row replaces the old process-local pause boolean. A single
database row is visible to every API and dialer worker, survives restarts, and
can therefore be enforced by CallGuard instead of only changing a dashboard
label.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0014_admin_media_controls"
down_revision: str | None = "0013_canonical_rls_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        CREATE TABLE IF NOT EXISTS platform_runtime_controls (
            id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            outbound_calls_paused BOOLEAN NOT NULL DEFAULT FALSE,
            paused_at TIMESTAMPTZ,
            paused_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
            pause_reason TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
    )
    op.execute(
        text("""
        INSERT INTO platform_runtime_controls (id, outbound_calls_paused)
        VALUES (1, FALSE)
        ON CONFLICT (id) DO NOTHING
        """)
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS platform_runtime_controls"))
