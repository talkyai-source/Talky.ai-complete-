"""widen the user_profiles.role CHECK to every UserRole

Revision ID: 0035_user_profiles_role_widen
Revises: 0034_inbound_billing_four_eye
Create Date: 2026-08-29 00:00:00.000000

``UserRole`` gained CAMPAIGN_MANAGER, AGENT and BILLING_USER (plus the
``operator`` alias for AGENT).  The database still restricted
``user_profiles.role`` to the five pre-existing names, so INSERTing a user
with any of the three new roles raised a check violation and goals.md §12's
six-role, 200-tenant validation seeding could not run at all.

The old constraint has two different names in the wild:

* ``chk_user_profiles_role_valid`` — the name given by the archived
  ``database/migrations/_archive/day4_rbac_tenant_isolation.sql`` and carried
  by the 2026-06-02 production dump.
* ``user_profiles_role_check`` — the name PostgreSQL auto-generated for the
  inline ``CHECK (role IN (...))`` in earlier
  ``database/complete_schema.sql`` revisions and therefore still possible in
  older CI/bootstrap databases. The maintained schema now uses the canonical
  name above.

Guessing either one would break the other, so this revision discovers the
constraint from ``pg_constraint``. It proceeds only when exactly one validated
single-column role CHECK has one of those two names and its parsed PostgreSQL
expression exactly matches the known legacy or widened role set. Missing,
duplicate, unvalidated, unknown-name, and unknown-definition constraints fail
closed before anything is dropped. The replacement uses the canonical
``chk_user_profiles_role_valid`` name.

Migrations must not import application code, so the allowed list is spelled
out here; ``tests/unit/test_user_profiles_role_widen_migration.py`` compares
it against ``UserRole`` so the two cannot drift.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0035_user_profiles_role_widen"
down_revision: str | None = "0034_inbound_billing_four_eye"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "chk_user_profiles_role_valid"
_AUTO_CONSTRAINT = "user_profiles_role_check"
_KNOWN_CONSTRAINT_NAMES = frozenset({_CONSTRAINT, _AUTO_CONSTRAINT})
_PROBE_CONSTRAINT = "__talky_0035_role_check_probe"

# Must equal [role.value for role in UserRole], in declaration order.
_ALLOWED_ROLES: tuple[str, ...] = (
    "platform_admin",
    "partner_admin",
    "tenant_admin",
    "campaign_manager",
    "user",
    "agent",
    "billing_user",
    "readonly",
)

# The five names the CHECK allowed before this revision.
_LEGACY_ROLES: tuple[str, ...] = (
    "platform_admin",
    "partner_admin",
    "tenant_admin",
    "user",
    "readonly",
)

# Roles this revision introduces to the database; downgrade refuses while any
# row still uses one of them.
_NEW_ROLES: tuple[str, ...] = tuple(role for role in _ALLOWED_ROLES if role not in _LEGACY_ROLES)


def _sql_list(roles: Sequence[str]) -> str:
    return ", ".join(f"'{role}'" for role in roles)


def _role_check_constraints() -> list[dict[str, object]]:
    """Return every single-column CHECK constraint on ``public.user_profiles.role``.

    Discovered from the catalog rather than assumed: a database bootstrapped
    from an older ``complete_schema.sql`` may name it
    ``user_profiles_role_check`` while the maintained bootstrap and preserved
    production dump name it ``chk_user_profiles_role_valid``.

    ``pg_get_expr`` removes parser source-location metadata while preserving
    PostgreSQL's normalized expression. Comparing that deparse with probes
    created on the same table avoids accepting a merely similar expression
    without making raw ``pg_node_tree`` location offsets part of equality.
    """

    return [
        dict(row)
        for row in op.get_bind()
        .execute(
            text(
                """
                SELECT c.conname,
                       c.convalidated,
                       pg_get_expr(c.conbin, c.conrelid, true) AS expression
                FROM pg_constraint AS c
                JOIN pg_attribute AS a
                  ON a.attrelid = c.conrelid
                 AND a.attnum = ANY (c.conkey)
                WHERE c.conrelid = 'public.user_profiles'::regclass
                  AND c.contype = 'c'
                  AND a.attname = 'role'
                  AND array_length(c.conkey, 1) = 1
                ORDER BY c.conname
                """
            )
        )
        .mappings()
    ]


def _probe_role_check_expression(
    roles: Sequence[str],
    *,
    restored_dump_shape: bool = False,
) -> str:
    """Return the parsed tree for one exact target role CHECK.

    The probe is transactional, never validated, and always dropped before
    returning. The caller already holds an ACCESS EXCLUSIVE table lock, so no
    concurrent DDL can race the catalog comparison.
    """

    if restored_dump_shape:
        # The preserved production pg_dump deparses the original IN expression
        # as an ANY(array) whose varchar literals are individually relabelled
        # to text. Recreate that exact known parse shape as a second probe so a
        # restored historical database is accepted without accepting arbitrary
        # definitions.
        elements = ", ".join(
            f"('{role}'::character varying)::text" for role in roles
        )
        expression = f"(role)::text = ANY (ARRAY[{elements}])"
    else:
        expression = f"role IN ({_sql_list(roles)})"

    op.execute(
        text(
            f'ALTER TABLE public.user_profiles ADD CONSTRAINT "{_PROBE_CONSTRAINT}" '
            f"CHECK ({expression}) NOT VALID"
        )
    )
    try:
        rows = list(
            op.get_bind()
            .execute(
                text(
                    "SELECT c.convalidated, "
                    "pg_get_expr(c.conbin, c.conrelid, true) AS expression "
                    "FROM pg_constraint AS c "
                    "WHERE c.conrelid = 'public.user_profiles'::regclass "
                    "AND c.contype = 'c' "
                    f"AND c.conname = '{_PROBE_CONSTRAINT}'"
                )
            )
            .mappings()
        )
        if len(rows) != 1 or bool(rows[0]["convalidated"]):
            raise RuntimeError(
                "0035 could not create exactly one unvalidated role CHECK probe"
            )
        return str(rows[0]["expression"])
    finally:
        op.execute(
            text(
                f'ALTER TABLE public.user_profiles DROP CONSTRAINT "{_PROBE_CONSTRAINT}"'
            )
        )


def _known_role_check_expressions(roles: Sequence[str]) -> set[str]:
    return {
        _probe_role_check_expression(roles),
        _probe_role_check_expression(roles, restored_dump_shape=True),
    }


def _require_known_role_check() -> str:
    constraints = _role_check_constraints()
    if len(constraints) != 1:
        raise RuntimeError(
            "Refusing 0035 role CHECK replacement: expected exactly one "
            f"single-column public.user_profiles.role CHECK; found {constraints}"
        )

    constraint = constraints[0]
    name = str(constraint["conname"])
    if name not in _KNOWN_CONSTRAINT_NAMES:
        raise RuntimeError(
            "Refusing 0035 role CHECK replacement: unknown constraint name "
            f"{name!r}; expected one of {sorted(_KNOWN_CONSTRAINT_NAMES)}"
        )
    if not bool(constraint["convalidated"]):
        raise RuntimeError(
            "Refusing 0035 role CHECK replacement: existing constraint "
            f"{name!r} is not validated"
        )

    candidate = str(constraint["expression"])
    legacy = _known_role_check_expressions(_LEGACY_ROLES)
    allowed = _known_role_check_expressions(_ALLOWED_ROLES)
    if candidate not in legacy | allowed:
        raise RuntimeError(
            "Refusing 0035 role CHECK replacement: existing constraint "
            f"{name!r} has an unknown parsed definition"
        )
    return name


def _reject_rows_outside(roles: Sequence[str], *, direction: str) -> None:
    offenders = list(
        op.get_bind()
        .execute(
            text(
                f"""
                SELECT role, count(*) AS rows
                FROM public.user_profiles
                WHERE role IS NULL OR role NOT IN ({_sql_list(roles)})
                GROUP BY role
                ORDER BY role
                """
            )
        )
        .mappings()
    )
    if offenders:
        detail = ", ".join(f"{row['role']!r}={row['rows']}" for row in offenders)
        raise RuntimeError(
            f"Refusing to {direction} 0035: user_profiles rows hold role "
            f"value(s) the target CHECK would reject ({detail}). Reassign "
            "those users before running this migration."
        )


def _install_role_check(roles: Sequence[str]) -> None:
    existing = _require_known_role_check()
    op.execute(
        text(f'ALTER TABLE public.user_profiles DROP CONSTRAINT "{existing}"')
    )
    op.execute(
        text(
            f"ALTER TABLE public.user_profiles ADD CONSTRAINT {_CONSTRAINT} "
            f"CHECK (role IN ({_sql_list(roles)})) NOT VALID"
        )
    )
    op.execute(
        text(
            f"ALTER TABLE public.user_profiles VALIDATE CONSTRAINT {_CONSTRAINT}"
        )
    )


def _validate_role_check(roles: Sequence[str]) -> None:
    constraints = _role_check_constraints()
    if len(constraints) != 1 or constraints[0]["conname"] != _CONSTRAINT:
        raise RuntimeError(
            "0035 failed to leave exactly one user_profiles.role CHECK "
            f"constraint named {_CONSTRAINT}; found {constraints}"
        )
    constraint = constraints[0]
    if not bool(constraint["convalidated"]):
        raise RuntimeError("0035 installed an unvalidated user_profiles.role CHECK")
    expected = _probe_role_check_expression(roles)
    if str(constraint["expression"]) != expected:
        raise RuntimeError(
            "0035 installed a user_profiles.role CHECK whose parsed definition "
            "does not exactly match the target role set"
        )


def upgrade() -> None:
    op.execute(text("LOCK TABLE public.user_profiles IN ACCESS EXCLUSIVE MODE"))
    # The widened list is a strict superset of the legacy one, so this can only
    # trip on a database that never carried the constraint and already holds an
    # unknown role name — which normalize_role() would silently downgrade to
    # readonly.  Fail loudly instead of leaving the constraint un-validated.
    _reject_rows_outside(_ALLOWED_ROLES, direction="upgrade")
    _install_role_check(_ALLOWED_ROLES)
    _validate_role_check(_ALLOWED_ROLES)


def downgrade() -> None:
    op.execute(text("LOCK TABLE public.user_profiles IN ACCESS EXCLUSIVE MODE"))
    retained = int(
        op.get_bind()
        .execute(
            text(
                "SELECT count(*) FROM public.user_profiles "
                f"WHERE role IN ({_sql_list(_NEW_ROLES)})"
            )
        )
        .scalar()
        or 0
    )
    if retained:
        raise RuntimeError(
            f"Refusing to downgrade 0035: {retained} user_profiles row(s) hold "
            f"one of {list(_NEW_ROLES)}. Narrowing the CHECK would either fail "
            "or require silently rewriting those principals' roles; reassign "
            "them explicitly first."
        )
    _reject_rows_outside(_LEGACY_ROLES, direction="downgrade")
    # Restored under the canonical name even where the pre-0035 constraint was
    # PostgreSQL's auto-generated user_profiles_role_check: re-running upgrade()
    # discovers the constraint by column, never by name, so either name works.
    _install_role_check(_LEGACY_ROLES)
    _validate_role_check(_LEGACY_ROLES)
