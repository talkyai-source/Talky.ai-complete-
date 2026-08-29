"""durable, idempotent admin media deletion intents

Revision ID: 0023_admin_media_deletion_safety
Revises: 0022_inbound_calling_foundation
Create Date: 2026-08-26 00:00:00.000000

Permanent media deletion crosses two systems: PostgreSQL and object/local
storage.  This table is the durable boundary between them.  The API commits an
intent here before touching audio bytes, then advances the request through the
storage and metadata stages.  The actor/idempotency and resource uniqueness
constraints make both client retries and concurrent delete attempts safe.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from alembic import op
from sqlalchemy import text

revision: str = "0023_admin_media_deletion_safety"
down_revision: str | None = "0022_inbound_calling_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CANONICAL_ID_DEFAULT = "gen_random_uuid()"
_COMPLETE_SCHEMA_ID_DEFAULT = "uuid_generate_v4()"

_EXPECTED_COLUMNS: dict[str, tuple[str, bool, frozenset[str | None]]] = {
    "id": (
        "uuid",
        True,
        frozenset({_CANONICAL_ID_DEFAULT, _COMPLETE_SCHEMA_ID_DEFAULT}),
    ),
    "actor_id": ("uuid", True, frozenset({None})),
    "tenant_id": ("uuid", True, frozenset({None})),
    "call_id": ("uuid", True, frozenset({None})),
    "resource_type": ("character varying(32)", True, frozenset({None})),
    "resource_id": ("uuid", True, frozenset({None})),
    "idempotency_key": ("character varying(255)", True, frozenset({None})),
    "reason": ("text", True, frozenset({None})),
    "resource_snapshot": ("jsonb", True, frozenset({None})),
    "status": (
        "character varying(24)",
        True,
        frozenset({"'intent_committed'::character varying"}),
    ),
    "attempt_count": ("integer", True, frozenset({"1"})),
    "last_error": ("text", False, frozenset({None})),
    "object_deleted_at": ("timestamp with time zone", False, frozenset({None})),
    "completed_at": ("timestamp with time zone", False, frozenset({None})),
    "response_body": ("jsonb", False, frozenset({None})),
    "created_at": ("timestamp with time zone", True, frozenset({"now()"})),
    "updated_at": ("timestamp with time zone", True, frozenset({"now()"})),
}

# complete_schema.sql is the supported bootstrap for a fresh installation and
# represents the current head, while this historical revision must still
# validate the 0023 shape it creates itself. Accept only this explicitly known
# forward addition; arbitrary extra columns remain a hard failure.
_KNOWN_FORWARD_COLUMNS: dict[str, tuple[str, bool, frozenset[str | None]]] = {
    "attempt_actor_ids": ("uuid[]", True, frozenset({None})),
}

_EXPECTED_CONSTRAINTS: dict[str, tuple[str, str]] = {
    "admin_media_deletion_intents_pkey": ("p", "PRIMARY KEY (id)"),
    "admin_media_deletion_intents_actor_id_fkey": (
        "f",
        "FOREIGN KEY (actor_id) REFERENCES user_profiles(id) ON DELETE RESTRICT",
    ),
    "admin_media_deletion_intents_tenant_id_fkey": (
        "f",
        "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT",
    ),
    "admin_media_deletion_intents_resource_type_check": (
        "c",
        "CHECK (resource_type::text = ANY (ARRAY['recording'::character varying, "
        "'call_feedback'::character varying]::text[]))",
    ),
    "admin_media_deletion_intents_reason_check": (
        "c",
        "CHECK (char_length(btrim(reason)) >= 8)",
    ),
    "admin_media_deletion_intents_status_check": (
        "c",
        "CHECK (status::text = ANY (ARRAY['intent_committed'::character varying, "
        "'object_deleted'::character varying, 'completed'::character varying, "
        "'failed'::character varying]::text[]))",
    ),
    "admin_media_deletion_intents_attempt_count_check": (
        "c",
        "CHECK (attempt_count > 0)",
    ),
    "admin_media_deletion_actor_idempotency_unique": (
        "u",
        "UNIQUE (actor_id, idempotency_key)",
    ),
    "admin_media_deletion_resource_unique": (
        "u",
        "UNIQUE (resource_type, resource_id)",
    ),
}

_KNOWN_FORWARD_CONSTRAINTS: dict[str, tuple[str, str]] = {
    "admin_media_deletion_attempt_actors_check": (
        "c",
        "CHECK (cardinality(attempt_actor_ids) = attempt_count AND "
        "cardinality(attempt_actor_ids) > 0 AND "
        "array_position(attempt_actor_ids, NULL::uuid) IS NULL)",
    ),
}

_EXPECTED_INDEXES = {
    "idx_admin_media_deletion_tenant_created": (
        "CREATE INDEX idx_admin_media_deletion_tenant_created ON "
        "public.admin_media_deletion_intents USING btree (tenant_id, created_at DESC)"
    ),
    "idx_admin_media_deletion_status_updated": (
        "CREATE INDEX idx_admin_media_deletion_status_updated ON "
        "public.admin_media_deletion_intents USING btree (status, updated_at) "
        "WHERE ((status)::text <> 'completed'::text)"
    ),
}

_POLICY_NAMES = {
    "admin_media_deletion_intents_select",
    "admin_media_deletion_intents_insert",
    "admin_media_deletion_intents_update",
    "admin_media_deletion_intents_delete",
}

_PROTECTION_TRIGGER_NAMES = {
    "trg_protect_admin_media_deletion_intent_update",
    "trg_protect_admin_media_deletion_intent_delete",
}

_FORWARD_COMPAT_TRIGGER = "trg_maintain_admin_media_deletion_attempt_actors"
_FORWARD_COMPAT_TRIGGER_DEFINITION = (
    "CREATE TRIGGER trg_maintain_admin_media_deletion_attempt_actors BEFORE INSERT OR "
    "UPDATE OF attempt_count, attempt_actor_ids ON admin_media_deletion_intents FOR "
    "EACH ROW EXECUTE FUNCTION maintain_admin_media_deletion_attempt_actors()"
)
_FORWARD_REQUEST_KEY_GUARD_TRIGGER = "trg_guard_admin_media_deletion_origin_key"
_FORWARD_REQUEST_KEY_GUARD_TRIGGER_DEFINITION = (
    "CREATE TRIGGER trg_guard_admin_media_deletion_origin_key BEFORE INSERT ON "
    "admin_media_deletion_intents FOR EACH ROW EXECUTE FUNCTION "
    "guard_admin_media_deletion_origin_key()"
)

# complete_schema.sql's generic updated_at trigger loop adds this after it
# creates the table. It is baseline-owned, safe, and deliberately preserved.
_BASELINE_UPDATED_AT_TRIGGER = "update_admin_media_deletion_intents_updated_at"
_BASELINE_UPDATED_AT_TRIGGER_DEFINITION = (
    "CREATE TRIGGER update_admin_media_deletion_intents_updated_at BEFORE UPDATE ON "
    "admin_media_deletion_intents FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()"
)


def _mapping_rows(connection: Any, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]


def _validate_existing_table(connection: Any, *, allow_complete_schema_default: bool) -> None:
    relation = _mapping_rows(
        connection,
        """
        SELECT c.relkind::text AS relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'admin_media_deletion_intents'
        """,
    )
    if len(relation) != 1 or relation[0]["relkind"] != "r":
        raise RuntimeError(
            "admin_media_deletion_intents must be one ordinary table in public; "
            "refusing to accept an incompatible pre-existing relation"
        )

    column_rows = _mapping_rows(
        connection,
        """
        SELECT
            a.attname AS name,
            format_type(a.atttypid, a.atttypmod) AS formatted_type,
            a.attnotnull AS not_null,
            pg_get_expr(d.adbin, d.adrelid) AS default_expr
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d
          ON d.adrelid = a.attrelid
         AND d.adnum = a.attnum
        WHERE a.attrelid = 'public.admin_media_deletion_intents'::regclass
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
    )
    actual_columns = {str(row["name"]): row for row in column_rows}
    problems: list[str] = []

    missing_columns = sorted(set(_EXPECTED_COLUMNS) - set(actual_columns))
    known_columns = set(_EXPECTED_COLUMNS) | set(_KNOWN_FORWARD_COLUMNS)
    unexpected_columns = sorted(set(actual_columns) - known_columns)
    if missing_columns:
        problems.append(f"missing columns: {', '.join(missing_columns)}")
    if unexpected_columns:
        problems.append(f"unexpected columns: {', '.join(unexpected_columns)}")

    for name in sorted(set(_EXPECTED_COLUMNS) & set(actual_columns)):
        expected_type, expected_not_null, expected_defaults = _EXPECTED_COLUMNS[name]
        row = actual_columns[name]
        if row["formatted_type"] != expected_type:
            problems.append(f"{name} type is {row['formatted_type']!r}, expected {expected_type!r}")
        if bool(row["not_null"]) != expected_not_null:
            problems.append(
                f"{name} nullability is {bool(row['not_null'])!r}, "
                f"expected not_null={expected_not_null!r}"
            )
        allowed_defaults = expected_defaults
        if name == "id" and not allow_complete_schema_default:
            allowed_defaults = frozenset({_CANONICAL_ID_DEFAULT})
        if row["default_expr"] not in allowed_defaults:
            allowed_display = ", ".join(repr(value) for value in allowed_defaults)
            problems.append(
                f"{name} default is {row['default_expr']!r}, expected one of "
                f"[{allowed_display}]"
            )

    for name in sorted(set(_KNOWN_FORWARD_COLUMNS) & set(actual_columns)):
        expected_type, expected_not_null, expected_defaults = _KNOWN_FORWARD_COLUMNS[name]
        row = actual_columns[name]
        if row["formatted_type"] != expected_type:
            problems.append(
                f"{name} type is {row['formatted_type']!r}, expected {expected_type!r}"
            )
        if bool(row["not_null"]) != expected_not_null:
            problems.append(
                f"{name} nullability is {bool(row['not_null'])!r}, "
                f"expected not_null={expected_not_null!r}"
            )
        if row["default_expr"] not in expected_defaults:
            problems.append(
                f"{name} default is {row['default_expr']!r}, expected no default"
            )

    constraint_rows = _mapping_rows(
        connection,
        """
        SELECT
            conname AS name,
            contype::text AS constraint_type,
            convalidated AS validated,
            pg_get_constraintdef(oid, TRUE) AS definition
        FROM pg_constraint
        WHERE conrelid = 'public.admin_media_deletion_intents'::regclass
        ORDER BY conname
        """,
    )
    actual_constraints = {str(row["name"]): row for row in constraint_rows}
    missing_constraints = sorted(set(_EXPECTED_CONSTRAINTS) - set(actual_constraints))
    known_constraints = set(_EXPECTED_CONSTRAINTS) | set(_KNOWN_FORWARD_CONSTRAINTS)
    unexpected_constraints = sorted(set(actual_constraints) - known_constraints)
    if missing_constraints:
        problems.append(f"missing constraints: {', '.join(missing_constraints)}")
    if unexpected_constraints:
        problems.append(f"unexpected constraints: {', '.join(unexpected_constraints)}")

    for name in sorted(set(_EXPECTED_CONSTRAINTS) & set(actual_constraints)):
        expected_type, expected_definition = _EXPECTED_CONSTRAINTS[name]
        row = actual_constraints[name]
        if row["constraint_type"] != expected_type:
            problems.append(
                f"{name} type is {row['constraint_type']!r}, expected {expected_type!r}"
            )
        if not bool(row["validated"]):
            problems.append(f"{name} is not validated")
        if row["definition"] != expected_definition:
            problems.append(
                f"{name} definition is {row['definition']!r}, " f"expected {expected_definition!r}"
            )

    has_forward_column = "attempt_actor_ids" in actual_columns
    has_forward_constraint = (
        "admin_media_deletion_attempt_actors_check" in actual_constraints
    )
    if has_forward_column != has_forward_constraint:
        problems.append(
            "attempt_actor_ids and admin_media_deletion_attempt_actors_check "
            "must either both exist or both be absent"
        )
    for name in sorted(set(_KNOWN_FORWARD_CONSTRAINTS) & set(actual_constraints)):
        expected_type, expected_definition = _KNOWN_FORWARD_CONSTRAINTS[name]
        row = actual_constraints[name]
        if row["constraint_type"] != expected_type:
            problems.append(
                f"{name} type is {row['constraint_type']!r}, expected {expected_type!r}"
            )
        if not bool(row["validated"]):
            problems.append(f"{name} is not validated")
        if row["definition"] != expected_definition:
            problems.append(
                f"{name} definition is {row['definition']!r}, "
                f"expected {expected_definition!r}"
            )

    if problems:
        raise RuntimeError(
            "Existing admin_media_deletion_intents schema is incompatible; "
            "refusing a destructive or lossy automatic repair: " + "; ".join(problems)
        )


