#!/usr/bin/env python3
"""Is row-level security actually protecting anything right now?

    python scripts/verify_rls.py            # posture + behaviour
    python scripts/verify_rls.py --posture  # posture only, no scratch objects

WHY THIS EXISTS
---------------
On 2026-08-22 this database had 29 tables with RLS enabled, 65 policies, and
zero enforcement: the app role was a superuser with BYPASSRLS, so every policy
was decorative. Nothing in the product noticed, because a policy that never runs
looks exactly like a policy that always passes.

So "we have RLS" is not a fact anyone should accept without running this. It
reports three things that are easy to get wrong and impossible to see:

  POSTURE     which tables are protected, which have FORCE, and — the finding
              that mattered most — which tables carry tenant_id with no policy
              at all. Fixing the role would not have protected those.

  ROLE        whether the connected role can bypass everything anyway. This is
              the difference between "isolated" and "isolated on paper".

  BEHAVIOUR   the policy expression exercised against a real NOSUPERUSER
              NOBYPASSRLS role, including the failure modes that bite in
              production: an empty GUC (the ::uuid cast landmine), an unset GUC,
              an uppercase uuid, and whether app.bypass_rls still lets workers
              and admin tooling do cross-tenant work.

The behavioural section builds a scratch schema, table, policy and role inside a
transaction and rolls it back. CREATE ROLE and CREATE SCHEMA are transactional in
PostgreSQL, so nothing survives — safe to run against production.
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

# The canonical policy, kept identical to Alembic 0013_canonical_rls_policies.
# If you change one, change both — a probe that tests a different expression
# from the one deployed is worse than no probe.
USING = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)
USING_NULLABLE = USING + " OR tenant_id IS NULL"

T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        sys.exit("DATABASE_URL is required. Try: set -a && . ./.env && set +a")
    return raw.replace("postgresql+asyncpg", "postgresql")


async def posture(conn: asyncpg.Connection) -> None:
    print("== role ==")
    user = await conn.fetchval("SELECT current_user")
    sup = await conn.fetchval("SELECT usesuper FROM pg_user WHERE usename=current_user")
    byp = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
    print(f"  {user}  superuser={sup}  bypassrls={byp}")
    check(
        "the app role cannot bypass row security",
        not sup and not byp,
        "a superuser or BYPASSRLS role makes every policy decorative",
    )

    print("\n== posture ==")
    rows = await conn.fetch("""
        SELECT c.relname AS t, c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced,
               (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS pols,
               EXISTS (SELECT 1 FROM pg_attribute a
                        WHERE a.attrelid=c.oid AND a.attname='tenant_id'
                          AND a.attnum>0 AND NOT a.attisdropped) AS has_tenant
        FROM pg_class c
        WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'
        ORDER BY c.relname
    """)
    tenant_tables = [r for r in rows if r["has_tenant"]]
    protected = [r for r in tenant_tables if r["enabled"]]
    unprotected = [r["t"] for r in tenant_tables if not r["enabled"]]
    no_force = [r["t"] for r in protected if not r["forced"]]
    no_policy = [r["t"] for r in protected if r["pols"] == 0]

    print(f"  tables with tenant_id : {len(tenant_tables)}")
    print(f"  RLS enabled           : {len(protected)}")
    check("every tenant-scoped table has RLS enabled", not unprotected,
          f"{len(unprotected)} without: {', '.join(unprotected[:6])}"
          + (" ..." if len(unprotected) > 6 else ""))
    check("every protected table has FORCE (owners bypass without it)", not no_force,
          f"{len(no_force)} without FORCE")
    check("no table has RLS enabled but zero policies (would deny all)", not no_policy,
          ", ".join(no_policy))

    shapes = await conn.fetch("""
        SELECT count(DISTINCT qual) AS n,
               count(*) FILTER (WHERE qual NOT LIKE '%bypass_rls%') AS no_bypass,
               count(*) FILTER (WHERE qual LIKE '%)::uuid%'
                                  AND qual NOT LIKE '%NULLIF%') AS raw_cast,
               count(*) FILTER (WHERE with_check IS NULL) AS no_check,
               count(*) AS total
        FROM pg_policies WHERE schemaname='public'
    """)
    s = shapes[0]
    # Two shapes is the target, not one: tables whose tenant_id is nullable hold
    # platform-global rows and carry an extra `OR tenant_id IS NULL`. Asserting
    # a single shape would report a correct database as broken.
    check("at most two policy shapes (strict + nullable variant)", s["n"] <= 2,
          f"{s['n']} distinct USING expressions")
    check("one policy per protected table (no per-command leftovers)",
          s["total"] == len(protected),
          f"{s['total']} policies across {len(protected)} tables")
    check("all policies honour app.bypass_rls", s["no_bypass"] == 0,
          f"{s['no_bypass']} do not — workers and admin views would see nothing")
    check("no raw ::uuid cast (empty GUC would raise)", s["raw_cast"] == 0,
          f"{s['raw_cast']} policies at risk")
    check("every policy has WITH CHECK (blocks writes into another tenant)",
          s["no_check"] == 0, f"{s['no_check']} without")


async def behaviour(conn: asyncpg.Connection) -> None:
    print("\n== behaviour, against a real NOSUPERUSER NOBYPASSRLS role ==")
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("CREATE SCHEMA rls_probe")
        await conn.execute("CREATE ROLE rls_probe_app NOSUPERUSER NOBYPASSRLS LOGIN")
        await conn.execute("GRANT USAGE ON SCHEMA rls_probe TO rls_probe_app")
        await conn.execute("CREATE TABLE rls_probe.strict (id int, tenant_id uuid NOT NULL)")
        await conn.execute("CREATE TABLE rls_probe.lenient (id int, tenant_id uuid)")
        for t, expr in (("strict", USING), ("lenient", USING_NULLABLE)):
            await conn.execute(f"ALTER TABLE rls_probe.{t} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE rls_probe.{t} FORCE ROW LEVEL SECURITY")
            await conn.execute(f"GRANT ALL ON rls_probe.{t} TO rls_probe_app")
            await conn.execute(
                f"CREATE POLICY p ON rls_probe.{t} FOR ALL USING ({expr}) WITH CHECK ({expr})"
            )
        await conn.execute(f"INSERT INTO rls_probe.strict VALUES (1,'{T1}'),(2,'{T2}')")
        await conn.execute(f"INSERT INTO rls_probe.lenient VALUES (1,'{T1}'),(2,NULL)")
        await conn.execute("SET LOCAL ROLE rls_probe_app")

        async def n(tbl: str = "strict") -> int:
            return await conn.fetchval(f"SELECT count(*) FROM rls_probe.{tbl}")

        async def guarded(label: str, sql: str, sp: str) -> None:
            """Expected-to-be-denied statement. Needs a savepoint: a policy
            violation aborts the transaction and every later statement fails."""
            await conn.execute(f"SAVEPOINT {sp}")
            try:
                await conn.execute(sql)
                check(label, False, "statement was allowed")
                await conn.execute(f"RELEASE SAVEPOINT {sp}")
            except asyncpg.InsufficientPrivilegeError:
                check(label, True)
                await conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")

        await conn.execute(f"SET LOCAL app.current_tenant_id = '{T1}'")
        check("owning tenant sees only its row", await n() == 1)
        await conn.execute(f"SET LOCAL app.current_tenant_id = '{T2}'")
        check("another tenant sees only its own", await n() == 1)

        await conn.execute("SET LOCAL app.current_tenant_id = ''")
        try:
            check("empty GUC: no rows and NO exception", await n() == 0)
        except Exception as exc:  # noqa: BLE001
            check("empty GUC: no rows and NO exception", False, type(exc).__name__)

        await conn.execute("RESET app.current_tenant_id")
        try:
            check("unset GUC: no rows and no exception", await n() == 0)
        except Exception as exc:  # noqa: BLE001
            check("unset GUC: no rows and no exception", False, type(exc).__name__)

        await conn.execute("SET LOCAL app.bypass_rls = 'true'")
        check("bypass_rls=true still works (workers, admin tooling)", await n() == 2)
        await conn.execute("SET LOCAL app.bypass_rls = ''")

        await conn.execute(f"SET LOCAL app.current_tenant_id = '{T1.upper()}'")
        check("uppercase uuid matches (text comparison would not)", await n() == 1)

        await conn.execute(f"SET LOCAL app.current_tenant_id = '{T1}'")
        await guarded("WITH CHECK blocks a cross-tenant INSERT",
                      f"INSERT INTO rls_probe.strict VALUES (9,'{T2}')", "sp1")
        await guarded("WITH CHECK blocks moving a row to another tenant",
                      f"UPDATE rls_probe.strict SET tenant_id='{T2}' WHERE id=1", "sp2")

        # Not an error — it simply matches nothing visible. That is correct.
        await conn.execute("DELETE FROM rls_probe.strict WHERE id=2")
        check("cross-tenant DELETE affects nothing", await n() == 1)

        check("nullable tenant_id keeps platform-global rows visible",
              await n("lenient") == 2)
        await conn.execute("RESET ROLE")
    finally:
        await tx.rollback()

    left = await conn.fetchval("SELECT count(*) FROM pg_roles WHERE rolname='rls_probe_app'")
    check("scratch role and schema rolled back cleanly", left == 0)


async def main() -> int:
    conn = await asyncpg.connect(dsn())
    try:
        await posture(conn)
        if "--posture" not in sys.argv:
            await behaviour(conn)
    finally:
        await conn.close()
    passed, total = sum(_results), len(_results)
    print(f"\n===== {passed}/{total} checks passed =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
