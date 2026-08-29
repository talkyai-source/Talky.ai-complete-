"""Drift guards for audited recovery of incomplete media deletions."""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest


_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION = _BACKEND / "Alembic" / "versions" / "0026_media_delete_recovery.py"
_COMPLETE_SCHEMA = _BACKEND / "database" / "complete_schema.sql"
_TABLE = "admin_media_deletion_intents"
_PROTECT_FUNCTION = "protect_admin_media_deletion_intent"
_MAINTAIN_FUNCTION = "maintain_admin_media_deletion_attempt_actors"
_MAINTAIN_TRIGGER = "trg_maintain_admin_media_deletion_attempt_actors"
_ORIGIN_KEY_GUARD_FUNCTION = "guard_admin_media_deletion_origin_key"
_ORIGIN_KEY_GUARD_TRIGGER = "trg_guard_admin_media_deletion_origin_key"
_ATTEMPT_CONSTRAINT = "admin_media_deletion_attempt_actors_check"


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().rstrip(";").replace("public.", "")


def _canonical_sql(value: str) -> str:
    return re.sub(r"\s*([(),;])\s*", r"\1", _normalized(value))


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
    start = source.index(f"CREATE TRIGGER {trigger}")
    function_call = re.search(
        rf"(?:public\.)?{function}\(\)",
        source[start:],
    )
    assert function_call is not None
    end = start + function_call.end()
    return source[start:end]


def _migration_0023():
    return importlib.import_module("Alembic.versions.0023_admin_media_deletion_safety")


def _0023_column_rows(migration) -> list[dict[str, object]]:
    specs = {**migration._EXPECTED_COLUMNS, **migration._KNOWN_FORWARD_COLUMNS}
    rows = []
    for name, (formatted_type, not_null, defaults) in specs.items():
        default = migration._CANONICAL_ID_DEFAULT if name == "id" else next(iter(defaults))
        rows.append(
            {
                "name": name,
                "formatted_type": formatted_type,
                "not_null": not_null,
                "default_expr": default,
            }
        )
    return rows


def _0023_constraint_rows(migration) -> list[dict[str, object]]:
    specs = {
        **migration._EXPECTED_CONSTRAINTS,
        **migration._KNOWN_FORWARD_CONSTRAINTS,
    }
    return [
        {
            "name": name,
            "constraint_type": constraint_type,
            "validated": True,
            "definition": definition,
        }
        for name, (constraint_type, definition) in specs.items()
    ]


def test_0026_chains_directly_from_hold_serialization() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0026_media_delete_recovery"' in source
    assert 'down_revision: str | None = "0025_media_hold_serialization"' in source
    assert len("0026_media_delete_recovery") <= 32


def test_attempt_actor_history_is_added_and_backfilled_to_attempt_count() -> None:
    migration = _normalized(_MIGRATION.read_text(encoding="utf-8"))
    complete_schema = _normalized(_COMPLETE_SCHEMA.read_text(encoding="utf-8"))

    required = (
        f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS attempt_actor_ids UUID[]",
        f"UPDATE {_TABLE} SET attempt_actor_ids = " "array_fill(actor_id, ARRAY[attempt_count])",
        "WHERE attempt_actor_ids IS NULL OR cardinality(attempt_actor_ids) = 0",
        f"ALTER TABLE {_TABLE} ALTER COLUMN attempt_actor_ids SET NOT NULL",
    )
    for statement in required:
        assert statement in migration
        assert statement in complete_schema


def test_attempt_actor_constraint_ties_non_null_history_exactly_to_count() -> None:
    migration = _normalized(_MIGRATION.read_text(encoding="utf-8"))
    complete_schema = _normalized(_COMPLETE_SCHEMA.read_text(encoding="utf-8"))

    required = (
        f"conname = '{_ATTEMPT_CONSTRAINT}'",
        f"ADD CONSTRAINT {_ATTEMPT_CONSTRAINT}",
        "cardinality(attempt_actor_ids) = attempt_count",
        "cardinality(attempt_actor_ids) > 0",
        "array_position(attempt_actor_ids, NULL) IS NULL",
    )
    for invariant in required:
        assert invariant in migration
        assert invariant in complete_schema


def test_origin_stays_immutable_and_attempt_history_is_append_only() -> None:
    function = _normalized(
        _function_sql(
            _MIGRATION.read_text(encoding="utf-8"),
            _PROTECT_FUNCTION,
        )
    )

    immutable_guard = function[
        function.index("IF NEW.id IS DISTINCT FROM OLD.id") : function.index(
            "RAISE EXCEPTION 'admin media deletion audit fields are immutable'"
        )
    ]
    assert "NEW.actor_id IS DISTINCT FROM OLD.actor_id" in immutable_guard
    assert "NEW.reason IS DISTINCT FROM OLD.reason" in immutable_guard

    append_only = (
        "NEW.attempt_count < OLD.attempt_count",
        "NEW.attempt_count > OLD.attempt_count + 1",
        "cardinality(NEW.attempt_actor_ids) <> NEW.attempt_count",
        "NEW.attempt_actor_ids[1:OLD.attempt_count] IS DISTINCT FROM " "OLD.attempt_actor_ids",
    )
    for invariant in append_only:
        assert invariant in function


