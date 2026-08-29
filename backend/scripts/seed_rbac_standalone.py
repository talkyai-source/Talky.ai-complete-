#!/usr/bin/env python3
"""Seed the RBAC catalogue AND backfill tenant memberships, atomically.

    python scripts/seed_rbac_standalone.py                    # dry run
    python scripts/seed_rbac_standalone.py --apply            # write
    python scripts/seed_rbac_standalone.py --apply --allow-orphans

WHY THIS EXISTS (and why not scripts/seed_rbac.py)
--------------------------------------------------
Two things are wrong with running ``scripts/seed_rbac.py`` on the production
host, and both were confirmed against a restored replica of production:

1. It obtains its pool through ``get_container()`` / ``await
   container.startup()``.  That boots Redis and the telephony ownership
   machinery as a side effect of what should be four INSERT statements, so it
   is not a thing you run casually -- and it never has been run: ``roles``,
   ``role_permissions`` and ``tenant_users`` are all **0 rows** in production.
   This script talks to ``asyncpg`` directly with ``DATABASE_URL`` and boots
   nothing.

2. It seeds ``permissions``, ``roles`` and ``role_permissions`` and stops.
   Seeding the catalogue *without* backfilling memberships is not a partial
   improvement, it is the dangerous state.  ``rbac_data_is_seeded()``
   (app/core/security/rbac.py) probes two things globally:

       EXISTS (SELECT 1 FROM role_permissions)
       EXISTS (SELECT 1 FROM tenant_users WHERE status = 'active')

   While both are empty, ``require_permission`` falls back to
   ``ROLE_DEFAULT_PERMISSIONS`` and everyone keeps working.  The instant the
   catalogue exists AND one active membership exists, database grants become
   authoritative for **everybody** -- and every user with no ``tenant_users``
   row resolves to the empty permission set and is locked out of every
   permission-gated route.  Catalogue and memberships therefore have to land
   in the SAME transaction, which is what this script does.

THE FAILURE THIS SCRIPT REFUSES TO REPEAT
-----------------------------------------
Run against that replica, the documented membership backfill

    INSERT INTO tenant_users (...)
    SELECT ... FROM user_profiles up JOIN roles r ON r.name = up.role

returned ``INSERT 0 0`` and exit 0.  It matched nothing because ``roles`` was
empty; an operator reads that pair as success.  Migrations 0022 and 0024 have
the same shape (``INSERT ... SELECT ... JOIN roles``) and insert zero rows
against an empty catalogue while reporting success.  So here:

* zero inserted rows while profiles still need a membership is a **failure**
  with an explanation, never a silent success;
* zero inserted rows with nothing outstanding is an idempotent no-op and says
  so;
* a ``user_profiles.role`` value with no matching ``roles.name`` -- exactly
  what makes that JOIN match nothing -- aborts with the list, before any write,
  instead of quietly skipping those users.  ``normalize_role()`` accepts the
  aliases ``admin``/``owner``/``super_admin``/``operator``, none of which is a
  role name, so this is a live hazard, not a hypothetical one.

WHAT IT WRITES
--------------
Exactly what ``scripts/seed_rbac.py`` writes -- same tables, same ``ON
CONFLICT`` arbiters, same ``level``/``is_system_role``/``tenant_scoped``
derivation, all taken from ``Permission``, ``UserRole`` and
``ROLE_DEFAULT_PERMISSIONS`` rather than a hand-maintained list (the previous
hardcoded 5-role dict is why ``campaign_manager``, ``agent`` and
``billing_user`` could exist in the enum and never reach the database) -- plus
the ``tenant_users`` backfill it omits.

The one deliberate difference in form: role and permission ids are resolved by
sub-select on the natural key inside each statement rather than by ``RETURNING
id`` into Python.  The rows written are identical; it keeps every statement
static, which is what makes the plan testable without a database.

NO PRODUCTION GUARD
-------------------
``scripts/seed_validation_tenants.py`` refuses to touch production because it
writes 50 fake tenants.  This script is *for* production -- production is the
database whose catalogue is empty.  Instead of refusing it prints the host and
database name it is about to modify (never credentials) and writes nothing
without ``--apply``.

EXIT CODES
----------
    0  dry run completed, or --apply committed
    2  refused: unmatched role names, orphans, or a zero-row backfill
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

sys.path.insert(0, os.getcwd())

# rbac.py imports CurrentUser from app.api.v1.dependencies, which imports back
# from rbac. Importing rbac FIRST from a __main__ script hits that cycle
# ("cannot import name 'UserRole' from partially initialized module"); loading
# the dependencies module first breaks it. Importing app.core.container is what
# scripts/seed_rbac.py does instead -- and that is the container boot this
# script exists to avoid.
import app.api.v1.dependencies  # noqa: E402,F401

# The catalogue is the enums. Imported, never re-typed: a role or permission
# that exists in code and not in the database is a silent privilege change.
from app.core.security.rbac import (  # noqa: E402
    ROLE_ALIASES,
    ROLE_DEFAULT_PERMISSIONS,
    Permission,
    UserRole,
)

# ---------------------------------------------------------------------------
# Reads. Named constants so tests can drive the script with a fake connection.
# ---------------------------------------------------------------------------

COUNTS_SQL = """
    SELECT
        (SELECT count(*) FROM permissions)      AS permissions,
        (SELECT count(*) FROM roles)            AS roles,
        (SELECT count(*) FROM role_permissions) AS role_permissions,
        (SELECT count(*) FROM tenant_users)     AS tenant_users,
        (SELECT count(*) FROM user_profiles)    AS user_profiles
