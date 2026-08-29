"""require four-eye approval for manual inbound charge finalization

Revision ID: 0034_inbound_billing_four_eye
Revises: 0033_bootstrap_contract_repair
Create Date: 2026-08-28 00:00:00.000000

Manual release of an unanswered hold remains a single-admin, no-charge safety
operation.  A manual finalize can create money-bearing usage, so its evidence
is first pinned by one platform administrator and may only be approved by a
different platform administrator in a later, independently idempotent request.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0034_inbound_billing_four_eye"
down_revision: str | None = "0033_bootstrap_contract_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TENANT_POLICY = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)


def _canonical_rls(table: str) -> None:
    op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
    op.execute(
        text(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (
                    COALESCE(
                        NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                        FALSE
                    )
                    OR tenant_id = NULLIF(
                        current_setting('app.current_tenant_id', TRUE), ''
                    )::uuid
                )
                WITH CHECK (
                    COALESCE(
                        NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                        FALSE
                    )
                    OR tenant_id = NULLIF(
                        current_setting('app.current_tenant_id', TRUE), ''
                    )::uuid
                )
            """
        )
    )


def _repair_billing_ledger_immutability() -> None:
    """Make the top-up ledger append-only for tenant and service contexts.

    0033 originally gave every repaired tenant table one permissive ``ALL``
    policy.  That was wrong for a financial ledger: the service bypass could
    UPDATE or DELETE credited minutes.  Command-specific RLS prevents ordinary
    mutations, while the trigger also protects table owners/BYPASSRLS sessions
    that do not pass through RLS.
    """

    op.execute(
        text(
            """
            DO $policy$
            DECLARE existing_policy record;
            BEGIN
                FOR existing_policy IN
                    SELECT policyname
                    FROM pg_policies
                    WHERE schemaname='public' AND tablename='billing_ledger'
                LOOP
                    EXECUTE format(
                        'DROP POLICY IF EXISTS %I ON public.billing_ledger',
                        existing_policy.policyname
                    );
                END LOOP;
            END;
            $policy$;
            """
        )
    )
    op.execute(text("ALTER TABLE billing_ledger ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE billing_ledger FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY billing_ledger_select ON billing_ledger FOR SELECT "
            f"USING ({_TENANT_POLICY})"
        )
    )
    op.execute(
        text(
            "CREATE POLICY billing_ledger_insert ON billing_ledger FOR INSERT "
            f"WITH CHECK ({_TENANT_POLICY})"
        )
    )
    op.execute(
        text("CREATE POLICY billing_ledger_update ON billing_ledger " "FOR UPDATE USING (FALSE)")
    )
    op.execute(
        text("CREATE POLICY billing_ledger_delete ON billing_ledger " "FOR DELETE USING (FALSE)")
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION public.prevent_billing_ledger_mutation()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION
                    'billing_ledger is append-only; write a compensating entry'
                    USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(text("DROP TRIGGER IF EXISTS billing_ledger_immutable ON billing_ledger"))
    op.execute(
        text(
            "CREATE TRIGGER billing_ledger_immutable "
            "BEFORE UPDATE OR DELETE ON billing_ledger "
            "FOR EACH ROW EXECUTE FUNCTION "
            "public.prevent_billing_ledger_mutation()"
        )
    )
    op.execute(text("ALTER TABLE billing_ledger " "ENABLE ALWAYS TRIGGER billing_ledger_immutable"))


def _validate_billing_ledger_immutability() -> None:
    connection = op.get_bind()
    policies = list(
        connection.execute(
            text(
                """
                SELECT cmd, COALESCE(qual, '') AS qual,
                       COALESCE(with_check, '') AS with_check
                FROM pg_policies
                WHERE schemaname='public' AND tablename='billing_ledger'
                """
            )
        ).mappings()
    )
    by_command = {row["cmd"]: row for row in policies}
    if (
        len(policies) != 4
        or "app.bypass_rls" not in by_command.get("SELECT", {}).get("qual", "")
        or "app.bypass_rls" not in by_command.get("INSERT", {}).get("with_check", "")
        or by_command.get("UPDATE", {}).get("qual") != "false"
        or by_command.get("DELETE", {}).get("qual") != "false"
    ):
        raise RuntimeError(
            "0034 failed to install billing_ledger read/append and " "deny-mutation policies"
        )

    function_contract = (
        connection.execute(
            text(
                """
                SELECT p.prorettype::regtype::text AS return_type, p.pronargs
                FROM pg_proc AS p
                WHERE p.oid=to_regprocedure(
                    'public.prevent_billing_ledger_mutation()'
                )
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    trigger_contract = (
        connection.execute(
            text(
                """
                SELECT t.tgtype, t.tgenabled::text AS tgenabled,
                       t.tgfoid=to_regprocedure(
                           'public.prevent_billing_ledger_mutation()'
                       ) AS canonical_function
                FROM pg_trigger AS t
                WHERE t.tgrelid='public.billing_ledger'::regclass
                  AND t.tgname='billing_ledger_immutable'
                  AND NOT t.tgisinternal
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        function_contract is None
        or function_contract["return_type"] != "trigger"
        or function_contract["pronargs"] != 0
        or trigger_contract is None
        or trigger_contract["tgtype"] != 27
        or trigger_contract["tgenabled"] != "A"
        or not trigger_contract["canonical_function"]
    ):
        raise RuntimeError(
            "0034 failed to install the always-enabled " "billing_ledger immutability trigger"
        )


def _validate_hold_finalize_approval_immutability() -> None:
    connection = op.get_bind()
    function_contract = (
        connection.execute(
            text(
                """
                SELECT p.prorettype::regtype::text AS return_type, p.pronargs
                FROM pg_proc AS p
                WHERE p.oid=to_regprocedure(
                    'public.enforce_inbound_hold_finalize_approval_transition()'
                )
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    trigger_contract = (
        connection.execute(
            text(
                """
                SELECT t.tgtype, t.tgenabled::text AS tgenabled,
                       t.tgfoid=to_regprocedure(
                           'public.enforce_inbound_hold_finalize_approval_transition()'
                       ) AS canonical_function
                FROM pg_trigger AS t
                WHERE t.tgrelid=
                    'public.inbound_billing_hold_finalize_approvals'::regclass
                  AND t.tgname='inbound_hold_finalize_approval_transition'
                  AND NOT t.tgisinternal
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        function_contract is None
        or function_contract["return_type"] != "trigger"
        or function_contract["pronargs"] != 0
        or trigger_contract is None
        or trigger_contract["tgtype"] != 27
        or trigger_contract["tgenabled"] != "A"
        or not trigger_contract["canonical_function"]
    ):
        raise RuntimeError(
            "0034 failed to install the always-enabled inbound billing-hold "
            "approval immutability trigger"
        )


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE TABLE inbound_billing_hold_finalize_approvals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL
                    REFERENCES tenants(id) ON DELETE RESTRICT,
                call_id UUID NOT NULL,
                hold_reason VARCHAR(64) NOT NULL,
                evidence_type VARCHAR(32) NOT NULL,
                evidence_reference VARCHAR(255) NOT NULL,
                evidence_sha256 CHAR(64) NOT NULL,
                adjudication_reason TEXT NOT NULL,
                authoritative_duration_seconds INTEGER NOT NULL,
                authoritative_cost DECIMAL(10,4),
                authoritative_currency VARCHAR(3),
                resolution_hash CHAR(64) NOT NULL,
                requested_by UUID NOT NULL
                    REFERENCES user_profiles(id) ON DELETE RESTRICT,
                request_id VARCHAR(255) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                approved_by UUID
                    REFERENCES user_profiles(id) ON DELETE RESTRICT,
                approval_idempotency_key VARCHAR(255),
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                approved_at TIMESTAMPTZ,
                CONSTRAINT inbound_hold_finalize_call_tenant_fk
                    FOREIGN KEY (call_id, tenant_id)
                    REFERENCES calls(id, tenant_id) ON DELETE RESTRICT,
                CONSTRAINT inbound_hold_finalize_call_unique
                    UNIQUE (tenant_id, call_id),
                CONSTRAINT inbound_hold_finalize_request_key_unique
                    UNIQUE (requested_by, request_id),
                CONSTRAINT inbound_hold_finalize_reason_valid CHECK (
                    hold_reason IN (
                        'provider_answer_ambiguous',
                        'usage_exceeded_reservation'
                    )
                ),
                CONSTRAINT inbound_hold_finalize_evidence_valid CHECK (
                    (hold_reason = 'provider_answer_ambiguous'
                        AND evidence_type = 'carrier_cdr')
                    OR (hold_reason = 'usage_exceeded_reservation'
                        AND evidence_type = 'provider_usage_record')
                ),
                CONSTRAINT inbound_hold_finalize_duration_nonnegative CHECK (
                    authoritative_duration_seconds >= 0
                ),
                CONSTRAINT inbound_hold_finalize_cost_currency_pair CHECK (
                    (authoritative_cost IS NULL
                        AND authoritative_currency IS NULL)
                    OR (authoritative_cost >= 0
                        AND authoritative_currency ~ '^[A-Z]{3}$')
                ),
                CONSTRAINT inbound_hold_finalize_status_valid CHECK (
                    status IN ('pending', 'approved')
                ),
                CONSTRAINT inbound_hold_finalize_approval_state_valid CHECK (
                    (status = 'pending'
                        AND approved_by IS NULL
                        AND approval_idempotency_key IS NULL
                        AND approved_at IS NULL)
                    OR (status = 'approved'
                        AND approved_by IS NOT NULL
                        AND approved_by <> requested_by
                        AND length(approval_idempotency_key) BETWEEN 8 AND 255
                        AND approved_at IS NOT NULL)
                )
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX inbound_hold_finalize_approval_key_unique "
            "ON inbound_billing_hold_finalize_approvals "
            "(approved_by, approval_idempotency_key) "
            "WHERE approval_idempotency_key IS NOT NULL"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_hold_finalize_pending "
            "ON inbound_billing_hold_finalize_approvals "
            "(tenant_id, requested_at) WHERE status='pending'"
        )
    )
    _canonical_rls("inbound_billing_hold_finalize_approvals")

    # The evidence and requester fields are immutable after insert.  The only
    # legal transition is pending -> approved, and the constraint above plus
    # this trigger makes the distinct-approver rule survive application bugs.
    op.execute(
        text(
            """
            CREATE FUNCTION enforce_inbound_hold_finalize_approval_transition()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION
                        'inbound billing-hold approvals cannot be deleted';
                END IF;
                IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
                    OR OLD.call_id IS DISTINCT FROM NEW.call_id
                    OR OLD.hold_reason IS DISTINCT FROM NEW.hold_reason
                    OR OLD.evidence_type IS DISTINCT FROM NEW.evidence_type
                    OR OLD.evidence_reference IS DISTINCT FROM NEW.evidence_reference
                    OR OLD.evidence_sha256 IS DISTINCT FROM NEW.evidence_sha256
                    OR OLD.adjudication_reason IS DISTINCT FROM NEW.adjudication_reason
                    OR OLD.authoritative_duration_seconds IS DISTINCT FROM
                        NEW.authoritative_duration_seconds
                    OR OLD.authoritative_cost IS DISTINCT FROM NEW.authoritative_cost
                    OR OLD.authoritative_currency IS DISTINCT FROM
                        NEW.authoritative_currency
                    OR OLD.resolution_hash IS DISTINCT FROM NEW.resolution_hash
                    OR OLD.requested_by IS DISTINCT FROM NEW.requested_by
                    OR OLD.request_id IS DISTINCT FROM NEW.request_id
                    OR OLD.requested_at IS DISTINCT FROM NEW.requested_at
                    OR OLD.status <> 'pending'
                    OR NEW.status <> 'approved'
                    OR NEW.approved_by IS NULL
                    OR NEW.approved_by = OLD.requested_by
                    OR NEW.approval_idempotency_key IS NULL
                    OR NEW.approved_at IS NULL
                THEN
                    RAISE EXCEPTION
                        'invalid inbound billing-hold approval transition';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        text(
            "CREATE TRIGGER inbound_hold_finalize_approval_transition "
            "BEFORE UPDATE OR DELETE "
            "ON inbound_billing_hold_finalize_approvals "
            "FOR EACH ROW EXECUTE FUNCTION "
            "enforce_inbound_hold_finalize_approval_transition()"
        )
    )
    op.execute(
        text(
            "ALTER TABLE inbound_billing_hold_finalize_approvals "
            "ENABLE ALWAYS TRIGGER inbound_hold_finalize_approval_transition"
        )
    )
    _validate_hold_finalize_approval_immutability()
    _repair_billing_ledger_immutability()
    _validate_billing_ledger_immutability()


def downgrade() -> None:
    op.execute(
        text("LOCK TABLE inbound_billing_hold_finalize_approvals " "IN ACCESS EXCLUSIVE MODE")
    )
    retained = int(
        op.get_bind()
        .execute(text("SELECT count(*) FROM " "inbound_billing_hold_finalize_approvals"))
        .scalar()
        or 0
    )
    if retained:
        raise RuntimeError(
            "Refusing to downgrade 0034: "
            f"{retained} four-eye billing approval row(s) would be lost"
        )
    op.execute(text("DROP TABLE inbound_billing_hold_finalize_approvals"))
    op.execute(text("DROP FUNCTION enforce_inbound_hold_finalize_approval_transition()"))
    # billing_ledger immutability is also part of patched 0033.  It deliberately
    # survives a safe, empty 0034 downgrade so the marker and schema continue
    # to describe the append-only financial boundary.
