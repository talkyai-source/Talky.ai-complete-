"""make calls.lead_id nullable — a call does not always come from a lead

This finishes a fix that was written on 2026-07-17 and never applied. It lives
in ``database/migrations/20260717_calls_lead_id_nullable.sql``, in the raw-SQL
system that was frozen and archived on 2026-06-02, so it has been sitting there
unapplied ever since while the bug it describes stayed live.

ITS OWN HEADER DESCRIBES THE BUG, AND THE BUG IS STILL REAL
------------------------------------------------------------
  "Root-cause fix for 'non-dialer recordings never get a DB row': the stub
   calls row inserted for manual/PBX-originated calls that never went through
   the dialer has no lead to attach ... The column was declared NOT NULL
   REFERENCES leads(id), so that INSERT could never succeed; every non-dialer
   call fell back to disk-only storage (logged as stub_calls_row_insert_failed)
   and never appeared in the recordings UI."

That failure path is still in the code today — ``telephony/recording.py:323``
still logs ``stub_calls_row_insert_failed``. So any manually-placed or
PBX-originated call still has its recording written to disk and never shown.

FOUND AGAIN FROM THE OTHER SIDE
--------------------------------
Campaign test sessions (Alembic 0017) hit the identical wall: a browser test
call has no lead either, so its ``calls`` row insert failed with
``NotNullViolationError``. The insert is wrapped, so it would have failed
SILENTLY in production — a warning in the journal and no row — which is exactly
how the original bug survived a year.

WHY THIS IS SAFE
----------------
Widening NOT NULL to NULL cannot invalidate an existing row, and every reader
already handles the absence: calls.py:322 and :670, postgres_adapter.py:922 and
call_service.py:386 all read it as ``str(row["lead_id"]) if row["lead_id"] else
None``. The code was written expecting nullable; only the constraint disagreed.

The foreign key and ON DELETE behaviour are untouched — a lead_id that IS
present still has to reference a real lead.

Revision ID: 0018_calls_lead_id_nullable
Revises: 0017_test_calls
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0018_calls_lead_id_nullable"
down_revision: str | None = "0017_test_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE calls ALTER COLUMN lead_id DROP NOT NULL"))
    op.execute(
        text("""
        COMMENT ON COLUMN calls.lead_id IS
        'The lead this call was placed for, when there is one. NULL for calls '
        'that did not come from the dialer: manual/PBX-originated calls and '
        'campaign test sessions.'
    """)
    )


def downgrade() -> None:
    # Restoring NOT NULL would fail outright if any NULL rows exist, and if it
    # succeeded it would re-break every non-dialer recording. Deliberately a
    # no-op: this is a constraint that should never have been there.
    pass