def test_complete_schema_matches_0026_protection_function() -> None:
    migration_function = _canonical_sql(
        _function_sql(
            _MIGRATION.read_text(encoding="utf-8"),
            _PROTECT_FUNCTION,
        )
    )
    schema_function = _canonical_sql(
        _function_sql(
            _COMPLETE_SCHEMA.read_text(encoding="utf-8"),
            _PROTECT_FUNCTION,
        )
    )

    assert schema_function == migration_function


def test_expand_contract_trigger_fills_only_legacy_attempt_history_writes() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    function = _normalized(_function_sql(source, _MAINTAIN_FUNCTION))
    trigger = _normalized(_trigger_sql(source, _MAINTAIN_TRIGGER, _MAINTAIN_FUNCTION))

    assert "IF TG_OP = 'INSERT' THEN" in function
    assert (
        "IF NEW.attempt_actor_ids IS NULL OR " "cardinality(NEW.attempt_actor_ids) = 0 THEN"
    ) in function
    assert (
        "NEW.attempt_actor_ids := array_fill( NEW.actor_id, " "ARRAY[NEW.attempt_count] )"
    ) in function

    assert (
        "ELSIF NEW.attempt_count = OLD.attempt_count + 1 AND "
        "NEW.attempt_actor_ids IS NOT DISTINCT FROM OLD.attempt_actor_ids THEN"
    ) in function
    assert (
        "NEW.attempt_actor_ids := array_append( OLD.attempt_actor_ids, " "NEW.actor_id )"
    ) in function

    # Current writers provide attempt_actor_ids themselves. There is no ELSE
    # rewrite: they reach RETURN NEW unchanged.
    assert function.count("NEW.attempt_actor_ids :=") == 2
    assert "ELSE NEW.attempt_actor_ids" not in function
    assert function.endswith("RETURN NEW; END; $$ LANGUAGE plpgsql")
    assert trigger == _normalized(
        f"""
        CREATE TRIGGER {_MAINTAIN_TRIGGER}
        BEFORE INSERT OR UPDATE OF attempt_count, attempt_actor_ids
        ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_MAINTAIN_FUNCTION}()
        """
    )
    assert _MAINTAIN_TRIGGER < "trg_protect_admin_media_deletion_intent_update"


def test_complete_schema_matches_0026_compatibility_function_and_trigger() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    complete_schema = _COMPLETE_SCHEMA.read_text(encoding="utf-8")

    assert _canonical_sql(_function_sql(complete_schema, _MAINTAIN_FUNCTION)) == _canonical_sql(
        _function_sql(migration, _MAINTAIN_FUNCTION)
    )
    assert _canonical_sql(
        _trigger_sql(complete_schema, _MAINTAIN_TRIGGER, _MAINTAIN_FUNCTION)
    ) == _canonical_sql(_trigger_sql(migration, _MAINTAIN_TRIGGER, _MAINTAIN_FUNCTION))


def test_0023_forward_allowlists_are_exactly_the_known_0026_and_0027_objects() -> None:
    migration = _migration_0023()

    assert migration._KNOWN_FORWARD_COLUMNS == {
        "attempt_actor_ids": ("uuid[]", True, frozenset({None}))
    }
    assert migration._KNOWN_FORWARD_CONSTRAINTS == {
        _ATTEMPT_CONSTRAINT: (
            "c",
            "CHECK (cardinality(attempt_actor_ids) = attempt_count AND "
            "cardinality(attempt_actor_ids) > 0 AND "
            "array_position(attempt_actor_ids, NULL::uuid) IS NULL)",
        )
    }
    assert migration._FORWARD_COMPAT_TRIGGER == _MAINTAIN_TRIGGER
    assert migration._FORWARD_COMPAT_TRIGGER_DEFINITION == (
        f"CREATE TRIGGER {_MAINTAIN_TRIGGER} BEFORE INSERT OR UPDATE OF "
        f"attempt_count, attempt_actor_ids ON {_TABLE} FOR EACH ROW EXECUTE "
        f"FUNCTION {_MAINTAIN_FUNCTION}()"
    )
    assert migration._FORWARD_REQUEST_KEY_GUARD_TRIGGER == _ORIGIN_KEY_GUARD_TRIGGER
    assert migration._FORWARD_REQUEST_KEY_GUARD_TRIGGER_DEFINITION == (
        f"CREATE TRIGGER {_ORIGIN_KEY_GUARD_TRIGGER} BEFORE INSERT ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_ORIGIN_KEY_GUARD_FUNCTION}()"
    )

    assert len(migration._EXPECTED_COLUMNS) == 17
    assert len(migration._KNOWN_FORWARD_COLUMNS) == 1
    assert len(migration._EXPECTED_COLUMNS | migration._KNOWN_FORWARD_COLUMNS) == 18
    assert len(migration._EXPECTED_CONSTRAINTS) == 9
    assert len(migration._KNOWN_FORWARD_CONSTRAINTS) == 1
    assert len(migration._EXPECTED_CONSTRAINTS | migration._KNOWN_FORWARD_CONSTRAINTS) == 10
    assert len(migration._PROTECTION_TRIGGER_NAMES) == 2
    assert (
        len(
            migration._PROTECTION_TRIGGER_NAMES
            | {
                migration._BASELINE_UPDATED_AT_TRIGGER,
                migration._FORWARD_COMPAT_TRIGGER,
                migration._FORWARD_REQUEST_KEY_GUARD_TRIGGER,
            }
        )
        == 5
    )