"""

# Every distinct role name in use, with how many of those profiles have no
# tenant at all (they can never own a tenant_users row -- tenant_id is NOT
# NULL -- so they are reported separately rather than counted as orphans).
PROFILE_ROLE_SQL = """
    SELECT role,
           count(*)                                   AS n,
           count(*) FILTER (WHERE tenant_id IS NULL)  AS without_tenant
    FROM user_profiles
    GROUP BY role
    ORDER BY role
"""

# Profiles that COULD hold an active membership and do not. Read before the
# backfill it is the work list; read again after, inside the same transaction,
# it is the verification -- any row still here is a user who would be locked
# out the moment this transaction commits.
MISSING_MEMBERSHIP_SQL = """
    SELECT up.id, up.email, up.role
    FROM user_profiles up
    WHERE up.tenant_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM tenant_users tu
          WHERE tu.user_id = up.id AND tu.status = 'active'
      )
    ORDER BY up.email
"""

COUNT_KEYS = (
    "permissions",
    "roles",
    "role_permissions",
    "tenant_users",
    "user_profiles",
)

BACKFILL_INSERTED = "inserted"
BACKFILL_NOOP = "noop"
BACKFILL_ZERO_ROWS = "zero_rows"


# ---------------------------------------------------------------------------
# The plan (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Statement:
    """One statement this script will run, and where it sits in the order."""

    key: str          # permissions | roles | role_permissions | tenant_users
    sql: str
    args: tuple = ()


@dataclass(frozen=True)
class RoleSpec:
    """A row for ``roles``, derived from ``UserRole`` and nothing else."""

    name: str
    level: int
    tenant_scoped: bool
    is_system: bool = True


def role_specs(roles: Optional[Iterable[Any]] = None) -> list[RoleSpec]:
    """Every role the database must hold.

    ``roles`` defaults to ``UserRole`` itself so a role added to the enum
    reaches the database with no edit here. Each entry needs ``.value`` and
    ``.level``; the derivation matches scripts/seed_rbac.py exactly --
    ``is_system_role`` always true, ``tenant_scoped`` false only for
    ``platform_admin``.
    """
    source = list(UserRole) if roles is None else list(roles)
    return [
        RoleSpec(
            name=role.value,
            level=role.level,
            tenant_scoped=role.value != UserRole.PLATFORM_ADMIN.value,
        )
        for role in source
    ]


def split_permission(value: str) -> tuple:
    """``Permission.value`` -> ``(resource, action)``.

    Split on the LAST colon, not the first, and not with a bare ``split(":")``.
    Three permissions carry two colons -- ``platform:tenants:manage``,
    ``platform:users:manage``, ``platform:settings:manage`` -- and
    ``database/complete_schema.sql`` stores them as resource
    ``'platform:tenants'`` + action ``'manage'``.  Matching that exactly is not
    cosmetic: ``ON CONFLICT (resource, action)`` has to find the existing row,
    or the upsert becomes an INSERT of a duplicate ``name`` and dies on
    ``uq_permissions_name``.

    ``scripts/seed_rbac.py`` uses ``value.split(":")`` and therefore raises
    ``ValueError: too many values to unpack`` on the first of these three --
    further evidence it has never completed a run.
    """
    resource, _, action = value.rpartition(":")
    return resource, action


PERMISSION_SQL = """
    INSERT INTO permissions (name, description, resource, action, is_system)
    VALUES ($1, $2, $3, $4, true)
    ON CONFLICT (resource, action) DO UPDATE SET name = EXCLUDED.name
