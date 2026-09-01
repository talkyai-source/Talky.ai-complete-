"""quarantine inbound duration with no durable Answer proof

Revision ID: 0037_inbound_duration_quarantine
Revises: 0036_inbound_rejection_log
Create Date: 2026-09-02 00:00:00.000000

The former live path measured from Answer intent and could keep measuring
through indefinitely delayed teardown.  Rows that never persisted Answer truth
therefore acquired fictional usage.  Reserved/held rows are deliberately not
charged until carrier evidence resolves them; this migration makes their calls
projection agree with that billing hold and records the prior value first.

Finalized/released/reversed rows are excluded because changing an immutable
settlement requires a separate, evidence-backed reversal transaction.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0037_inbound_duration_quarantine"
down_revision: str | None = "0036_inbound_rejection_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UNPROVEN_DURATION_PREDICATE = """
direction='inbound'
AND answered_at IS NULL
AND billing_status IN ('reserved','held')
AND COALESCE(duration_seconds,0) <> 0
""".strip()


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE calls "
            "ADD COLUMN provider_terminated_at TIMESTAMPTZ"
        )
    )
    op.execute(
        text(
            "COMMENT ON COLUMN calls.provider_terminated_at IS "
            "'Authoritative PBX parent-leg absence timestamp; NULL means "
            "ended_at is only a projection and is not billing proof.'"
        )
    )

    conn = op.get_bind()
    prior_bypass = str(
        conn.execute(
            text(
                "SELECT COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), ''), 'off')"
            )
        ).scalar()
        or "off"
    )
    conn.execute(text("SELECT set_config('app.bypass_rls', 'on', TRUE)"))
    # Preserve the exact value and state before correcting the mutable calls
    # projection. inbound_audit_events is append-only and tenant-scoped.
    try:
        op.execute(
            text(
                f"""
                INSERT INTO inbound_audit_events (
                    tenant_id, event_type, actor_id, actor_role,
                    resource_type, resource_id, reason,
                    before_state, after_state, metadata, idempotency_key
                )
                SELECT
                    tenant_id,
                    'billing_duration_quarantined',
                    NULL,
                    'migration',
                    'call',
                    id,
                    'historical_missing_answer_proof_duration_quarantined',
                    jsonb_build_object(
                        'duration_seconds', duration_seconds,
                        'answered_at', answered_at,
                        'ended_at', ended_at,
                        'status', status,
                        'processing_status', processing_status,
                        'billing_status', billing_status,
                        'reserved_seconds', reserved_seconds
                    ),
                    jsonb_build_object(
                        'duration_seconds', 0,
                        'answered_at', answered_at,
                        'ended_at', ended_at,
                        'status', status,
                        'processing_status', processing_status,
                        'billing_status', billing_status,
                        'reserved_seconds', reserved_seconds
                    ),
                    jsonb_build_object(
                        'migration_revision', '0037_inbound_duration_quarantine',
                        'settlement_changed', FALSE,
                        'requires_carrier_evidence', TRUE
                    ),
                    'migration:0037:duration-quarantine:' || id::text
                FROM calls
                WHERE {_UNPROVEN_DURATION_PREDICATE}
                """
            )
        )
        op.execute(
            text(
                f"""
                UPDATE calls
                SET duration_seconds=0,
                    updated_at=NOW()
                WHERE {_UNPROVEN_DURATION_PREDICATE}
                """
            )
        )
    finally:
        conn.execute(
            text("SELECT set_config('app.bypass_rls', :prior, TRUE)"),
            {"prior": prior_bypass},
        )


def downgrade() -> None:
    # The discarded value was explicitly unproven. Replaying it would invent
    # usage during rollback; the append-only audit remains the evidence trail.
    return None
