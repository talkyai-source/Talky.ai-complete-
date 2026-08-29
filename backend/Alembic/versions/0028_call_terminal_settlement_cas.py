"""make outbound terminal settlement monotonic and exactly once

Revision ID: 0028_call_terminal_settle_cas
Revises: 0027_media_delete_request_keys
Create Date: 2026-08-27 00:00:00.000000

``calls.status`` is shared by provider callbacks and operator hangup
endpoints.  The first terminal status must remain authoritative, while the
outbound lead/job/campaign settlement still has to run exactly once when an
endpoint wrote ``ended`` before the provider callback.  ``terminal_settled_at``
is that separate durable commit marker and the retry columns form its small
database outbox.

Existing outbound terminal rows predate this invariant.  They are marked at
the migration cutover so the new recovery scan cannot replay historical lead
attempts or campaign counters.  The ALTER/backfill transaction holds the
table lock: writers committed before cutover are included, while writers
after cutover receive the nullable default and are recoverable.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0028_call_terminal_settle_cas"
down_revision: str | None = "0027_media_delete_request_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FIRST_TERMINAL_FUNCTION = r"""
CREATE OR REPLACE FUNCTION public.update_call_status(
    p_call_uuid UUID,
    p_outcome TEXT,
    p_duration INT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_call RECORD;
    v_updated RECORD;
    v_applied BOOLEAN := FALSE;
    v_terminal_statuses CONSTANT TEXT[] := ARRAY[
        'ended','completed','failed','cancelled','canceled','rejected',
        'busy','no_answer'
    ];
BEGIN
    SELECT id, lead_id, campaign_id, status, outcome, ended_at,
           duration_seconds, terminal_settled_at, terminal_retry_payload,
           terminal_retry_enqueued_at
      INTO v_call
      FROM public.calls
     WHERE id = p_call_uuid
     FOR UPDATE;

    IF NOT FOUND THEN
        RETURN json_build_object(
            'found', false,
            'applied', false,
            'durable', false,
            'call_id', p_call_uuid
        );
    END IF;

    -- This compatibility RPC owns only the monotonic call projection. Full
    -- lead/job/campaign/outbox settlement belongs to CallService's pooled
    -- transaction, so this function never manufactures terminal_settled_at.
    IF v_call.status = ANY(v_terminal_statuses) THEN
        UPDATE public.calls
           SET outcome = COALESCE(outcome, p_outcome),
               duration_seconds = COALESCE(duration_seconds, p_duration),
               ended_at = COALESCE(ended_at, NOW()),
               updated_at = NOW()
         WHERE id = p_call_uuid
         RETURNING status, outcome, ended_at, terminal_settled_at,
                   terminal_retry_payload, terminal_retry_enqueued_at
              INTO v_updated;
    ELSE
        UPDATE public.calls
           SET status = 'completed',
               outcome = p_outcome,
               duration_seconds = COALESCE(p_duration, duration_seconds),
               ended_at = COALESCE(ended_at, NOW()),
               updated_at = NOW()
         WHERE id = p_call_uuid
           AND NOT (COALESCE(status, '') = ANY(v_terminal_statuses))
         RETURNING status, outcome, ended_at, terminal_settled_at,
                   terminal_retry_payload, terminal_retry_enqueued_at
              INTO v_updated;
        v_applied := FOUND;
    END IF;

    RETURN json_build_object(
        'found', true,
        'applied', v_applied,
        'durable', (
            v_updated.status = ANY(v_terminal_statuses)
            AND v_updated.outcome IS NOT NULL
            AND v_updated.ended_at IS NOT NULL
            AND v_updated.terminal_settled_at IS NOT NULL
            AND (
                v_updated.terminal_retry_payload IS NULL
                OR v_updated.terminal_retry_enqueued_at IS NOT NULL
            )
        ),
        'call_id', v_call.id,
        'lead_id', v_call.lead_id,
        'campaign_id', v_call.campaign_id,
        'status', v_updated.status,
        'outcome', v_updated.outcome,
        'settlement_required', (v_updated.terminal_settled_at IS NULL)
    );
END;
$$;
"""


_OPTIONAL_RPC_ROLE_GRANTS = r"""
DO $migration$
BEGIN
    -- ``authenticated`` and ``service_role`` exist in Supabase-style
    -- deployments, but Talky.ai's self-hosted PostgreSQL baseline and CI use
    -- an ordinary application role.  A migration must not make those
    -- optional platform roles a prerequisite for upgrading the database.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION '
                'public.update_call_status(UUID, TEXT, INT) TO authenticated';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION '
                'public.update_call_status(UUID, TEXT, INT) TO service_role';
    END IF;
END;
$migration$;
"""


_DOWNGRADE_GUARD = r"""
DO $migration$
BEGIN
    -- Rolling application code back while a terminal projection still needs
    -- settlement, or while its retry intent has not reached the queue, would
    -- strand durable recovery state that the pre-0028 path does not honor.
    IF EXISTS (
        SELECT 1
        FROM public.calls
        WHERE direction = 'outbound'
          AND (
                (
                    status = ANY(ARRAY[
                        'ended','completed','failed','cancelled','canceled',
                        'rejected','busy','no_answer'
                    ]::text[])
                    AND terminal_settled_at IS NULL
                )
                OR (
                    terminal_retry_payload IS NOT NULL
                    AND terminal_retry_enqueued_at IS NULL
                )
          )
    ) THEN
        RAISE EXCEPTION
            '0028 downgrade refused: unresolved terminal settlement or retry intent exists';
    END IF;

    -- Even a quiescent rollback cannot safely restore the pre-0028 RPC. That
    -- function overwrites an existing terminal projection and increments the
    -- lead attempt counter on every replay. Retaining the 0028 revision is the
    -- only safe automated outcome; a future replacement rollback would need a
    -- separately reviewed monotonic compatibility contract.
    RAISE EXCEPTION
        '0028 downgrade refused: pre-0028 update_call_status is non-monotonic and replay-unsafe';
END;
$migration$;
"""


def upgrade() -> None:
    op.execute(
        text(
            """
            ALTER TABLE public.calls
            ADD COLUMN IF NOT EXISTS dialer_job_id UUID
                REFERENCES public.dialer_jobs(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS terminal_settled_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS terminal_retry_payload JSONB,
            ADD COLUMN IF NOT EXISTS terminal_retry_enqueued_at TIMESTAMPTZ
            """
        )
    )
    # ``dialer_job_id`` existed as a standalone SQL migration but was absent
    # from the Alembic chain and complete schema. Ensure both upgraded and
    # freshly bootstrapped databases can execute the canonical settlement
    # SELECT, and recover the link for preexisting calls where possible.
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_calls_dialer_job_id
                ON public.calls(dialer_job_id)
             WHERE dialer_job_id IS NOT NULL
            """
        )
    )
    op.execute(
        text(
            """
            UPDATE public.calls AS c
               SET dialer_job_id = linked.id
              FROM (
                    SELECT DISTINCT ON (call_id) call_id, id
                      FROM public.dialer_jobs
                     WHERE call_id IS NOT NULL
                     ORDER BY call_id, created_at DESC, id DESC
                   ) AS linked
             WHERE c.id = linked.call_id
               AND c.dialer_job_id IS NULL
            """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN public.calls.terminal_settled_at IS
            'Proof that outbound call, lead, job, campaign and retry-outbox intent committed; full durability also requires any retry payload to be acknowledged'
            """
        )
    )
    op.execute(
        text(
            """
            UPDATE public.calls
               SET terminal_settled_at = CURRENT_TIMESTAMP
             WHERE direction = 'outbound'
               AND status = ANY(ARRAY[
                   'ended','completed','failed','cancelled','canceled',
                   'rejected','busy','no_answer'
               ]::text[])
               AND terminal_settled_at IS NULL
            """
        )
    )
    op.execute(text(_FIRST_TERMINAL_FUNCTION))
    op.execute(text(_OPTIONAL_RPC_ROLE_GRANTS))


def downgrade() -> None:
    # Serialize the evidence check with every call settlement writer, then
    # refuse the rollback unconditionally. Restoring the pre-0028 RPC would
    # overwrite first-terminal truth and increment lead attempts on replay.
    op.execute(text("LOCK TABLE public.calls IN ACCESS EXCLUSIVE MODE"))
    op.execute(text(_DOWNGRADE_GUARD))
