"""bind every permanent-delete idempotency key to one durable intent

Revision ID: 0027_media_delete_request_keys
Revises: 0026_media_delete_recovery
Create Date: 2026-08-26 00:00:00.000000

Cross-actor recovery must not weaken the original `(actor, Idempotency-Key)`
safety contract. This append-only alias table binds every recovery request to
exactly one intent and reason while preserving the immutable origin fields on
the intent itself.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

from alembic import op
from sqlalchemy import text

revision: str = "0027_media_delete_request_keys"
down_revision: str | None = "0026_media_delete_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EXPECTED_COLUMNS = {
    "id": ("uuid", True, "gen_random_uuid()"),
    "intent_id": ("uuid", True, None),
    "actor_id": ("uuid", True, None),
    "idempotency_key": ("character varying(255)", True, None),
    "request_reason": ("text", True, None),
    "created_at": ("timestamp with time zone", True, "now()"),
}
_EXPECTED_CONSTRAINTS = {
    "admin_media_deletion_request_keys_pkey": (
        "p",
        "PRIMARY KEY (id)",
    ),
    "admin_media_deletion_request_keys_intent_id_fkey": (
        "f",
        "FOREIGN KEY (intent_id) REFERENCES admin_media_deletion_intents(id) "
        "ON DELETE RESTRICT",
    ),
    "admin_media_deletion_request_keys_actor_id_fkey": (
        "f",
        "FOREIGN KEY (actor_id) REFERENCES user_profiles(id) ON DELETE RESTRICT",
    ),
    "admin_media_deletion_request_keys_request_reason_check": (
        "c",
        "CHECK (char_length(btrim(request_reason)) >= 8)",
    ),
    "admin_media_deletion_request_actor_key_unique": (
        "u",
        "UNIQUE (actor_id, idempotency_key)",
    ),
}
_EXPECTED_POLICIES = {
    "admin_media_deletion_request_keys_select": (
        "SELECT",
        "PERMISSIVE",
        "{public}",
        "(COALESCE((NULLIF(current_setting('app.bypass_rls'::text, true), "
        "''::text))::boolean, false) OR (EXISTS ( SELECT 1 FROM "
        "admin_media_deletion_intents i WHERE ((i.id = "
        "admin_media_deletion_request_keys.intent_id) AND (i.tenant_id = "
        "(NULLIF(current_setting('app.current_tenant_id'::text, true), "
        "''::text))::uuid)))))",
        None,
    ),
    "admin_media_deletion_request_keys_insert": (
        "INSERT",
        "PERMISSIVE",
        "{public}",
        None,
        "COALESCE((NULLIF(current_setting('app.bypass_rls'::text, true), "
        "''::text))::boolean, false)",
    ),
    "admin_media_deletion_request_keys_update": (
        "UPDATE",
        "PERMISSIVE",
        "{public}",
        "false",
        None,
    ),
    "admin_media_deletion_request_keys_delete": (
        "DELETE",
        "PERMISSIVE",
        "{public}",
        "false",
        None,
    ),
}
_EXPECTED_TRIGGERS = {
    "trg_guard_admin_media_deletion_request_key": (
        "CREATE TRIGGER trg_guard_admin_media_deletion_request_key BEFORE INSERT "
        "ON admin_media_deletion_request_keys FOR EACH ROW EXECUTE FUNCTION "
        "guard_admin_media_deletion_request_key()"
    ),
    "trg_protect_admin_media_deletion_request_key_update": (
        "CREATE TRIGGER trg_protect_admin_media_deletion_request_key_update BEFORE "
        "UPDATE ON admin_media_deletion_request_keys FOR EACH ROW EXECUTE FUNCTION "
        "protect_admin_media_deletion_request_key()"
    ),
    "trg_protect_admin_media_deletion_request_key_delete": (
        "CREATE TRIGGER trg_protect_admin_media_deletion_request_key_delete BEFORE "
        "DELETE ON admin_media_deletion_request_keys FOR EACH ROW EXECUTE FUNCTION "
        "protect_admin_media_deletion_request_key()"
    ),
}


def _rows(connection: Any, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql)).mappings().all()]


def _normalized_catalog_sql(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def _request_key_table_exists(connection: Any) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT to_regclass('public.admin_media_deletion_request_keys') "
                "IS NOT NULL"
            )
        ).scalar()
    )


def _validate_request_key_structure(connection: Any) -> None:
    relation = _rows(
        connection,
        """
        SELECT c.relkind::text AS relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'admin_media_deletion_request_keys'
        """,
    )
    if len(relation) != 1 or relation[0]["relkind"] != "r":
        raise RuntimeError(
            "admin_media_deletion_request_keys must be one ordinary table in public"
        )

    columns = {
        str(row["name"]): row
        for row in _rows(
            connection,
            """
            SELECT a.attname AS name,
                   format_type(a.atttypid, a.atttypmod) AS formatted_type,
                   a.attnotnull AS not_null,
                   pg_get_expr(d.adbin, d.adrelid) AS default_expr
            FROM pg_attribute a
            LEFT JOIN pg_attrdef d
              ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid =
                  'public.admin_media_deletion_request_keys'::regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
        )
    }
    problems: list[str] = []
    if set(columns) != set(_EXPECTED_COLUMNS):
        problems.append(
            f"columns are {sorted(columns)!r}, expected {sorted(_EXPECTED_COLUMNS)!r}"
        )
    for name in sorted(set(columns) & set(_EXPECTED_COLUMNS)):
        formatted_type, not_null, default = _EXPECTED_COLUMNS[name]
        row = columns[name]
        if (
            row["formatted_type"] != formatted_type
            or bool(row["not_null"]) != not_null
            or row["default_expr"] != default
        ):
            problems.append(f"column {name} has an incompatible definition")

    constraints = {
        str(row["name"]): row
        for row in _rows(
            connection,
            """
            SELECT conname AS name, contype::text AS constraint_type,
                   convalidated AS validated,
                   pg_get_constraintdef(oid, TRUE) AS definition
            FROM pg_constraint
            WHERE conrelid =
                  'public.admin_media_deletion_request_keys'::regclass
            ORDER BY conname
            """,
        )
    }
    if set(constraints) != set(_EXPECTED_CONSTRAINTS):
        problems.append(
            "constraints are incompatible with the request-key audit ledger"
        )
    for name in sorted(set(constraints) & set(_EXPECTED_CONSTRAINTS)):
        constraint_type, definition = _EXPECTED_CONSTRAINTS[name]
        row = constraints[name]
        if (
            row["constraint_type"] != constraint_type
            or not bool(row["validated"])
            or row["definition"] != definition
        ):
            problems.append(f"constraint {name} has an incompatible definition")
    if problems:
        raise RuntimeError(
            "Existing admin_media_deletion_request_keys schema is incompatible; "
            + "; ".join(problems)
        )