"""

ROLE_SQL = """
    INSERT INTO roles (name, description, level, is_system_role, tenant_scoped)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (name) DO UPDATE SET level = EXCLUDED.level
"""

# role_id / permission_id by natural key: both rows are written earlier in this
# same transaction, so the sub-selects always resolve.
ROLE_PERMISSION_SQL = """
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM roles r, permissions p
    WHERE r.name = $1 AND p.name = $2
    ON CONFLICT (role_id, permission_id) DO NOTHING
"""

# The backfill scripts/seed_rbac.py never had.
#
# ``is_primary`` is computed, not hardcoded true: ``idx_tu_user_primary`` is a
# UNIQUE index on ``user_id`` WHERE ``is_primary``, and an ``ON CONFLICT
# (user_id, tenant_id)`` arbiter would not catch a violation of it. The
# sub-select sees the pre-statement snapshot, and a profile holds one tenant,
# so at most one row per user is inserted here.
TENANT_USER_BACKFILL_SQL = """
    INSERT INTO tenant_users (
        user_id, tenant_id, role_id, is_primary, status, joined_at
    )
    SELECT up.id,
           up.tenant_id,
           r.id,
           NOT EXISTS (
               SELECT 1 FROM tenant_users pr
               WHERE pr.user_id = up.id AND pr.is_primary
           ),
           'active',
           NOW()
    FROM user_profiles up
    JOIN roles r ON r.name = up.role
    WHERE up.tenant_id IS NOT NULL
    ON CONFLICT (user_id, tenant_id) DO NOTHING