def test_0023_accepts_the_complete_known_0026_column_and_constraint(monkeypatch) -> None:
    migration = _migration_0023()
    columns = _0023_column_rows(migration)
    constraints = _0023_constraint_rows(migration)

    def mapping_rows(_connection, sql, **_params):
        if "FROM pg_class" in sql:
            return [{"relkind": "r"}]
        if "FROM pg_attribute" in sql:
            return columns
        if "FROM pg_constraint" in sql:
            return constraints
        raise AssertionError(sql)

    monkeypatch.setattr(migration, "_mapping_rows", mapping_rows)
    migration._validate_existing_table(object(), allow_complete_schema_default=True)


@pytest.mark.parametrize("extra_kind", ["column", "constraint"])
def test_0023_still_rejects_arbitrary_forward_schema_extras(
    monkeypatch,
    extra_kind: str,
) -> None:
    migration = _migration_0023()
    columns = _0023_column_rows(migration)
    constraints = _0023_constraint_rows(migration)
    if extra_kind == "column":
        columns.append(
            {
                "name": "arbitrary_forward_column",
                "formatted_type": "text",
                "not_null": False,
                "default_expr": None,
            }
        )
    else:
        constraints.append(
            {
                "name": "arbitrary_forward_constraint",
                "constraint_type": "c",
                "validated": True,
                "definition": "CHECK (true)",
            }
        )

    def mapping_rows(_connection, sql, **_params):
        if "FROM pg_class" in sql:
            return [{"relkind": "r"}]
        if "FROM pg_attribute" in sql:
            return columns
        if "FROM pg_constraint" in sql:
            return constraints
        raise AssertionError(sql)

    monkeypatch.setattr(migration, "_mapping_rows", mapping_rows)
    with pytest.raises(RuntimeError, match=rf"unexpected {extra_kind}s: arbitrary_forward"):
        migration._validate_existing_table(
            object(),
            allow_complete_schema_default=True,
        )


def test_0023_accepts_only_known_0026_and_0027_forward_triggers(monkeypatch) -> None:
    migration = _migration_0023()
    triggers = [
        {
            "name": migration._FORWARD_COMPAT_TRIGGER,
            "enabled": "O",
            "definition": migration._FORWARD_COMPAT_TRIGGER_DEFINITION,
        },
        {
            "name": migration._FORWARD_REQUEST_KEY_GUARD_TRIGGER,
            "enabled": "O",
            "definition": migration._FORWARD_REQUEST_KEY_GUARD_TRIGGER_DEFINITION,
        },
    ]

    def mapping_rows(_connection, sql, **_params):
        if "FROM pg_policies" in sql:
            return []
        if "FROM pg_trigger" in sql:
            return triggers
        raise AssertionError(sql)

    monkeypatch.setattr(migration, "_mapping_rows", mapping_rows)
    migration._validate_known_security_objects(object())

    triggers[1]["definition"] = "CREATE TRIGGER wrong_origin_key_guard"
    with pytest.raises(RuntimeError, match="request-key guard trigger is incompatible"):
        migration._validate_known_security_objects(object())
    triggers[1]["definition"] = migration._FORWARD_REQUEST_KEY_GUARD_TRIGGER_DEFINITION

    triggers.append(
        {
            "name": "trg_arbitrary_forward_object",
            "enabled": "O",
            "definition": "CREATE TRIGGER trg_arbitrary_forward_object",
        }
    )
    with pytest.raises(RuntimeError, match="unexpected triggers: trg_arbitrary"):
        migration._validate_known_security_objects(object())


def test_0026_downgrade_intentionally_retains_irreversible_audit_data() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    downgrade = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )

    assert len(downgrade.body) == 1
    assert isinstance(downgrade.body[0], ast.Expr)
    assert isinstance(downgrade.body[0].value, ast.Constant)
    explanation = ast.get_docstring(downgrade) or ""
    assert "Retain attempt actors" in explanation
    assert "irreversible audit evidence" in explanation
    assert "retained compatibility trigger" in explanation
    assert "pre-0026" in explanation
    assert "increments ``attempt_count``" in explanation
    assert not any(isinstance(node, ast.Call) for node in ast.walk(downgrade))