def _validate_or_create_index(connection: Any, name: str, create_sql: str) -> None:
    qualified_name = f"public.{name}"
    relation_exists = bool(
        connection.execute(
            text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
            {"qualified_name": qualified_name},
        ).scalar()
    )
    rows = _mapping_rows(
        connection,
        """
        SELECT
            ix.indisunique AS is_unique,
            ix.indisvalid AS is_valid,
            ix.indisready AS is_ready,
            pg_get_indexdef(ix.indexrelid) AS definition
        FROM pg_index ix
        WHERE ix.indexrelid = to_regclass(:qualified_name)
        """,
        qualified_name=qualified_name,
    )
    if relation_exists and not rows:
        raise RuntimeError(f"{qualified_name} exists but is not an index")
    if rows:
        row = rows[0]
        expected_definition = _EXPECTED_INDEXES[name]
        if (
            bool(row["is_unique"])
            or not bool(row["is_valid"])
            or not bool(row["is_ready"])
            or row["definition"] != expected_definition
        ):
            raise RuntimeError(
                f"Existing index {qualified_name} is incompatible; expected "
                f"{expected_definition!r}, found {row['definition']!r}"
            )
        return

    op.execute(text(create_sql))
    _validate_or_create_index(connection, name, create_sql)


def _validate_known_security_objects(connection: Any) -> None:
    policies = {
        str(row["policyname"])
        for row in _mapping_rows(
            connection,
            """
            SELECT policyname
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = 'admin_media_deletion_intents'
            """,
        )
    }
    unknown_policies = sorted(policies - _POLICY_NAMES)
    if unknown_policies:
        raise RuntimeError(
            "Existing admin_media_deletion_intents has unexpected RLS policies: "
            + ", ".join(unknown_policies)
        )

    trigger_rows = _mapping_rows(
        connection,
        """
        SELECT
            t.tgname AS name,
            t.tgenabled::text AS enabled,
            pg_get_triggerdef(t.oid, TRUE) AS definition
        FROM pg_trigger t
        WHERE t.tgrelid = 'public.admin_media_deletion_intents'::regclass
          AND NOT t.tgisinternal
        ORDER BY t.tgname
        """,
    )
    allowed_trigger_names = _PROTECTION_TRIGGER_NAMES | {
        _BASELINE_UPDATED_AT_TRIGGER,
        _FORWARD_COMPAT_TRIGGER,
        _FORWARD_REQUEST_KEY_GUARD_TRIGGER,
    }
    unknown_triggers = sorted(
        str(row["name"]) for row in trigger_rows if str(row["name"]) not in allowed_trigger_names
    )
    if unknown_triggers:
        raise RuntimeError(
            "Existing admin_media_deletion_intents has unexpected triggers: "
            + ", ".join(unknown_triggers)
        )
    for row in trigger_rows:
        trigger_name = str(row["name"])
        if trigger_name == _FORWARD_REQUEST_KEY_GUARD_TRIGGER:
            if (
                row["enabled"] != "O"
                or row["definition"]
                != _FORWARD_REQUEST_KEY_GUARD_TRIGGER_DEFINITION
            ):
                raise RuntimeError(
                    "The forward-compatible request-key guard trigger is incompatible"
                )
            continue
        if trigger_name == _FORWARD_COMPAT_TRIGGER:
            if (
                row["enabled"] != "O"
                or row["definition"] != _FORWARD_COMPAT_TRIGGER_DEFINITION
            ):
                raise RuntimeError(
                    "The forward-compatible deletion-attempt trigger is incompatible"
                )
            continue
        if trigger_name != _BASELINE_UPDATED_AT_TRIGGER:
            continue
        if row["enabled"] != "O" or row["definition"] != _BASELINE_UPDATED_AT_TRIGGER_DEFINITION:
            raise RuntimeError(
                "The baseline-owned admin_media_deletion_intents updated_at trigger "
                "is incompatible; refusing to replace an object owned outside 0023"
            )


