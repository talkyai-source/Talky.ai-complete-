"""The decision half of ``scripts/seed_rbac_standalone.py``.

The script needs a database; the decisions it makes -- *what* to write, *in
what order*, *inside what transaction*, and *whether to commit at all* -- do
not, and those decisions are the whole risk.

The failure this script exists to prevent is specific and was observed on a
restored production replica: ``roles`` was empty, so the documented membership
backfill (``INSERT INTO tenant_users ... JOIN roles r ON r.name = up.role``)
returned ``INSERT 0 0`` and exit 0.  An operator reads that as success.  It is
not: ``rbac_data_is_seeded()`` in app/core/security/rbac.py flips to
DB-authoritative the moment ``role_permissions`` has a row AND one ``active``
``tenant_users`` row exists, and every user without a membership row is then
resolved to the empty permission set -- locked out.

So the tests below assert, without a database:

* statement order is permissions -> roles -> role_permissions -> tenant_users;
* every write happens inside ONE transaction that is explicitly committed;
* an orphan found after the backfill rolls that transaction back;
* a ``user_profiles.role`` value with no matching role fails loudly, with the
  list, instead of being skipped;
* ``INSERT 0 0`` is reported as a problem unless there was nothing to do;
* the role list is derived from ``UserRole``, not a hand-maintained list;
* a dry run issues no writes at all -- not even rolled-back ones.

``scripts/`` is not an importable package, so the module is loaded by path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Load the dependency module first: rbac.py imports CurrentUser from it.
from app.api.v1 import dependencies as _dependencies  # noqa: F401
from app.core.security.rbac import (
    ROLE_DEFAULT_PERMISSIONS,
    Permission,
    UserRole,
)

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "seed_rbac_standalone.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_seed_rbac_standalone", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules[cls.__module__],
    # so the module has to be registered before it is executed.
    sys.modules["_seed_rbac_standalone"] = module
    spec.loader.exec_module(module)
    return module


seed_rbac_standalone = _load()
M = seed_rbac_standalone


# ---------------------------------------------------------------------------
# A fake asyncpg connection: records every statement, scripts every read.
# ---------------------------------------------------------------------------


class FakeTransaction:
    def __init__(self, log: list):
        self._log = log
        self.started = False
        self.committed = False
        self.rolled_back = False

    async def start(self):
        self.started = True
        self._log.append(("BEGIN", ()))

    async def commit(self):
        self.committed = True
        self._log.append(("COMMIT", ()))

    async def rollback(self):
        self.rolled_back = True
        self._log.append(("ROLLBACK", ()))


class FakeConnection:
    """Serves the three reads the script makes and records every write."""

    def __init__(
        self,
        *,
        counts=None,
        profile_roles=(),
        missing_before=(),
        missing_after=None,
        insert_status="INSERT 0 1",
        backfill_status=None,
    ):
        self.log: list = []
        self.executed: list = []
        self.transactions: list[FakeTransaction] = []
        self._counts = list(counts or [_counts_row()])
        self._profile_roles = list(profile_roles)
        self._missing = [
            list(missing_before),
            list(missing_before if missing_after is None else missing_after),
        ]
        self._insert_status = insert_status
        self._backfill_status = backfill_status

    # -- reads --------------------------------------------------------------
    async def fetchrow(self, sql, *args):
        assert sql == M.COUNTS_SQL, f"unexpected fetchrow: {sql[:60]}"
        self.log.append(("fetchrow", ()))
        row = self._counts[0]
        if len(self._counts) > 1:
            self._counts.pop(0)
        return row

    async def fetch(self, sql, *args):
        self.log.append(("fetch", ()))
        if sql == M.PROFILE_ROLE_SQL:
            return list(self._profile_roles)
        if sql == M.MISSING_MEMBERSHIP_SQL:
            out = self._missing[0]
            if len(self._missing) > 1:
                self._missing.pop(0)
            return list(out)
        raise AssertionError(f"unexpected fetch: {sql[:60]}")

    # -- writes -------------------------------------------------------------
    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        self.log.append(("execute", sql))
        if "INTO tenant_users" in sql and self._backfill_status is not None:
            return self._backfill_status
        return self._insert_status

    def transaction(self):
        tx = FakeTransaction(self.log)
        self.transactions.append(tx)
        return tx


def _counts_row(**overrides):
    row = {
        "permissions": 7,
        "roles": 0,
        "role_permissions": 0,
        "tenant_users": 0,
        "user_profiles": 17,
    }
    row.update(overrides)
    return row


def _profile(role, n=1, without_tenant=0):
    return {"role": role, "n": n, "without_tenant": without_tenant}


def _user(email, role="user"):
    return {"id": f"id-{email}", "email": email, "role": role}


# The production shape proved on the restored replica: catalogue empty,
# memberships empty, 17 profiles across three legitimate role names.
PROD_PROFILE_ROLES = (
    _profile("platform_admin", 2, without_tenant=2),
    _profile("tenant_admin", 5),
    _profile("user", 10),
)
PROD_MISSING = tuple(_user(f"u{i}@example.com") for i in range(15))


# ---------------------------------------------------------------------------
# 1. The plan: order, source, and INSERT semantics
# ---------------------------------------------------------------------------


def test_statement_order_is_permissions_roles_role_permissions_tenant_users():
    keys = [s.key for s in M.build_statements()]
    # Every permission before every role, every role before every grant, the
    # backfill last: role_permissions cannot resolve a role that does not
    # exist yet, and the backfill cannot resolve a role name either.
    first = {}
    last = {}
    for index, key in enumerate(keys):
        first.setdefault(key, index)
        last[key] = index
    order = ["permissions", "roles", "role_permissions", "tenant_users"]
    assert set(keys) == set(order)
    for earlier, later in zip(order, order[1:]):
        assert last[earlier] < first[later], f"{earlier} must precede {later}"
    assert keys[-1] == "tenant_users"
    assert keys.count("tenant_users") == 1


def test_role_list_comes_from_userrole_not_a_hand_maintained_list():
    assert {spec.name for spec in M.role_specs()} == {r.value for r in UserRole}


def test_a_role_added_to_the_enum_reaches_the_database():
    # The regression the hardcoded 5-role dict in the old seeder caused:
    # campaign_manager/agent/billing_user were added to UserRole and still
    # never reached `roles`. A new role must appear with no edit here.
    extra = SimpleNamespace(value="auditor", level=25)
    specs = M.role_specs(list(UserRole) + [extra])
    names = [spec.name for spec in specs]
    assert "auditor" in names
    statements = M.build_statements(roles=list(UserRole) + [extra])
    assert any(
        s.key == "roles" and "auditor" in s.args for s in statements
    ), "a role in the enum must produce a roles INSERT"


def test_permission_statements_match_the_existing_seeder_semantics():
    statements = [s for s in M.build_statements() if s.key == "permissions"]
    assert len(statements) == len(list(Permission))
    for statement in statements:
        assert "INSERT INTO permissions" in statement.sql
        # Same arbiter as scripts/seed_rbac.py -- (resource, action), not name.
        assert "ON CONFLICT (resource, action) DO UPDATE" in statement.sql
    values = {s.args[0] for s in statements}
    assert values == {p.value for p in Permission}


def test_permission_value_splits_on_the_last_colon_like_the_schema():
    # database/complete_schema.sql stores platform:tenants:manage as
    # resource='platform:tenants', action='manage'. ON CONFLICT
    # (resource, action) must find that row; a first-colon split would
    # produce a second row with a duplicate name and hit uq_permissions_name.
    assert M.split_permission("platform:tenants:manage") == (
        "platform:tenants", "manage"
    )
    assert M.split_permission("campaigns:create") == ("campaigns", "create")
    # scripts/seed_rbac.py's bare split(":") raises on these three.
    two_colon = [p.value for p in Permission if p.value.count(":") == 2]
    assert two_colon, "the three-part platform permissions still exist"
    for value in two_colon:
        with pytest.raises(ValueError):
            _resource, _action = value.split(":")
        assert len(M.split_permission(value)) == 2


def test_role_statements_match_the_existing_seeder_semantics():
    specs = {spec.name: spec for spec in M.role_specs()}
    # is_system_role is always true; tenant_scoped is false only for
    # platform_admin; level comes from UserRole.level.
    assert specs["platform_admin"].tenant_scoped is False
    assert specs["tenant_admin"].tenant_scoped is True
    assert all(spec.is_system for spec in specs.values())
    assert specs["platform_admin"].level == UserRole.PLATFORM_ADMIN.level
    assert specs["readonly"].level == UserRole.READONLY.level
    statement = next(s for s in M.build_statements() if s.key == "roles")
    assert "INSERT INTO roles" in statement.sql
    assert "ON CONFLICT (name) DO UPDATE" in statement.sql


def test_role_permission_grants_come_from_role_default_permissions():
    grants = [s for s in M.build_statements() if s.key == "role_permissions"]
    expected = sum(
        len(ROLE_DEFAULT_PERMISSIONS.get(role, set())) for role in UserRole
    )
    assert len(grants) == expected
    for statement in grants:
        assert "INSERT INTO role_permissions" in statement.sql
        assert "ON CONFLICT (role_id, permission_id) DO NOTHING" in statement.sql


def test_backfill_only_touches_profiles_that_can_have_a_membership():
    statement = next(s for s in M.build_statements() if s.key == "tenant_users")
    assert "INSERT INTO tenant_users" in statement.sql
    assert "JOIN roles r ON r.name = up.role" in statement.sql
    # tenant_users.tenant_id is NOT NULL, so a profile with no tenant cannot
    # have a row at all.
    assert "up.tenant_id IS NOT NULL" in statement.sql
    assert "ON CONFLICT (user_id, tenant_id) DO NOTHING" in statement.sql
    assert "'active'" in statement.sql


# ---------------------------------------------------------------------------
# 2. One transaction, all or nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_write_happens_inside_one_committed_transaction():
    conn = FakeConnection(
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=PROD_MISSING,
        missing_after=(),
        backfill_status="INSERT 0 15",
    )
    outcome = await M.seed(conn, apply=True)

    assert len(conn.transactions) == 1
    tx = conn.transactions[0]
    assert tx.started and tx.committed and not tx.rolled_back
    assert outcome.committed is True
    assert outcome.exit_code == 0

    kinds = [entry[0] for entry in conn.log]
    begin = kinds.index("BEGIN")
    commit = kinds.index("COMMIT")
    writes = [i for i, entry in enumerate(conn.log) if entry[0] == "execute"]
    assert writes, "apply must write"
    assert all(begin < i < commit for i in writes)


@pytest.mark.asyncio
async def test_statements_reach_the_connection_in_plan_order():
    conn = FakeConnection(
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=PROD_MISSING,
        missing_after=(),
        backfill_status="INSERT 0 15",
    )
    await M.seed(conn, apply=True)
    executed = [sql for sql, _ in conn.executed]
    assert [s.sql for s in M.build_statements()] == executed


# ---------------------------------------------------------------------------
# 3. Refuse to leave orphans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_orphan_after_the_backfill_rolls_the_transaction_back():
    # A profile whose membership row already existed as 'suspended': the
    # ON CONFLICT DO NOTHING skips it, so it stays without an ACTIVE row and
    # is locked out the moment the catalogue goes authoritative.
    stranded = (_user("stranded@example.com", "user"),)
    conn = FakeConnection(
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=PROD_MISSING,
        missing_after=stranded,
        backfill_status="INSERT 0 14",
    )
    outcome = await M.seed(conn, apply=True)

    tx = conn.transactions[0]
    assert tx.rolled_back is True
    assert tx.committed is False
    assert outcome.committed is False
    assert outcome.exit_code != 0
    assert [row["email"] for row in outcome.orphans] == ["stranded@example.com"]


@pytest.mark.asyncio
async def test_allow_orphans_commits_but_still_reports_them():
    stranded = (_user("stranded@example.com", "user"),)
    conn = FakeConnection(
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=PROD_MISSING,
        missing_after=stranded,
        backfill_status="INSERT 0 14",
    )
    outcome = await M.seed(conn, apply=True, allow_orphans=True)

    assert conn.transactions[0].committed is True
    assert outcome.committed is True
    assert outcome.orphans, "the orphans are still reported"


@pytest.mark.asyncio
async def test_profiles_without_a_tenant_are_reported_separately_not_as_orphans():
    conn = FakeConnection(
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=PROD_MISSING,
        missing_after=(),
        backfill_status="INSERT 0 15",
    )
    outcome = await M.seed(conn, apply=True)
    assert outcome.orphans == []
    assert dict(outcome.unmappable) == {"platform_admin": 2}
    assert outcome.committed is True


def test_a_non_platform_admin_without_a_tenant_is_flagged_as_locked_out():
    # platform_admin bypasses the tenant check in require_permission(); any
    # other role with tenant_id IS NULL can never get a membership row and so
    # can never resolve a permission.
    rows = [_profile("platform_admin", 2, 2), _profile("user", 3, 1)]
    assert M.unmappable_at_risk(rows) == [("user", 1)]
    assert M.unmappable_at_risk([_profile("platform_admin", 2, 2)]) == []


# ---------------------------------------------------------------------------
# 4. Role-name mismatches fail loudly
# ---------------------------------------------------------------------------


def test_role_aliases_are_detected_as_unmatched():
    rows = [_profile("admin", 3), _profile("tenant_admin", 1)]
    names = {spec.name for spec in M.role_specs()}
    # normalize_role() accepts 'admin' as an alias of tenant_admin, but
    # roles.name never equals 'admin', so the backfill JOIN drops these users.
    assert M.unmatched_role_names(rows, names) == ["admin"]
    assert M.unmatched_role_names(list(PROD_PROFILE_ROLES), names) == []


def test_alias_remediation_names_the_intended_role():
    assert M.alias_remediation("admin") == "tenant_admin"
    assert M.alias_remediation("super_admin") == "platform_admin"
    assert M.alias_remediation("operator") == "agent"
    assert M.alias_remediation("nonsense") is None


@pytest.mark.asyncio
async def test_an_unmatched_role_fails_before_anything_is_written():
    conn = FakeConnection(
        profile_roles=(_profile("admin", 3), _profile("user", 4)),
        missing_before=PROD_MISSING,
    )
    outcome = await M.seed(conn, apply=True)

    assert outcome.unmatched_roles == ["admin"]
    assert outcome.exit_code != 0
    assert outcome.committed is False
    assert conn.executed == [], "no statement may run when a role is unmatched"
    assert conn.transactions == [], "no transaction is opened either"


# ---------------------------------------------------------------------------
# 5. INSERT 0 0 is never success
# ---------------------------------------------------------------------------


def test_rows_from_status_reads_the_asyncpg_tag():
    assert M.rows_from_status("INSERT 0 15") == 15
    assert M.rows_from_status("INSERT 0 0") == 0
    assert M.rows_from_status(None) == 0


def test_zero_row_backfill_with_work_outstanding_is_a_problem():
    status, _ = M.classify_backfill(inserted=0, eligible=15, roles_before=0)
    assert status == M.BACKFILL_ZERO_ROWS
    assert status != M.BACKFILL_NOOP


def test_zero_row_backfill_with_nothing_outstanding_is_an_idempotent_noop():
    status, _ = M.classify_backfill(inserted=0, eligible=0, roles_before=8)
    assert status == M.BACKFILL_NOOP


def test_a_backfill_that_inserted_rows_is_reported_as_such():
    status, _ = M.classify_backfill(inserted=15, eligible=15, roles_before=0)
    assert status == M.BACKFILL_INSERTED


@pytest.mark.asyncio
async def test_a_zero_row_backfill_is_a_failure_not_a_silent_success():
    # Exactly the replica rehearsal: 15 profiles need a membership and the
    # backfill writes none. `INSERT 0 0` + exit 0 is what must not happen.
    conn = FakeConnection(
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=PROD_MISSING,
        missing_after=PROD_MISSING,
        backfill_status="INSERT 0 0",
    )
    outcome = await M.seed(conn, apply=True)
    assert outcome.backfill_status == M.BACKFILL_ZERO_ROWS
    assert outcome.exit_code != 0
    assert outcome.committed is False


@pytest.mark.asyncio
async def test_a_second_run_is_a_noop_and_says_so():
    conn = FakeConnection(
        counts=[
            _counts_row(permissions=45, roles=8, role_permissions=120, tenant_users=15),
            _counts_row(permissions=45, roles=8, role_permissions=120, tenant_users=15),
        ],
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=(),
        missing_after=(),
        insert_status="INSERT 0 0",
        backfill_status="INSERT 0 0",
    )
    outcome = await M.seed(conn, apply=True)
    assert outcome.backfill_status == M.BACKFILL_NOOP
    assert outcome.exit_code == 0
    assert outcome.committed is True
    assert outcome.idempotent_noop is True


# ---------------------------------------------------------------------------
# 6. Dry run writes nothing at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_issues_no_writes():
    conn = FakeConnection(
        profile_roles=PROD_PROFILE_ROLES,
        missing_before=PROD_MISSING,
    )
    outcome = await M.seed(conn, apply=False)

    assert conn.executed == []
    assert conn.transactions == [], "a rolled-back write is still a write"
    assert outcome.applied is False
    assert outcome.committed is False
    assert outcome.exit_code == 0
    # It still has to say what it would do.
    assert outcome.before["roles"] == 0
    assert outcome.predicted_after["roles"] == len(list(UserRole))
    assert outcome.predicted_after["tenant_users"] == len(PROD_MISSING)


@pytest.mark.asyncio
async def test_dry_run_predicts_the_orphans_an_apply_would_leave():
    conn = FakeConnection(
        profile_roles=(_profile("admin", 2), _profile("user", 3)),
        missing_before=(_user("a@x", "admin"), _user("b@x", "user")),
    )
    outcome = await M.seed(conn, apply=False)
    # 'admin' matches no seeded role, so that user would be left behind.
    assert outcome.unmatched_roles == ["admin"]
    assert outcome.exit_code != 0
    assert conn.executed == []


# ---------------------------------------------------------------------------
# 7. Connection handling: DSN, and never printing credentials
# ---------------------------------------------------------------------------


def test_driver_suffix_is_stripped_from_the_dsn():
    assert M.normalize_dsn("postgresql+asyncpg://u:p@h:5432/db") == (
        "postgresql://u:p@h:5432/db"
    )
    assert M.normalize_dsn("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
    assert M.normalize_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_target_description_names_the_database_without_the_password():
    host, database = M.split_dsn("postgresql://talky:s3cr3t@144.76.17.150:5432/talky")
    assert (host, database) == ("144.76.17.150", "talky")
    line = M.describe_target("postgresql://talky:s3cr3t@144.76.17.150:5432/talky")
    assert "144.76.17.150" in line and "talky" in line
    assert "s3cr3t" not in line


def test_the_script_actually_starts_as_a_program():
    # rbac.py <-> app.api.v1.dependencies is a circular import: importing rbac
    # first from a __main__ script raises "cannot import name 'UserRole' from
    # partially initialized module". Every other test here loads the module
    # through importlib after the test file has already imported dependencies,
    # so only running it as a program catches that.
    import subprocess

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        cwd=str(_SCRIPT.resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout
    assert "--allow-orphans" in result.stdout


def test_there_is_no_production_refusal():
    # Unlike scripts/seed_validation_tenants.py, this script is MEANT for
    # production: a guard that refuses would make the one database that needs
    # seeding the one database it cannot seed.
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "REFUSING TO SEED" not in source
    flags = M.build_parser().parse_args([])
    assert flags.apply is False, "writing must be opt-in"
