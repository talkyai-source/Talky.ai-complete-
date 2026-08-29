"""Drift guards for durable media-deletion request-key bindings."""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest


_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION = _BACKEND / "Alembic" / "versions" / "0027_media_delete_request_keys.py"
_COMPLETE_SCHEMA = _BACKEND / "database" / "complete_schema.sql"
_TABLE = "admin_media_deletion_request_keys"
_ORIGIN_GUARD = "guard_admin_media_deletion_origin_key"
_REQUEST_GUARD = "guard_admin_media_deletion_request_key"
_PROTECT_FUNCTION = "protect_admin_media_deletion_request_key"


def _migration_module():
    return importlib.import_module("Alembic.versions.0027_media_delete_request_keys")


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().rstrip(";").replace("public.", "")


def _canonical_sql(value: str) -> str:
    return re.sub(r"\s*([(),;])\s*", r"\1", _normalized(value))


def _compact_sql(value: str) -> str:
    return re.sub(r"\s+", "", _normalized(value))


def _function_sql(source: str, function: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION\s+(?:public\.)?{function}\(\)",
        source,
    )
    assert match is not None
    end_marker = "$$ LANGUAGE plpgsql"
    end = source.index(end_marker, match.start()) + len(end_marker)
    return source[match.start() : end]


def _trigger_sql(source: str, trigger: str, function: str) -> str:
    marker = f"CREATE TRIGGER {trigger}"
    if "from alembic import op" in source:
        return _migration_block(source, marker)
    start = source.index(marker)
    function_call = re.search(
        rf"(?:public\.)?{function}\(\)",
        source[start:],
    )
    assert function_call is not None
    return source[start : start + function_call.end()]


