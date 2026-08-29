"""Drift guards for compliance-hold/media-delete serialization."""

from __future__ import annotations

import re
from pathlib import Path


_BACKEND = Path(__file__).resolve().parents[2]
_VERSIONS = _BACKEND / "Alembic" / "versions"
_MIGRATION = _VERSIONS / "0025_media_hold_serialization.py"
_COMPLETE_SCHEMA = _BACKEND / "database" / "complete_schema.sql"
_HOLD_FUNCTION = "serialize_compliance_media_hold"
_HOLD_TRIGGER = "trg_serialize_compliance_media_hold"
_MEMBERSHIP_FUNCTION = "serialize_tenant_partner_membership"
_MEMBERSHIP_TRIGGER = "trg_serialize_tenant_partner_membership"


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().rstrip(";")


def _canonical_sql(value: str) -> str:
    return re.sub(r"\s*([(),;])\s*", r"\1", _normalized(value))


def _sql_object(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start) + len(end_marker)
    return _normalized(source[start:end])


def _function_sql(source: str, function: str = _HOLD_FUNCTION) -> str:
    return _sql_object(
        source,
        f"CREATE OR REPLACE FUNCTION {function}()",
        "$$ LANGUAGE plpgsql",
    )


def _trigger_sql(
    source: str,
    trigger: str = _HOLD_TRIGGER,
    function: str = _HOLD_FUNCTION,
) -> str:
    return _sql_object(
        source,
        f"CREATE TRIGGER {trigger}",
        f"FOR EACH ROW EXECUTE FUNCTION {function}()",
    )


def test_0025_chains_directly_from_recording_permissions() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0025_media_hold_serialization"' in source
    assert 'down_revision: str | None = "0024_recording_permissions"' in source
    assert len("0025_media_hold_serialization") <= 32


def test_tenant_and_partner_holds_take_the_media_delete_advisory_lock() -> None:
    function = _function_sql(_MIGRATION.read_text(encoding="utf-8"))

    assert (
        "IF NEW.suspension_type = 'COMPLIANCE' AND NEW.is_active = TRUE "
        "AND NEW.restored_at IS NULL THEN"
    ) in function
    assert function.count("PERFORM pg_advisory_xact_lock(") == 2

    tenant_branch, partner_branch = function.split(
        "ELSIF NEW.target_type = 'partner' THEN",
        maxsplit=1,
    )
    assert "IF NEW.target_type = 'tenant' THEN" in tenant_branch
    assert "hashtextextended( 'talky:media-hold:' || NEW.target_id::text, 0 )" in tenant_branch

    tenant_query = "SELECT id FROM tenants WHERE white_label_partner_id = NEW.target_id ORDER BY id"
    assert tenant_query in partner_branch
    assert "hashtextextended( 'talky:media-hold:' || held_tenant_id::text, 0 )" in partner_branch
    assert partner_branch.index(tenant_query) < partner_branch.index("LOOP")
    assert partner_branch.index("LOOP") < partner_branch.index("PERFORM pg_advisory_xact_lock(")


def test_trigger_covers_compliance_hold_creation_and_activation_writes() -> None:
    trigger = _trigger_sql(_MIGRATION.read_text(encoding="utf-8"))

    assert trigger == _normalized(
        f"""
        CREATE TRIGGER {_HOLD_TRIGGER}
        BEFORE INSERT OR UPDATE OF
            target_type, target_id, suspension_type, is_active, restored_at,
            suspended_until
        ON suspension_events
        FOR EACH ROW EXECUTE FUNCTION {_HOLD_FUNCTION}()
        """
    )


