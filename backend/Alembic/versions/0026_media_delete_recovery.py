"""allow audited recovery of incomplete permanent media deletions

Revision ID: 0026_media_delete_recovery
Revises: 0025_media_hold_serialization
Create Date: 2026-08-26 00:00:00.000000

The originating actor and reason remain immutable.  Every physical-deletion
attempt records the authorized actor that performed it, allowing a different
administrator to recover a failed intent when the original account has been
disabled without weakening the durable audit trail.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0026_media_delete_recovery"
down_revision: str | None = "0025_media_hold_serialization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This audit table has FORCE RLS. Migration roles are intentionally not
    # assumed to be superusers/BYPASSRLS; pin the same transaction-local,
    # cross-tenant context used by trusted backend maintenance jobs.
    op.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    op.execute(
        text(
            "SELECT set_config('app.current_tenant_id', "
            "'00000000-0000-0000-0000-000000000000', true)"
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE public.admin_media_deletion_intents
            ADD COLUMN IF NOT EXISTS attempt_actor_ids UUID[]
            """
        )
    )
    op.execute(
        text(
            """
            UPDATE public.admin_media_deletion_intents
               SET attempt_actor_ids = array_fill(actor_id, ARRAY[attempt_count])
             WHERE attempt_actor_ids IS NULL
                OR cardinality(attempt_actor_ids) = 0
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE public.admin_media_deletion_intents
            ALTER COLUMN attempt_actor_ids SET NOT NULL
            """
        )
    )
    op.execute(
        text(
            """
            DO $migration$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid =
                          'public.admin_media_deletion_intents'::regclass
                      AND conname =
                          'admin_media_deletion_attempt_actors_check'
                ) THEN
                    ALTER TABLE public.admin_media_deletion_intents
                    ADD CONSTRAINT admin_media_deletion_attempt_actors_check
                    CHECK (
                        cardinality(attempt_actor_ids) = attempt_count
                        AND cardinality(attempt_actor_ids) > 0
                        AND array_position(attempt_actor_ids, NULL) IS NULL
                    );
                END IF;
            END;
            $migration$
            """
        )
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION
                public.maintain_admin_media_deletion_attempt_actors()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.attempt_actor_ids IS NULL
                       OR cardinality(NEW.attempt_actor_ids) = 0 THEN
                        NEW.attempt_actor_ids := array_fill(
                            NEW.actor_id,
                            ARRAY[NEW.attempt_count]
                        );
                    END IF;
                ELSIF NEW.attempt_count = OLD.attempt_count + 1
                      AND NEW.attempt_actor_ids
                          IS NOT DISTINCT FROM OLD.attempt_actor_ids THEN
                    NEW.attempt_actor_ids := array_append(
                        OLD.attempt_actor_ids,
                        NEW.actor_id
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        text(
            """
            DROP TRIGGER IF EXISTS
                trg_maintain_admin_media_deletion_attempt_actors
            ON public.admin_media_deletion_intents
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_maintain_admin_media_deletion_attempt_actors
            BEFORE INSERT OR UPDATE OF attempt_count, attempt_actor_ids
            ON public.admin_media_deletion_intents
            FOR EACH ROW EXECUTE FUNCTION
                public.maintain_admin_media_deletion_attempt_actors()
            """
        )
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION public.protect_admin_media_deletion_intent()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'admin_media_deletion_intents is an immutable audit record';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.call_id IS DISTINCT FROM OLD.call_id
                   OR NEW.resource_type IS DISTINCT FROM OLD.resource_type
                   OR NEW.resource_id IS DISTINCT FROM OLD.resource_id
                   OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                   OR NEW.reason IS DISTINCT FROM OLD.reason
                   OR NEW.resource_snapshot IS DISTINCT FROM OLD.resource_snapshot
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'admin media deletion audit fields are immutable';
                END IF;
                IF NOT (
                    NEW.status = OLD.status
                    OR (OLD.status = 'intent_committed' AND NEW.status IN ('object_deleted', 'failed'))
                    OR (OLD.status = 'failed' AND NEW.status IN ('intent_committed', 'object_deleted'))
                    OR (OLD.status = 'object_deleted' AND NEW.status = 'completed')
                ) THEN
                    RAISE EXCEPTION 'invalid admin media deletion status transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                IF NEW.attempt_count < OLD.attempt_count
                   OR NEW.attempt_count > OLD.attempt_count + 1
                   OR cardinality(NEW.attempt_actor_ids) <> NEW.attempt_count
                   OR NEW.attempt_actor_ids[1:OLD.attempt_count]
                      IS DISTINCT FROM OLD.attempt_actor_ids
                   OR (OLD.object_deleted_at IS NOT NULL
                       AND NEW.object_deleted_at IS DISTINCT FROM OLD.object_deleted_at)
                   OR (OLD.completed_at IS NOT NULL
                       AND NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN
                    RAISE EXCEPTION 'admin media deletion progress cannot move backwards';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )


def downgrade() -> None:
    """Retain attempt actors because they are irreversible audit evidence.

    The retained compatibility trigger fills the actor history for pre-0026
    INSERTs and appends the immutable origin actor when old retry SQL only
    increments ``attempt_count``. A later re-upgrade is idempotent.
    """