def _migration_block(source: str, marker: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "text":
            continue
        try:
            sql = ast.literal_eval(node.args[0])
        except (TypeError, ValueError):
            continue
        if isinstance(sql, str) and marker in sql:
            return sql
    raise AssertionError(f"SQL block containing {marker!r} not found")


def _schema_statement(source: str, marker: str) -> str:
    start = source.index(marker)
    return source[start : source.index(";", start) + 1]


def _structure_rows(migration):
    columns = [
        {
            "name": name,
            "formatted_type": formatted_type,
            "not_null": not_null,
            "default_expr": default,
        }
        for name, (formatted_type, not_null, default) in migration._EXPECTED_COLUMNS.items()
    ]
    constraints = [
        {
            "name": name,
            "constraint_type": constraint_type,
            "validated": True,
            "definition": definition,
        }
        for name, (constraint_type, definition) in migration._EXPECTED_CONSTRAINTS.items()
    ]
    return {
        "relation": [{"relkind": "r"}],
        "columns": columns,
        "constraints": constraints,
    }


def _structure_router(payload):
    def rows(_connection, sql):
        if "FROM pg_class c" in sql:
            return payload["relation"]
        if "FROM pg_attribute" in sql:
            return payload["columns"]
        if "FROM pg_constraint" in sql:
            return payload["constraints"]
        raise AssertionError(sql)

    return rows


def _security_rows(migration):
    return {
        "relation": [{"rls_enabled": True, "rls_forced": True}],
        "policies": [
            {
                "policyname": name,
                "cmd": command,
                "permissive": permissive,
                "roles": roles,
                "qual": using_expression,
                "with_check": check_expression,
            }
            for name, (
                command,
                permissive,
                roles,
                using_expression,
                check_expression,
            ) in sorted(migration._EXPECTED_POLICIES.items())
        ],
        "triggers": [
            {"name": name, "enabled": "O", "definition": definition}
            for name, definition in sorted(migration._EXPECTED_TRIGGERS.items())
        ],
        "index": [
            {
                "is_unique": False,
                "is_valid": True,
                "is_ready": True,
                "definition": (
                    "CREATE INDEX idx_admin_media_deletion_request_keys_intent ON "
                    "public.admin_media_deletion_request_keys USING btree "
                    "(intent_id, created_at)"
                ),
            }
        ],
    }


def _security_router(payload):
    def rows(_connection, sql):
        if "relrowsecurity" in sql:
            return payload["relation"]
        if "FROM pg_policies" in sql:
            return payload["policies"]
        if "FROM pg_trigger" in sql:
            return payload["triggers"]
        if "FROM pg_index" in sql:
            return payload["index"]
        raise AssertionError(sql)

    return rows


def test_0027_chains_directly_from_media_delete_recovery() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0027_media_delete_request_keys"' in source
    assert 'down_revision: str | None = "0026_media_delete_recovery"' in source
    assert len("0027_media_delete_request_keys") <= 32


def test_request_key_ledger_contract_is_exact_and_has_no_stored_tenant() -> None:
    migration = _migration_module()

    assert migration._EXPECTED_COLUMNS == {
        "id": ("uuid", True, "gen_random_uuid()"),
        "intent_id": ("uuid", True, None),
        "actor_id": ("uuid", True, None),
        "idempotency_key": ("character varying(255)", True, None),
        "request_reason": ("text", True, None),
        "created_at": ("timestamp with time zone", True, "now()"),
    }
    assert "tenant_id" not in migration._EXPECTED_COLUMNS
    assert migration._EXPECTED_CONSTRAINTS == {
        "admin_media_deletion_request_keys_pkey": ("p", "PRIMARY KEY (id)"),
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

    table_sql = _migration_block(
        _MIGRATION.read_text(encoding="utf-8"),
        "CREATE TABLE IF NOT EXISTS public.admin_media_deletion_request_keys",
    )
    assert "tenant_id" not in table_sql
    assert "UNIQUE (actor_id, idempotency_key)" in _normalized(table_sql)
    assert table_sql.count("ON DELETE RESTRICT") == 2


def test_preexisting_structure_accepts_only_the_exact_ledger(monkeypatch) -> None:
    migration = _migration_module()
    payload = _structure_rows(migration)
    monkeypatch.setattr(migration, "_rows", _structure_router(payload))

    migration._validate_request_key_structure(object())


@pytest.mark.parametrize("drift", ["relation", "column", "constraint"])
def test_preexisting_structure_rejects_arbitrary_drift(monkeypatch, drift: str) -> None:
    migration = _migration_module()
    payload = _structure_rows(migration)
    if drift == "relation":
        payload["relation"] = [{"relkind": "v"}]
    elif drift == "column":
        payload["columns"].append(
            {
                "name": "unreviewed_tenant_id",
                "formatted_type": "uuid",
                "not_null": False,
                "default_expr": None,
            }
        )
    else:
        payload["constraints"].append(
            {
                "name": "unreviewed_constraint",
                "constraint_type": "c",
                "validated": True,
                "definition": "CHECK (true)",
            }
        )

    monkeypatch.setattr(migration, "_rows", _structure_router(payload))
    with pytest.raises(RuntimeError, match="ordinary table|incompatible"):
        migration._validate_request_key_structure(object())


@pytest.mark.parametrize(
    "drift",
    [
        "rls",
        "policy",
        "policy_definition",
        "trigger",
        "trigger_definition",
        "disabled_trigger",
        "index",
    ],
)
def test_preexisting_security_rejects_rls_policy_trigger_and_index_drift(
    monkeypatch,
    drift: str,
) -> None:
    migration = _migration_module()
    payload = _security_rows(migration)
    if drift == "rls":
        payload["relation"][0]["rls_forced"] = False
    elif drift == "policy":
        extra_policy = dict(payload["policies"][0])
        extra_policy["policyname"] = "unreviewed_policy"
        payload["policies"].append(extra_policy)
    elif drift == "policy_definition":
        payload["policies"][0]["qual"] = "true"
    elif drift == "trigger":
        extra_trigger = dict(payload["triggers"][0])
        extra_trigger["name"] = "trg_unreviewed_request_key"
        payload["triggers"].append(extra_trigger)
    elif drift == "trigger_definition":
        payload["triggers"][0]["definition"] = "CREATE TRIGGER wrong_guard"
    elif drift == "disabled_trigger":
        payload["triggers"][0]["enabled"] = "D"
    else:
        payload["index"][0]["definition"] += " DESC"

    monkeypatch.setattr(migration, "_rows", _security_router(payload))
    with pytest.raises(RuntimeError, match="forced RLS|policies|triggers|index"):
        migration._validate_preexisting_security(object())


def test_preexisting_security_compares_policy_and_trigger_definitions() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    validation = source[
        source.index("def _validate_preexisting_security") : source.index("def upgrade() -> None:")
    ]

    # Names alone are not a security contract: a permissive SELECT policy or a
    # trigger wired to a different function must fail bootstrap validation.
    assert "qual, with_check" in validation
    assert 'str(row["cmd"])' in validation
    assert "policies != _EXPECTED_POLICIES" in validation
    assert "pg_get_triggerdef" in validation
    assert "definition != _EXPECTED_TRIGGERS[name]" in validation


def test_tenant_visibility_derives_through_the_intent_select_policy() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    select_policy = _normalized(
        _migration_block(source, "CREATE POLICY admin_media_deletion_request_keys_select")
    )
    insert_policy = _normalized(
        _migration_block(source, "CREATE POLICY admin_media_deletion_request_keys_insert")
    )
    update_policy = _normalized(
        _migration_block(source, "CREATE POLICY admin_media_deletion_request_keys_update")
    )
    delete_policy = _normalized(
        _migration_block(source, "CREATE POLICY admin_media_deletion_request_keys_delete")
    )

    assert "FROM admin_media_deletion_intents i" in select_policy
    assert "i.id = intent_id" in select_policy
    assert "i.tenant_id = NULLIF(" in select_policy
    assert "current_setting('app.current_tenant_id', TRUE)" in select_policy
    assert "current_setting('app.bypass_rls', TRUE)" in select_policy
    assert "current_tenant_id" not in insert_policy
    assert "current_setting('app.bypass_rls', TRUE)" in insert_policy
    assert update_policy.endswith("FOR UPDATE USING (FALSE)")
    assert delete_policy.endswith("FOR DELETE USING (FALSE)")


def test_origin_and_request_guards_share_lock_key_and_cross_check_each_other() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    origin = _compact_sql(_function_sql(source, _ORIGIN_GUARD))
    request = _compact_sql(_function_sql(source, _REQUEST_GUARD))
    lock_key = (
        "hashtextextended('talky:media-delete-key:'||NEW.actor_id::text||':'||"
        "NEW.idempotency_key,0)"
    )

    assert origin.count(lock_key) == 1
    assert request.count(lock_key) == 1
    assert "FROMadmin_media_deletion_request_keysk" in origin
    assert "k.actor_id=NEW.actor_id" in origin
    assert "k.idempotency_key=NEW.idempotency_key" in origin
    assert "k.intent_id<>NEW.id" in origin
    assert "FROMadmin_media_deletion_intentsi" in request
    assert "i.actor_id=NEW.actor_id" in request
    assert "i.idempotency_key=NEW.idempotency_key" in request
    assert "i.id<>NEW.intent_id" in request
    assert origin.count("ERRCODE='unique_violation'") == 1
    assert request.count("ERRCODE='unique_violation'") == 1

    assert _canonical_sql(
        _trigger_sql(
            source,
            "trg_guard_admin_media_deletion_origin_key",
            _ORIGIN_GUARD,
        )
    ) == _canonical_sql(
        """
        CREATE TRIGGER trg_guard_admin_media_deletion_origin_key
        BEFORE INSERT ON admin_media_deletion_intents
        FOR EACH ROW EXECUTE FUNCTION guard_admin_media_deletion_origin_key()
        """
    )
    assert _canonical_sql(
        _trigger_sql(
            source,
            "trg_guard_admin_media_deletion_request_key",
            _REQUEST_GUARD,
        )
    ) == _canonical_sql(
        """
        CREATE TRIGGER trg_guard_admin_media_deletion_request_key
        BEFORE INSERT ON admin_media_deletion_request_keys
        FOR EACH ROW EXECUTE FUNCTION guard_admin_media_deletion_request_key()
        """
    )


def test_backfill_is_idempotent_but_rejects_actor_key_binding_conflicts() -> None:
    migration = _normalized(_MIGRATION.read_text(encoding="utf-8"))
    complete_schema = _normalized(_COMPLETE_SCHEMA.read_text(encoding="utf-8"))
    invariants = (
        "INSERT INTO admin_media_deletion_request_keys ( intent_id, actor_id, "
        "idempotency_key, request_reason ) SELECT id, actor_id, idempotency_key, reason "
        "FROM admin_media_deletion_intents ON CONFLICT (actor_id, idempotency_key) "
        "DO NOTHING",
        "JOIN admin_media_deletion_request_keys k ON k.actor_id = i.actor_id AND "
        "k.idempotency_key = i.idempotency_key",
        "k.intent_id <> i.id OR k.request_reason <> i.reason",
        "RAISE EXCEPTION 'media deletion request-key backfill conflict'",
    )
    for invariant in invariants:
        assert invariant in migration
        assert invariant in complete_schema


def test_request_key_ledger_is_immutable_for_updates_and_deletes() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    function = _normalized(_function_sql(source, _PROTECT_FUNCTION))

    assert "RAISE EXCEPTION" in function
    assert "immutable audit record" in function
    for operation in ("UPDATE", "DELETE"):
        trigger = f"trg_protect_admin_media_deletion_request_key_{operation.lower()}"
        assert f"BEFORE {operation} ON {_TABLE}" in _normalized(
            _trigger_sql(source, trigger, _PROTECT_FUNCTION)
        )


def test_complete_schema_matches_0027_table_security_guards_and_index() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    complete_schema = _COMPLETE_SCHEMA.read_text(encoding="utf-8")

    assert _canonical_sql(
        _migration_block(
            migration,
            "CREATE TABLE IF NOT EXISTS public.admin_media_deletion_request_keys",
        )
    ) == _canonical_sql(
        _schema_statement(
            complete_schema,
            "CREATE TABLE IF NOT EXISTS admin_media_deletion_request_keys",
        )
    )

    for function in (_ORIGIN_GUARD, _REQUEST_GUARD, _PROTECT_FUNCTION):
        assert _canonical_sql(_function_sql(migration, function)) == _canonical_sql(
            _function_sql(complete_schema, function)
        )

    triggers = (
        ("trg_guard_admin_media_deletion_origin_key", _ORIGIN_GUARD),
        ("trg_guard_admin_media_deletion_request_key", _REQUEST_GUARD),
        ("trg_protect_admin_media_deletion_request_key_update", _PROTECT_FUNCTION),
        ("trg_protect_admin_media_deletion_request_key_delete", _PROTECT_FUNCTION),
    )
    for trigger, function in triggers:
        assert _canonical_sql(_trigger_sql(migration, trigger, function)) == _canonical_sql(
            _trigger_sql(complete_schema, trigger, function)
        )

    for policy in sorted(_migration_module()._EXPECTED_POLICIES):
        marker = f"CREATE POLICY {policy}"
        assert _canonical_sql(_migration_block(migration, marker)) == _canonical_sql(
            _schema_statement(complete_schema, marker)
        )

    assert _canonical_sql(
        _migration_block(
            migration,
            "idx_admin_media_deletion_request_keys_intent",
        )
    ) == _canonical_sql(
        _schema_statement(
            complete_schema,
            "CREATE INDEX IF NOT EXISTS idx_admin_media_deletion_request_keys_intent",
        )
    )

    for rls_mode in ("ENABLE", "FORCE"):
        statement = f"ALTER TABLE admin_media_deletion_request_keys {rls_mode} ROW LEVEL SECURITY"
        assert _canonical_sql(
            _migration_block(migration, f"{rls_mode} ROW LEVEL SECURITY")
        ) == _canonical_sql(statement)
        assert statement in _normalized(complete_schema)


def test_0027_downgrade_retains_irreversible_request_key_evidence() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    downgrade = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )

    assert len(downgrade.body) == 1
    assert isinstance(downgrade.body[0], ast.Expr)
    assert isinstance(downgrade.body[0].value, ast.Constant)
    assert "Retain request-key bindings" in (ast.get_docstring(downgrade) or "")
    assert "irreversible work" in (ast.get_docstring(downgrade) or "")
    assert not any(isinstance(node, ast.Call) for node in ast.walk(downgrade))