def _validate_preexisting_security(connection: Any) -> None:
    relation = _rows(
        connection,
        """
        SELECT relrowsecurity AS rls_enabled,
               relforcerowsecurity AS rls_forced
        FROM pg_class
        WHERE oid = 'public.admin_media_deletion_request_keys'::regclass
        """,
    )[0]
    if not relation["rls_enabled"] or not relation["rls_forced"]:
        raise RuntimeError("request-key audit ledger must have forced RLS")

    policies = {
        str(row["policyname"]): (
            str(row["cmd"]),
            str(row["permissive"]),
            str(row["roles"]),
            _normalized_catalog_sql(row["qual"]),
            _normalized_catalog_sql(row["with_check"]),
        )
        for row in _rows(
            connection,
            """
            SELECT policyname, cmd, permissive, roles::text AS roles,
                   qual, with_check
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = 'admin_media_deletion_request_keys'
            """,
        )
    }
    if policies != _EXPECTED_POLICIES:
        raise RuntimeError("request-key audit ledger has incompatible RLS policies")

    triggers = {
        str(row["name"]): (
            str(row["enabled"]),
            str(row["definition"]),
        )
        for row in _rows(
            connection,
            """
            SELECT tgname AS name, tgenabled::text AS enabled,
                   pg_get_triggerdef(oid, TRUE) AS definition
            FROM pg_trigger
            WHERE tgrelid =
                  'public.admin_media_deletion_request_keys'::regclass
              AND NOT tgisinternal
            """,
        )
    }
    if set(triggers) != set(_EXPECTED_TRIGGERS) or any(
        enabled != "O" or definition != _EXPECTED_TRIGGERS[name]
        for name, (enabled, definition) in triggers.items()
    ):
        raise RuntimeError("request-key audit ledger has incompatible triggers")

    index = _rows(
        connection,
        """
        SELECT ix.indisunique AS is_unique,
               ix.indisvalid AS is_valid,
               ix.indisready AS is_ready,
               pg_get_indexdef(ix.indexrelid) AS definition
        FROM pg_index ix
        WHERE ix.indexrelid = to_regclass(
            'public.idx_admin_media_deletion_request_keys_intent'
        )
        """,
    )
    expected = (
        "CREATE INDEX idx_admin_media_deletion_request_keys_intent ON "
        "public.admin_media_deletion_request_keys USING btree (intent_id, created_at)"
    )
    if (
        len(index) != 1
        or index[0]["is_unique"]
        or not index[0]["is_valid"]
        or not index[0]["is_ready"]
        or index[0]["definition"] != expected
    ):
        raise RuntimeError("request-key audit ledger intent index is incompatible")