def test_partner_membership_changes_take_the_tenant_media_lock() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    function = _function_sql(source, _MEMBERSHIP_FUNCTION)
    trigger = _trigger_sql(source, _MEMBERSHIP_TRIGGER, _MEMBERSHIP_FUNCTION)

    assert (
        "IF NEW.white_label_partner_id IS DISTINCT FROM " "OLD.white_label_partner_id THEN"
    ) in function
    assert function.count("PERFORM pg_advisory_xact_lock(") == 1
    assert "hashtextextended( 'talky:media-hold:' || NEW.id::text, 0 )" in function
    assert trigger == _normalized(
        f"""
        CREATE TRIGGER {_MEMBERSHIP_TRIGGER}
        BEFORE UPDATE OF white_label_partner_id
        ON tenants
        FOR EACH ROW EXECUTE FUNCTION {_MEMBERSHIP_FUNCTION}()
        """
    )


def test_complete_schema_matches_all_0025_functions_and_triggers() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    complete_schema = _COMPLETE_SCHEMA.read_text(encoding="utf-8")

    for function in (_HOLD_FUNCTION, _MEMBERSHIP_FUNCTION):
        assert _canonical_sql(_function_sql(complete_schema, function)) == _canonical_sql(
            _function_sql(migration, function)
        )
    for trigger, function in (
        (_HOLD_TRIGGER, _HOLD_FUNCTION),
        (_MEMBERSHIP_TRIGGER, _MEMBERSHIP_FUNCTION),
    ):
        assert _canonical_sql(_trigger_sql(complete_schema, trigger, function)) == _canonical_sql(
            _trigger_sql(migration, trigger, function)
        )

    normalized_schema = _normalized(complete_schema)
    assert f"DROP TRIGGER IF EXISTS {_HOLD_TRIGGER} ON suspension_events" in normalized_schema
    assert f"DROP TRIGGER IF EXISTS {_MEMBERSHIP_TRIGGER} ON tenants" in normalized_schema


def test_0025_downgrade_drops_trigger_before_function() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade() -> None:") :]
    cleanup = (
        f"DROP TRIGGER IF EXISTS {_MEMBERSHIP_TRIGGER} ",
        f"DROP FUNCTION IF EXISTS {_MEMBERSHIP_FUNCTION}()",
        f"DROP TRIGGER IF EXISTS {_HOLD_TRIGGER} ",
        f"DROP FUNCTION IF EXISTS {_HOLD_FUNCTION}()",
    )

    for statement in cleanup:
        assert statement in downgrade
    assert '"ON tenants"' in downgrade
    assert '"ON suspension_events"' in downgrade
    assert [downgrade.index(statement) for statement in cleanup] == sorted(
        downgrade.index(statement) for statement in cleanup
    )


def test_alembic_revision_graph_has_one_current_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND / "Alembic"))
    revisions = ScriptDirectory.from_config(config)

    # Derive the head instead of hardcoding it: the head is the one revision
    # nothing else points back to. Hardcoding "0029_trunk_runtime_status" made
    # this test fail on the very next migration (0030), which says nothing
    # about the property being guarded — a SINGLE head, i.e. no branch that
    # would make `alembic upgrade head` ambiguous.
    all_revisions = list(revisions.walk_revisions())
    parents: set[str] = set()
    for rev in all_revisions:
        down = rev.down_revision
        if down is None:
            continue
        parents.update(down if isinstance(down, tuple) else (down,))
    derived_heads = sorted(
        rev.revision for rev in all_revisions if rev.revision not in parents
    )

    assert len(derived_heads) == 1, f"migration graph has branched: {derived_heads}"
    assert sorted(revisions.get_heads()) == derived_heads
    assert (
        revisions.get_revision("0029_trunk_runtime_status").down_revision
        == "0028_call_terminal_settle_cas"
    )
    assert (
        revisions.get_revision("0028_call_terminal_settle_cas").down_revision
        == "0027_media_delete_request_keys"
    )
    assert (
        revisions.get_revision("0027_media_delete_request_keys").down_revision
        == "0026_media_delete_recovery"
    )
    assert (
        revisions.get_revision("0026_media_delete_recovery").down_revision
        == "0025_media_hold_serialization"
    )
    assert (
        revisions.get_revision("0025_media_hold_serialization").down_revision
        == "0024_recording_permissions"
    )