def upgrade() -> None:
    # Make catalog rendering deterministic for strict compatibility checks and
    # keep every object in the schema used by the application.
    op.execute(text("SET LOCAL search_path TO public"))
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.admin_media_deletion_intents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                actor_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE RESTRICT,
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                call_id UUID NOT NULL,
                resource_type VARCHAR(32) NOT NULL
                    CHECK (resource_type IN ('recording', 'call_feedback')),
                resource_id UUID NOT NULL,
                idempotency_key VARCHAR(255) NOT NULL,
                reason TEXT NOT NULL CHECK (CHAR_LENGTH(BTRIM(reason)) >= 8),
                resource_snapshot JSONB NOT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'intent_committed'
                    CHECK (status IN (
                        'intent_committed', 'object_deleted', 'completed', 'failed'
                    )),
                attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count > 0),
                last_error TEXT,
                object_deleted_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                response_body JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT admin_media_deletion_actor_idempotency_unique
                    UNIQUE (actor_id, idempotency_key),
                CONSTRAINT admin_media_deletion_resource_unique
                    UNIQUE (resource_type, resource_id)
            )
            """
        )
    )
    # CREATE TABLE IF NOT EXISTS alone is unsafe: PostgreSQL accepts any
    # relation with that name. Lock and validate the exact audit contract
    # before changing even the known complete-schema UUID default.
    op.execute(text("LOCK TABLE public.admin_media_deletion_intents " "IN ACCESS EXCLUSIVE MODE"))
    connection = op.get_bind()
    _validate_existing_table(connection, allow_complete_schema_default=True)
    _validate_known_security_objects(connection)

    # complete_schema.sql historically used uuid_generate_v4(). Both defaults
    # are safe UUID generators; normalize that one known drift to the Alembic
    # canonical definition. Every other structural difference fails above.
    op.execute(
        text(
            "ALTER TABLE public.admin_media_deletion_intents "
            "ALTER COLUMN id SET DEFAULT gen_random_uuid()"
        )
    )
    _validate_existing_table(connection, allow_complete_schema_default=False)

    _validate_or_create_index(
        connection,
        "idx_admin_media_deletion_tenant_created",
        "CREATE INDEX IF NOT EXISTS idx_admin_media_deletion_tenant_created "
        "ON public.admin_media_deletion_intents (tenant_id, created_at DESC)",
    )
    _validate_or_create_index(
        connection,
        "idx_admin_media_deletion_status_updated",
        "CREATE INDEX IF NOT EXISTS idx_admin_media_deletion_status_updated "
        "ON public.admin_media_deletion_intents (status, updated_at) "
        "WHERE status <> 'completed'",
    )

    op.execute(text("ALTER TABLE public.admin_media_deletion_intents ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE public.admin_media_deletion_intents FORCE ROW LEVEL SECURITY"))
    for policy in sorted(_POLICY_NAMES):
        op.execute(
            text(f"DROP POLICY IF EXISTS {policy} " "ON public.admin_media_deletion_intents")
        )
    op.execute(
        text(
            """
            CREATE POLICY admin_media_deletion_intents_select
            ON public.admin_media_deletion_intents FOR SELECT
            USING (
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
    op.execute(
        text(
            """
            CREATE POLICY admin_media_deletion_intents_insert
            ON public.admin_media_deletion_intents FOR INSERT
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
            CREATE POLICY admin_media_deletion_intents_update
            ON public.admin_media_deletion_intents FOR UPDATE
            USING (
                COALESCE(
                    NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                    FALSE
                )
            )
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
            CREATE POLICY admin_media_deletion_intents_delete
            ON public.admin_media_deletion_intents FOR DELETE USING (FALSE)
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
    for trigger in sorted(_PROTECTION_TRIGGER_NAMES):
        op.execute(
            text(f"DROP TRIGGER IF EXISTS {trigger} " "ON public.admin_media_deletion_intents")
        )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_protect_admin_media_deletion_intent_update
            BEFORE UPDATE ON public.admin_media_deletion_intents
            FOR EACH ROW EXECUTE FUNCTION public.protect_admin_media_deletion_intent()
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_protect_admin_media_deletion_intent_delete
            BEFORE DELETE ON public.admin_media_deletion_intents
            FOR EACH ROW EXECUTE FUNCTION public.protect_admin_media_deletion_intent()
            """
        )
    )


def downgrade() -> None:
    """Retain the durable audit boundary and all of its protections.

    complete_schema.sql may have created these objects before Alembic was
    stamped at 0022, so 0023 cannot prove ownership. More importantly, rows
    record irreversible media deletion and must survive application rollback.
    A downgraded application does not use this table; a later upgrade validates
    and reuses it safely.
    """