def upgrade() -> None:
    # Both ledger tables use FORCE RLS. Do not rely on a superuser migration
    # role: make the cross-tenant backfill/audit context explicit and local to
    # this migration transaction.
    op.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    op.execute(
        text(
            "SELECT set_config('app.current_tenant_id', "
            "'00000000-0000-0000-0000-000000000000', true)"
        )
    )
    connection = op.get_bind()
    preexisting = _request_key_table_exists(connection)
    if preexisting:
        _validate_request_key_structure(connection)
        _validate_preexisting_security(connection)

    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.admin_media_deletion_request_keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                intent_id UUID NOT NULL
                    REFERENCES public.admin_media_deletion_intents(id)
                    ON DELETE RESTRICT,
                actor_id UUID NOT NULL
                    REFERENCES public.user_profiles(id) ON DELETE RESTRICT,
                idempotency_key VARCHAR(255) NOT NULL,
                request_reason TEXT NOT NULL
                    CHECK (CHAR_LENGTH(BTRIM(request_reason)) >= 8),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT admin_media_deletion_request_actor_key_unique
                    UNIQUE (actor_id, idempotency_key)
            )
            """
        )
    )
    _validate_request_key_structure(connection)
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION
                public.guard_admin_media_deletion_origin_key()
            RETURNS TRIGGER AS $$
            BEGIN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(
                        'talky:media-delete-key:' || NEW.actor_id::text || ':' ||
                        NEW.idempotency_key,
                        0
                    )
                );
                IF EXISTS (
                    SELECT 1
                    FROM public.admin_media_deletion_request_keys k
                    WHERE k.actor_id = NEW.actor_id
                      AND k.idempotency_key = NEW.idempotency_key
                      AND k.intent_id <> NEW.id
                ) THEN
                    RAISE EXCEPTION
                        'Idempotency-Key is already bound to another media deletion'
                        USING ERRCODE = 'unique_violation';
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
            CREATE OR REPLACE FUNCTION
                public.guard_admin_media_deletion_request_key()
            RETURNS TRIGGER AS $$
            BEGIN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(
                        'talky:media-delete-key:' || NEW.actor_id::text || ':' ||
                        NEW.idempotency_key,
                        0
                    )
                );
                IF EXISTS (
                    SELECT 1
                    FROM public.admin_media_deletion_intents i
                    WHERE i.actor_id = NEW.actor_id
                      AND i.idempotency_key = NEW.idempotency_key
                      AND i.id <> NEW.intent_id
                ) THEN
                    RAISE EXCEPTION
                        'Idempotency-Key is already bound to another media deletion'
                        USING ERRCODE = 'unique_violation';
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
            DROP TRIGGER IF EXISTS trg_guard_admin_media_deletion_origin_key
            ON public.admin_media_deletion_intents
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_guard_admin_media_deletion_origin_key
            BEFORE INSERT ON public.admin_media_deletion_intents
            FOR EACH ROW EXECUTE FUNCTION
                public.guard_admin_media_deletion_origin_key()
            """
        )
    )
    op.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_guard_admin_media_deletion_request_key
            ON public.admin_media_deletion_request_keys
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_guard_admin_media_deletion_request_key
            BEFORE INSERT ON public.admin_media_deletion_request_keys
            FOR EACH ROW EXECUTE FUNCTION
                public.guard_admin_media_deletion_request_key()
            """
        )
    )
    op.execute(
        text(
            """
            INSERT INTO public.admin_media_deletion_request_keys (
                intent_id, actor_id, idempotency_key, request_reason
            )
            SELECT id, actor_id, idempotency_key, reason
            FROM public.admin_media_deletion_intents
            ON CONFLICT (actor_id, idempotency_key) DO NOTHING
            """
        )
    )
    op.execute(
        text(
            """
            DO $migration$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM public.admin_media_deletion_intents i
                    JOIN public.admin_media_deletion_request_keys k
                      ON k.actor_id = i.actor_id
                     AND k.idempotency_key = i.idempotency_key
                    WHERE k.intent_id <> i.id
                       OR k.request_reason <> i.reason
                ) THEN
                    RAISE EXCEPTION
                        'media deletion request-key backfill conflict';
                END IF;
            END;
            $migration$
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS
                idx_admin_media_deletion_request_keys_intent
            ON public.admin_media_deletion_request_keys(intent_id, created_at)
            """
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.admin_media_deletion_request_keys "
            "ENABLE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.admin_media_deletion_request_keys "
            "FORCE ROW LEVEL SECURITY"
        )
    )
    for policy in (
        "admin_media_deletion_request_keys_select",
        "admin_media_deletion_request_keys_insert",
        "admin_media_deletion_request_keys_update",
        "admin_media_deletion_request_keys_delete",
    ):
        op.execute(
            text(
                f"DROP POLICY IF EXISTS {policy} "
                "ON public.admin_media_deletion_request_keys"
            )
        )
    op.execute(
        text(
            """
            CREATE POLICY admin_media_deletion_request_keys_select
            ON public.admin_media_deletion_request_keys FOR SELECT
            USING (
                COALESCE(
                    NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                    FALSE
                )
                OR EXISTS (
                    SELECT 1
                    FROM public.admin_media_deletion_intents i
                    WHERE i.id = intent_id
                      AND i.tenant_id = NULLIF(
                          current_setting('app.current_tenant_id', TRUE), ''
                      )::uuid
                )
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE POLICY admin_media_deletion_request_keys_insert
            ON public.admin_media_deletion_request_keys FOR INSERT
            WITH CHECK (
                COALESCE(
                    NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                    FALSE
                )
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE POLICY admin_media_deletion_request_keys_update
            ON public.admin_media_deletion_request_keys FOR UPDATE USING (FALSE)
            """
        )
    )
    op.execute(
        text(
            """
            CREATE POLICY admin_media_deletion_request_keys_delete
            ON public.admin_media_deletion_request_keys FOR DELETE USING (FALSE)
            """
        )
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION
                public.protect_admin_media_deletion_request_key()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION
                    'admin_media_deletion_request_keys is an immutable audit record';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    for trigger in (
        "trg_protect_admin_media_deletion_request_key_update",
        "trg_protect_admin_media_deletion_request_key_delete",
    ):
        op.execute(
            text(
                f"DROP TRIGGER IF EXISTS {trigger} "
                "ON public.admin_media_deletion_request_keys"
            )
        )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_protect_admin_media_deletion_request_key_update
            BEFORE UPDATE ON public.admin_media_deletion_request_keys
            FOR EACH ROW EXECUTE FUNCTION
                public.protect_admin_media_deletion_request_key()
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_protect_admin_media_deletion_request_key_delete
            BEFORE DELETE ON public.admin_media_deletion_request_keys
            FOR EACH ROW EXECUTE FUNCTION
                public.protect_admin_media_deletion_request_key()
            """
        )
    )
    _validate_preexisting_security(connection)


def downgrade() -> None:
    """Retain request-key bindings because they authorize irreversible work."""