"""


def build_statements(
    roles: Optional[Iterable[Any]] = None,
    permissions: Optional[Iterable[Any]] = None,
    role_permissions: Optional[dict] = None,
) -> list[Statement]:
    """Every statement, in the only order that can work.

    permissions -> roles -> role_permissions -> tenant_users. A grant cannot
    resolve a role that does not exist yet, and the backfill cannot resolve a
    role name either -- which is precisely how the documented backfill
    returned ``INSERT 0 0``.
    """
    permission_source = list(Permission) if permissions is None else list(permissions)
    grants = ROLE_DEFAULT_PERMISSIONS if role_permissions is None else role_permissions
    specs = role_specs(roles)
    role_objects = list(UserRole) if roles is None else list(roles)

    out: list[Statement] = []

    for permission in permission_source:
        resource, action = split_permission(permission.value)
        out.append(
            Statement(
                "permissions",
                PERMISSION_SQL,
                (
                    permission.value,
                    f"Permission to {action} {resource}",
                    resource,
                    action,
                ),
            )
        )

    for spec in specs:
        out.append(
            Statement(
                "roles",
                ROLE_SQL,
                (
                    spec.name,
                    f"{spec.name.replace('_', ' ').title()} role",
                    spec.level,
                    spec.is_system,
                    spec.tenant_scoped,
                ),
            )
        )

    # Keyed by NAME, not by enum identity: a role supplied by a caller (or a
    # reloaded module) is a different object from the ROLE_DEFAULT_PERMISSIONS
    # key even when it means the same role.
    grants_by_name = {
        getattr(role, "value", role): permissions_for
        for role, permissions_for in grants.items()
    }
    for role in role_objects:
        # Sorted so two runs emit an identical plan; sets have no order.
        for permission in sorted(
            grants_by_name.get(role.value, set()), key=lambda p: p.value
        ):
            out.append(
                Statement(
                    "role_permissions",
                    ROLE_PERMISSION_SQL,
                    (role.value, permission.value),
                )
            )

    out.append(Statement("tenant_users", TENANT_USER_BACKFILL_SQL, ()))
    return out


# ---------------------------------------------------------------------------
# Decisions (pure)
# ---------------------------------------------------------------------------


def unmatched_role_names(
    profile_rows: Iterable[Any], seeded_names: Iterable[str]
) -> list[str]:
    """``user_profiles.role`` values that will match no ``roles.name``.

    These are the users the backfill JOIN drops on the floor. Reported, never
    skipped: a dropped user is a locked-out user.
    """
    known = set(seeded_names)
    out = []
    for row in profile_rows:
        name = row["role"]
        if name is not None and name not in known and name not in out:
            out.append(name)
    return sorted(out)


def alias_remediation(role_name: str) -> Optional[str]:
    """The role ``normalize_role()`` would resolve this alias to, if any.

    Turns "your data is wrong" into "run this UPDATE".
    """
    role = ROLE_ALIASES.get(role_name)
    return role.value if role is not None else None


def unmappable_at_risk(profile_rows: Iterable[Any]) -> list[tuple]:
    """Tenant-less profiles that a membership row can never rescue.

    ``tenant_users.tenant_id`` is NOT NULL, so a profile with no tenant cannot
    have a membership. ``platform_admin`` is fine -- ``require_permission``
    returns before the lookup for it. Any other role with a NULL tenant is
    locked out and no amount of seeding fixes it.
    """
    out = []
    for row in profile_rows:
        without_tenant = int(row["without_tenant"] or 0)
        if without_tenant and row["role"] != UserRole.PLATFORM_ADMIN.value:
            out.append((row["role"], without_tenant))
    return out


def unmappable_profiles(profile_rows: Iterable[Any]) -> list[tuple]:
    """Every tenant-less profile, by role. Reported, not counted as an orphan."""
    return [
        (row["role"], int(row["without_tenant"]))
        for row in profile_rows
        if int(row["without_tenant"] or 0)
    ]


def rows_from_status(status: Optional[str]) -> int:
    """Row count out of an asyncpg command tag (``INSERT 0 15`` -> 15)."""
    if not status:
        return 0
    parts = str(status).split()
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0


def classify_backfill(
    *, inserted: int, eligible: int, roles_before: int
) -> tuple[str, str]:
    """Is ``INSERT 0 0`` a no-op or the bug? Never "success" either way."""
    if inserted > 0:
        return BACKFILL_INSERTED, f"{inserted} membership row(s) written"
    if eligible == 0:
        return (
            BACKFILL_NOOP,
            "every profile that can hold a membership already has one",
        )
    reason = (
        f"{eligible} profile(s) still have no active membership and the backfill "
        "wrote NOTHING. The JOIN on roles.name matched no row for them"
    )
    if roles_before == 0:
        reason += " (the roles table was empty before this run)"
    return BACKFILL_ZERO_ROWS, reason + "."


def predicted_after(
    before: dict,
    *,
    statements: Sequence[Statement],
    eligible_rows: Sequence[Any],
    seeded_names: Iterable[str],
) -> dict:
    """What ``--apply`` would make the five counts. Targets, not guesses.

    permissions/roles/role_permissions are upserts, so the final count is at
    least the size of the definition; tenant_users grows by the profiles that
    both lack a membership and carry a role that will exist.
    """
    known = set(seeded_names)
    would_insert = sum(1 for row in eligible_rows if row["role"] in known)
    sizes = {
        "permissions": sum(1 for s in statements if s.key == "permissions"),
        "roles": sum(1 for s in statements if s.key == "roles"),
        "role_permissions": sum(1 for s in statements if s.key == "role_permissions"),
    }
    return {
        "permissions": max(before["permissions"], sizes["permissions"]),
        "roles": max(before["roles"], sizes["roles"]),
        "role_permissions": max(
            before["role_permissions"], sizes["role_permissions"]
        ),
        "tenant_users": before["tenant_users"] + would_insert,
        "user_profiles": before["user_profiles"],
    }


# ---------------------------------------------------------------------------
# DSN handling (pure)
# ---------------------------------------------------------------------------


def normalize_dsn(raw: str) -> str:
    """Strip a SQLAlchemy driver suffix -- asyncpg wants the bare scheme."""
    scheme, separator, rest = raw.partition("://")
    if separator and "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}{separator}{rest}" if separator else raw


def split_dsn(dsn: Optional[str]) -> tuple:
    """Host and database out of a Postgres URL, without a URL parser choking
    on the characters real passwords contain."""
    if not dsn:
        return "", ""
    remainder = dsn.split("://", 1)[-1].split("?", 1)[0]
    if "@" in remainder:
        remainder = remainder.rsplit("@", 1)[1]
    hostport, _, database = remainder.partition("/")
    host = hostport
    if host.startswith("["):                      # bracketed IPv6
        host = host[1:].split("]", 1)[0]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    return host, database


def describe_target(dsn: Optional[str]) -> str:
    """The line printed before anything happens. Host and database only --
    a password must never reach a terminal, a log or a ticket."""
    host, database = split_dsn(dsn)
    return f"host={host or '(none)'} database={database or '(none)'}"


def dsn() -> Optional[str]:
    """``DATABASE_URL`` first so this can be pointed at a replica; the app
    settings are the fallback for running on the server."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            from app.core.config import get_settings

            raw = get_settings().database_url
        except Exception:  # noqa: BLE001 - absence is handled by the caller
            raw = None
    return normalize_dsn(raw) if raw else None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass
