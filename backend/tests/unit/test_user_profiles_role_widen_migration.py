"""Drift guards for migration 0035 (user_profiles.role CHECK widening).

The migration deliberately does NOT import application code, so the allowed
role list is spelled out in the revision file.  These tests make that list
un-driftable against ``UserRole`` and pin the two behaviours that make the
revision safe to run on a real database: it discovers the constraint name from
``pg_constraint`` instead of guessing it, and its downgrade refuses rather than
rewriting principals whose role only exists post-0035.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

# Load the dependency module first: this repository intentionally re-exports
# RBAC dependencies from its footer, while rbac.py imports CurrentUser.
from app.api.v1 import dependencies as _dependencies  # noqa: F401
from app.core.security.rbac import UserRole

_BACKEND = Path(__file__).resolve().parents[2]
_VERSIONS = _BACKEND / "Alembic" / "versions"
_MIGRATION = _VERSIONS / "0035_user_profiles_role_widen.py"
_COMPLETE_SCHEMA = _BACKEND / "database" / "complete_schema.sql"

_AUTO_NAME = "user_profiles_role_check"
_CANONICAL_NAME = "chk_user_profiles_role_valid"
_PROBE_NAME = "__talky_0035_role_check_probe"
_LEGACY_ROLES = (
    "platform_admin",
    "partner_admin",
    "tenant_admin",
    "user",
    "readonly",
)
_ALLOWED_ROLES = (
    "platform_admin",
    "partner_admin",
    "tenant_admin",
    "campaign_manager",
    "user",
    "agent",
    "billing_user",
    "readonly",
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("alembic_0035_role_widen", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fake bind: enough of the SQLAlchemy result surface for upgrade()/downgrade().
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[Any], scalar: Any = None) -> None:
        self._rows = rows
        self._scalar = scalar

    def fetchall(self) -> list[Any]:
        return self._rows

    def mappings(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar


class _Bind:
    def __init__(
        self,
        *,
        constraint_names: list[str],
        constraint_roles: tuple[str, ...] = _LEGACY_ROLES,
        constraint_validated: bool = True,
        constraint_expression: str | None = None,
        offenders: list[dict[str, Any]] | None = None,
        new_role_rows: int = 0,
        installed_expression_override: str | None = None,
        suppress_installed_validation: bool = False,
    ) -> None:
        self.constraint_names = list(constraint_names)
        self.constraint_expressions = {
            name: constraint_expression or self.expression_for(constraint_roles)
            for name in constraint_names
        }
        self.constraint_validated = {
            name: constraint_validated for name in constraint_names
        }
        self.offenders = offenders or []
        self.new_role_rows = new_role_rows
        self.installed_expression_override = installed_expression_override
        self.suppress_installed_validation = suppress_installed_validation
        self.queries: list[str] = []

    @staticmethod
    def expression_for(roles: tuple[str, ...] | list[str]) -> str:
        return "ROLE_CHECK:" + ",".join(roles)

    @staticmethod
    def restored_dump_expression_for(roles: tuple[str, ...] | list[str]) -> str:
        return "RESTORED_DUMP_ROLE_CHECK:" + ",".join(roles)

    def execute(self, statement: Any) -> _Result:
        sql = str(statement)
        self.queries.append(sql)
        if "JOIN pg_attribute" in sql:
            return _Result(
                [
                    {
                        "conname": name,
                        "convalidated": self.constraint_validated[name],
                        "expression": self.constraint_expressions[name],
                    }
                    for name in sorted(self.constraint_names)
                ]
            )
        if f"c.conname = '{_PROBE_NAME}'" in sql:
            if _PROBE_NAME not in self.constraint_names:
                return _Result([])
            return _Result(
                [
                    {
                        "convalidated": self.constraint_validated[_PROBE_NAME],
                        "expression": self.constraint_expressions[_PROBE_NAME],
                    }
                ]
            )
        if re.search(r"SELECT\s+role,\s+count\(\*\)", sql):
            return _Result(list(self.offenders))
        return _Result([], scalar=self.new_role_rows)


class _Op:
    def __init__(self, bind: _Bind) -> None:
        self._bind = bind
        self.statements: list[str] = []

    def execute(self, statement: Any) -> None:
        sql = str(statement)
        self.statements.append(sql)
        if "ALTER TABLE" in sql or "LOCK TABLE" in sql:
            self._apply(sql)
            return
        self._bind.execute(statement)

    def _apply(self, sql: str) -> None:
        dropped = re.search(r'DROP CONSTRAINT "([^"]+)"', sql)
        if dropped:
            name = dropped.group(1)
            self._bind.constraint_names = [
                existing for existing in self._bind.constraint_names if existing != name
            ]
            self._bind.constraint_expressions.pop(name, None)
            self._bind.constraint_validated.pop(name, None)
        added = re.search(r'ADD CONSTRAINT "?(\w+)"?', sql)
        if added:
            self._bind.queries.append(sql)
            name = added.group(1)
            roles = re.findall(r"'([a-z_]+)'", sql)
            expression = (
                self._bind.restored_dump_expression_for(roles)
                if "::character varying" in sql
                else self._bind.expression_for(roles)
            )
            if name == _CANONICAL_NAME and self._bind.installed_expression_override is not None:
                expression = self._bind.installed_expression_override
            self._bind.constraint_names = sorted({*self._bind.constraint_names, name})
            self._bind.constraint_expressions[name] = expression
            self._bind.constraint_validated[name] = "NOT VALID" not in sql
        validated = re.search(r"VALIDATE CONSTRAINT (\w+)", sql)
        if validated:
            name = validated.group(1)
            if not (
                name == _CANONICAL_NAME and self._bind.suppress_installed_validation
            ):
                self._bind.constraint_validated[name] = True

    def get_bind(self) -> _Bind:
        return self._bind


def _run(module: ModuleType, func: str, bind: _Bind) -> _Op:
    fake_op = _Op(bind)
    original = module.op
    module.op = fake_op  # type: ignore[assignment]
    try:
        getattr(module, func)()
    finally:
        module.op = original  # type: ignore[assignment]
    return fake_op


# ---------------------------------------------------------------------------
# Drift guards
# ---------------------------------------------------------------------------


def test_migration_allowed_roles_match_userrole_exactly() -> None:
    module = _load_migration()

    assert list(module._ALLOWED_ROLES) == [role.value for role in UserRole]
    assert set(module._ALLOWED_ROLES) == {role.value for role in UserRole}


def test_new_roles_are_the_three_userrole_additions() -> None:
    module = _load_migration()

    assert set(module._LEGACY_ROLES) < set(module._ALLOWED_ROLES)
    assert set(module._NEW_ROLES) == {"campaign_manager", "agent", "billing_user"}
    assert set(module._LEGACY_ROLES) | set(module._NEW_ROLES) == set(module._ALLOWED_ROLES)


def test_operator_alias_resolves_to_a_role_the_check_allows() -> None:
    """goals.md §12 spells the role "Agent/operator"; only one is storable."""
    module = _load_migration()

    from app.core.security.rbac import normalize_role

    assert normalize_role("operator").value in module._ALLOWED_ROLES


def test_complete_schema_bootstrap_matches_the_widened_list() -> None:
    module = _load_migration()
    line = next(
        text
        for text in _COMPLETE_SCHEMA.read_text(encoding="utf-8").splitlines()
        if text.strip().startswith("role VARCHAR(50)")
    )

    assert re.findall(r"'([a-z_]+)'", line.split("CHECK", 1)[1]) == list(module._ALLOWED_ROLES)
    assert _CANONICAL_NAME in line


# ---------------------------------------------------------------------------
# Chain / structure
# ---------------------------------------------------------------------------


def test_0035_chains_directly_from_the_four_eye_revision() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0035_user_profiles_role_widen"' in source
    assert 'down_revision: str | None = "0034_inbound_billing_four_eye"' in source
    assert len("0035_user_profiles_role_widen") <= 32


def test_alembic_revision_graph_still_has_one_current_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND / "Alembic"))
    revisions = ScriptDirectory.from_config(config)

    all_revisions = list(revisions.walk_revisions())
    parents: set[str] = set()
    for rev in all_revisions:
        down = rev.down_revision
        if down is None:
            continue
        parents.update(down if isinstance(down, tuple) else (down,))
    derived_heads = sorted(rev.revision for rev in all_revisions if rev.revision not in parents)

    assert len(derived_heads) == 1, f"migration graph has branched: {derived_heads}"
    assert sorted(revisions.get_heads()) == derived_heads
    assert (
        revisions.get_revision("0035_user_profiles_role_widen").down_revision
        == "0034_inbound_billing_four_eye"
    )


def test_migration_catalog_guards_definition_validation_and_public_schema() -> None:
    """Only one known, validated, exact parsed CHECK may be replaced."""
    source = _MIGRATION.read_text(encoding="utf-8")

    discovery = source[source.index("def _role_check_constraints") :]
    discovery = discovery[: discovery.index("def _reject_rows_outside")]
    assert "FROM pg_constraint" in discovery
    assert "c.contype = 'c'" in discovery
    assert "a.attname = 'role'" in discovery
    assert "array_length(c.conkey, 1) = 1" in discovery
    assert "c.convalidated" in discovery
    assert "pg_get_expr(c.conbin, c.conrelid, true) AS expression" in discovery
    assert "_known_role_check_expressions(_LEGACY_ROLES)" in source
    assert "_known_role_check_expressions(_ALLOWED_ROLES)" in source
    assert "LOCK TABLE public.user_profiles" in source
    assert "ALTER TABLE public.user_profiles" in source
    assert "FROM public.user_profiles" in source


# ---------------------------------------------------------------------------
# upgrade(): handles either historical constraint name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("existing", "existing_roles"),
    [
        (_AUTO_NAME, _LEGACY_ROLES),
        (_CANONICAL_NAME, _LEGACY_ROLES),
        (_CANONICAL_NAME, _ALLOWED_ROLES),
    ],
    ids=["auto_legacy", "canonical_legacy", "canonical_already_widened"],
)
def test_upgrade_replaces_one_known_exact_constraint(
    existing: str,
    existing_roles: tuple[str, ...],
) -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[existing],
        constraint_roles=existing_roles,
    )

    fake_op = _run(module, "upgrade", bind)

    dropped = [
        match.group(1)
        for match in (re.search(r'DROP CONSTRAINT "([^"]+)"', sql) for sql in fake_op.statements)
        if match and match.group(1) != _PROBE_NAME
    ]
    assert dropped == [existing]

    added = next(
        sql
        for sql in fake_op.statements
        if f"ADD CONSTRAINT {_CANONICAL_NAME}" in sql
    )
    assert _CANONICAL_NAME in added
    assert re.findall(r"'([a-z_]+)'", added) == list(module._ALLOWED_ROLES)
    assert any("VALIDATE CONSTRAINT" in sql for sql in fake_op.statements)
    assert bind.constraint_names == [_CANONICAL_NAME]
    assert bind.constraint_validated[_CANONICAL_NAME] is True
    assert bind.constraint_expressions[_CANONICAL_NAME] == bind.expression_for(_ALLOWED_ROLES)


def test_upgrade_accepts_the_exact_preserved_pg_dump_parse_shape() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_CANONICAL_NAME],
        constraint_expression=_Bind.restored_dump_expression_for(_LEGACY_ROLES),
    )

    _run(module, "upgrade", bind)

    assert bind.constraint_names == [_CANONICAL_NAME]
    assert bind.constraint_expressions[_CANONICAL_NAME] == bind.expression_for(_ALLOWED_ROLES)


@pytest.mark.parametrize(
    ("constraint_names", "match"),
    [
        ([], "expected exactly one"),
        ([_AUTO_NAME, _CANONICAL_NAME], "expected exactly one"),
    ],
    ids=["missing", "duplicate"],
)
def test_upgrade_refuses_missing_or_duplicate_role_checks(
    constraint_names: list[str],
    match: str,
) -> None:
    module = _load_migration()
    bind = _Bind(constraint_names=constraint_names)

    with pytest.raises(RuntimeError, match=match):
        _run(module, "upgrade", bind)

    assert bind.constraint_names == constraint_names


def test_upgrade_refuses_unknown_constraint_name_before_drop() -> None:
    module = _load_migration()
    bind = _Bind(constraint_names=["custom_role_guard"])

    with pytest.raises(RuntimeError, match="unknown constraint name"):
        _run(module, "upgrade", bind)

    assert bind.constraint_names == ["custom_role_guard"]


def test_upgrade_refuses_unvalidated_constraint_before_drop() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_CANONICAL_NAME],
        constraint_validated=False,
    )

    with pytest.raises(RuntimeError, match="is not validated"):
        _run(module, "upgrade", bind)

    assert bind.constraint_names == [_CANONICAL_NAME]


def test_upgrade_refuses_unknown_constraint_definition_before_drop() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_CANONICAL_NAME],
        constraint_expression="ROLE_CHECK:platform_admin,readonly,custom_role",
    )

    with pytest.raises(RuntimeError, match="unknown parsed definition"):
        _run(module, "upgrade", bind)

    assert bind.constraint_names == [_CANONICAL_NAME]
    assert _PROBE_NAME not in bind.constraint_names


def test_upgrade_refuses_rows_holding_an_unknown_role() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_AUTO_NAME],
        offenders=[{"role": "superuser", "rows": 2}],
    )

    with pytest.raises(RuntimeError) as excinfo:
        _run(module, "upgrade", bind)

    assert "'superuser'=2" in str(excinfo.value)


def test_upgrade_fails_loudly_if_the_installed_check_is_incomplete() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_AUTO_NAME],
        installed_expression_override="ROLE_CHECK:platform_admin",
    )

    with pytest.raises(RuntimeError) as excinfo:
        _run(module, "upgrade", bind)

    assert "does not exactly match" in str(excinfo.value)


def test_upgrade_fails_loudly_if_installed_check_stays_unvalidated() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_AUTO_NAME],
        suppress_installed_validation=True,
    )

    with pytest.raises(RuntimeError, match="installed an unvalidated"):
        _run(module, "upgrade", bind)


# ---------------------------------------------------------------------------
# downgrade(): refuses instead of destroying data
# ---------------------------------------------------------------------------


def test_downgrade_refuses_while_rows_use_a_new_role() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_CANONICAL_NAME],
        constraint_roles=_ALLOWED_ROLES,
        new_role_rows=7,
    )

    with pytest.raises(RuntimeError) as excinfo:
        _run(module, "downgrade", bind)

    message = str(excinfo.value)
    assert "Refusing to downgrade 0035" in message
    assert "7 user_profiles row(s)" in message
    # Nothing was altered before the guard tripped.
    assert bind.constraint_names == [_CANONICAL_NAME]


def test_downgrade_counts_every_new_role() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_CANONICAL_NAME],
        constraint_roles=_ALLOWED_ROLES,
        new_role_rows=1,
    )

    with pytest.raises(RuntimeError):
        _run(module, "downgrade", bind)

    count_query = next(
        sql for sql in bind.queries if "count(*) FROM public.user_profiles" in sql
    )
    assert set(re.findall(r"'([a-z_]+)'", count_query)) == set(module._NEW_ROLES)


def test_downgrade_restores_the_narrow_list_when_no_row_blocks_it() -> None:
    module = _load_migration()
    bind = _Bind(
        constraint_names=[_CANONICAL_NAME],
        constraint_roles=_ALLOWED_ROLES,
        new_role_rows=0,
    )

    fake_op = _run(module, "downgrade", bind)

    added = next(
        sql
        for sql in fake_op.statements
        if f"ADD CONSTRAINT {_CANONICAL_NAME}" in sql
    )
    assert re.findall(r"'([a-z_]+)'", added) == list(module._LEGACY_ROLES)
    assert "campaign_manager" not in added
    assert bind.constraint_validated[_CANONICAL_NAME] is True
    assert bind.constraint_expressions[_CANONICAL_NAME] == bind.expression_for(_LEGACY_ROLES)


def test_migration_module_is_importable_and_exposes_both_directions() -> None:
    module = _load_migration()

    assert callable(module.upgrade)
    assert callable(module.downgrade)
    assert isinstance(module.revision, str)
    assert SimpleNamespace(**vars(module)).down_revision == "0034_inbound_billing_four_eye"