class SeedOutcome:
    applied: bool = False
    committed: bool = False
    exit_code: int = 0
    before: dict = field(default_factory=dict)
    after: Optional[dict] = None
    predicted_after: Optional[dict] = None
    profile_roles: list = field(default_factory=list)
    unmatched_roles: list = field(default_factory=list)
    unmappable: list = field(default_factory=list)
    unmappable_risk: list = field(default_factory=list)
    eligible_before: int = 0
    orphans: list = field(default_factory=list)
    written: dict = field(default_factory=dict)
    backfill_inserted: int = 0
    backfill_status: str = BACKFILL_NOOP
    backfill_reason: str = ""
    refusal: Optional[str] = None

    @property
    def idempotent_noop(self) -> bool:
        """A second run: nothing to write and nothing left behind."""
        return (
            self.backfill_status == BACKFILL_NOOP
            and not self.orphans
            and sum(self.written.values()) == 0
        )


async def _counts(conn: Any) -> dict:
    row = await conn.fetchrow(COUNTS_SQL)
    return {key: int(row[key]) for key in COUNT_KEYS}


async def seed(
    conn: Any,
    *,
    apply: bool = False,
    allow_orphans: bool = False,
    roles: Optional[Iterable[Any]] = None,
) -> SeedOutcome:
    """Read, decide, and (with ``apply``) write everything or nothing."""
    statements = build_statements(roles=roles)
    seeded_names = {spec.name for spec in role_specs(roles)}

    outcome = SeedOutcome(applied=apply)
    outcome.before = await _counts(conn)
    outcome.profile_roles = [dict(row) for row in await conn.fetch(PROFILE_ROLE_SQL)]
    outcome.unmappable = unmappable_profiles(outcome.profile_roles)
    outcome.unmappable_risk = unmappable_at_risk(outcome.profile_roles)

    eligible_rows = [dict(row) for row in await conn.fetch(MISSING_MEMBERSHIP_SQL)]
    outcome.eligible_before = len(eligible_rows)

    # Checked BEFORE the transaction opens: these users would be dropped by
    # the backfill JOIN, and a run that drops users must not write at all.
    outcome.unmatched_roles = unmatched_role_names(
        outcome.profile_roles, seeded_names
    )
    if outcome.unmatched_roles:
        outcome.refusal = "unmatched_roles"
        outcome.exit_code = 2
        return outcome

    if not apply:
        outcome.predicted_after = predicted_after(
            outcome.before,
            statements=statements,
            eligible_rows=eligible_rows,
            seeded_names=seeded_names,
        )
        return outcome

    transaction = conn.transaction()
    await transaction.start()
    try:
        for statement in statements:
            status = await conn.execute(statement.sql, *statement.args)
            written = rows_from_status(status)
            outcome.written[statement.key] = (
                outcome.written.get(statement.key, 0) + written
            )
            if statement.key == "tenant_users":
                outcome.backfill_inserted = written

        outcome.backfill_status, outcome.backfill_reason = classify_backfill(
            inserted=outcome.backfill_inserted,
            eligible=outcome.eligible_before,
            roles_before=outcome.before["roles"],
        )

        # The verification. Same query as the work list, re-read inside the
        # transaction: anything still here is a user who would be locked out
        # by this very commit.
        outcome.orphans = [
            dict(row) for row in await conn.fetch(MISSING_MEMBERSHIP_SQL)
        ]
        outcome.after = await _counts(conn)

        blocked = []
        if outcome.orphans and not allow_orphans:
            blocked.append("orphans")
        if outcome.backfill_status == BACKFILL_ZERO_ROWS:
            blocked.append("zero_row_backfill")

        if blocked:
            outcome.refusal = "+".join(blocked)
            outcome.exit_code = 2
            await transaction.rollback()
            return outcome

        await transaction.commit()
        outcome.committed = True
        return outcome
    except Exception:
        await transaction.rollback()
        raise


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_counts(outcome: SeedOutcome) -> None:
    after = outcome.after if outcome.after is not None else outcome.predicted_after
    heading = "after" if outcome.after is not None else "would be"
    print("\n=== COUNTS ===")
    print(f"  {'table':<18} {'before':>8} {heading:>10}")
    for key in COUNT_KEYS:
        value = after[key] if after else "?"
        print(f"  {key:<18} {outcome.before[key]:>8} {value:>10}")


def report(outcome: SeedOutcome) -> None:
    print("\n=== USER PROFILES BY ROLE ===")
    for row in outcome.profile_roles:
        note = (
            f"  ({row['without_tenant']} with no tenant_id)"
            if row["without_tenant"]
            else ""
        )
        print(f"  {str(row['role']):<18} {row['n']:>5}{note}")

    if outcome.unmatched_roles:
        print("\n=== REFUSED: ROLE NAMES THAT MATCH NO ROLE ===")
        print(
            "  These user_profiles.role values do not equal any roles.name, so\n"
            "  the membership backfill would silently skip every user holding\n"
            "  them -- the exact shape of an 'INSERT 0 0' that reads as success.\n"
            "  Nothing was written."
        )
        for name in outcome.unmatched_roles:
            target = alias_remediation(name)
            if target:
                print(
                    f"  {name!r} is an alias of {target!r}, not a role name. Fix with:\n"
                    f"      UPDATE user_profiles SET role = '{target}' "
                    f"WHERE role = '{name}';"
                )
            else:
                print(
                    f"  {name!r} is not a role or an alias. Decide what it should "
                    "be, or add it to UserRole."
                )
        return

    if outcome.unmappable:
        print("\n=== PROFILES WITH NO TENANT (cannot hold a membership) ===")
        print("  tenant_users.tenant_id is NOT NULL, so these get no row.")
        for role, count in outcome.unmappable:
            print(f"  {role:<18} {count:>5}")
    if outcome.unmappable_risk:
        print(
            "\n  WARNING: the roles below are NOT platform_admin, so they do not\n"
            "  bypass the tenant check in require_permission(). With no tenant_id\n"
            "  they can never hold a membership and will resolve to NO permissions\n"
            "  once the catalogue is authoritative. Give them a tenant_id or make\n"
            "  them platform_admin before running --apply."
        )
        for role, count in outcome.unmappable_risk:
            print(f"    {role:<18} {count:>5}")

    _print_counts(outcome)

    print("\n=== MEMBERSHIP BACKFILL ===")
    print(f"  profiles needing a membership (before) {outcome.eligible_before}")
    if not outcome.applied:
        would = (
            outcome.predicted_after["tenant_users"] - outcome.before["tenant_users"]
            if outcome.predicted_after
            else 0
        )
        print(f"  rows an --apply would insert          {would}")
        if outcome.eligible_before and would == 0:
            print(
                "  *** WARNING: profiles need a membership and the backfill would\n"
                "      insert NOTHING. Do not run --apply until this is understood."
            )
        return

    print(f"  rows inserted                          {outcome.backfill_inserted}")
    print(f"  verdict                                {outcome.backfill_status}")
    print(f"  {outcome.backfill_reason}")

    if outcome.backfill_status == BACKFILL_ZERO_ROWS:
        print(
            "\n  *** INSERT 0 0 IS NOT SUCCESS ***\n"
            "  The transaction was ROLLED BACK. Committing the catalogue without\n"
            "  memberships would make database grants authoritative for every\n"
            "  user while leaving these users with no permissions at all."
        )

    if outcome.orphans:
        print(f"\n=== ORPHANS: {len(outcome.orphans)} profile(s) with no active membership ===")
        for row in outcome.orphans[:50]:
            print(f"  {row['email']}  role={row['role']}  id={row['id']}")
        if len(outcome.orphans) > 50:
            print(f"  ... and {len(outcome.orphans) - 50} more")

    if outcome.committed:
        written = ", ".join(
            f"{key}={outcome.written.get(key, 0)}" for key in
            ("permissions", "roles", "role_permissions", "tenant_users")
        )
        print(f"\nCOMMITTED - rows written: {written}")
        if outcome.idempotent_noop:
            print("Nothing changed: this database was already seeded (no-op).")
        if outcome.orphans:
            print(
                "Committed WITH ORPHANS because --allow-orphans was passed. The "
                "users listed above are now locked out of permission-gated routes."
            )
    else:
        print(
            f"\nROLLED BACK ({outcome.refusal}) - nothing was written.\n"
            "Locking a real user out is worse than not seeding."
        )
        if outcome.orphans:
            print(
                "Re-run with --allow-orphans only once you have decided that "
                "every user listed above may lose access."
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed the RBAC catalogue and backfill tenant_users memberships in "
            "one transaction (dry run by default)."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write. Without this the script only reads and reports.",
    )
    parser.add_argument(
        "--allow-orphans", action="store_true",
        help="Commit even if profiles are left with no active membership. "
             "Those users lose access to every permission-gated route.",
    )
    return parser


async def run(args) -> int:
    database_url = dsn()
    if not database_url:
        print(
            "DATABASE_URL is required. Try: set -a && . ./.env && set +a",
            file=sys.stderr,
        )
        return 2

    print("=== TARGET ===")
    print(f"  {describe_target(database_url)}")
    print(f"  mode: {'APPLY (will write)' if args.apply else 'DRY RUN (reads only)'}")

    import asyncpg

    try:
        conn = await asyncpg.connect(database_url, timeout=15)
    except Exception as exc:  # noqa: BLE001 - one line, not a traceback
        print(
            f"\nCould not connect to {describe_target(database_url)}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        outcome = await seed(
            conn, apply=args.apply, allow_orphans=args.allow_orphans
        )
    finally:
        await conn.close()

    report(outcome)
    if not args.apply and not outcome.unmatched_roles:
        print("\nDRY RUN - nothing written. Re-run with --apply to write.")
    return outcome.exit_code


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
