"""Prove the top-up money invariants against a real PostgreSQL, not a fake.

The unit tests exercise the service's branching against an in-memory double.
What they cannot exercise is the half of the design that lives in the database:
the UNIQUE index that makes a redelivered webhook a no-op, the CHECK
constraints, and the fact that all three writes land in one transaction.

HOW THIS AVOIDS TOUCHING ANYTHING REAL
---------------------------------------
Everything happens in a throwaway schema (``topup_verify_<pid>``) placed first
on the connection's ``search_path``, so every unqualified table name in the
service resolves there. The schema — including a shadow ``tenants`` — is
created at the start and dropped at the end. No production table is read or
written, and no migration is applied to the real schema.

The DDL is not copied here. ``upgrade()`` from the migration is executed
directly against the scratch schema through a shim, so what is proven is the
migration's own statements. A copy would drift.

    python scripts/verify_topup_ledger.py

Exits non-zero on the first failed invariant.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import uuid
from pathlib import Path

import asyncpg

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SCHEMA = f"topup_verify_{os.getpid()}"
TENANT = str(uuid.uuid4())
UNLIMITED_TENANT = str(uuid.uuid4())

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def dsn() -> str:
    """DATABASE_URL first so this can be pointed at a scratch database without
    the app's whole configuration being present."""
    url = os.getenv("DATABASE_URL")
    if not url:
        from app.core.config import get_settings

        url = get_settings().database_url
    if not url:
        raise SystemExit(
            "No database URL. Set DATABASE_URL, or run where the app config "
            "can be loaded."
        )
    return url.replace("+asyncpg", "")


# ── run the migration's own DDL into the scratch schema ─────────────────────

async def apply_migration_ddl(conn) -> None:
    """Execute the real ``upgrade()`` with a shim standing in for alembic.op."""
    path = BACKEND / "Alembic" / "versions" / "0021_billing_topup.py"
    spec = importlib.util.spec_from_file_location("mig0021", path)
    mig = importlib.util.module_from_spec(spec)

    statements: list[str] = []

    class _Op:
        @staticmethod
        def execute(stmt):
            statements.append(str(getattr(stmt, "text", stmt)))

        @staticmethod
        def get_bind():
            raise AssertionError("upgrade() should not need a bind")

    # The module does `from alembic import op` at import time.
    import alembic

    real_op = alembic.op
    alembic.op = _Op
    try:
        spec.loader.exec_module(mig)
        mig.op = _Op
        mig.upgrade()
    finally:
        alembic.op = real_op

    for stmt in statements:
        await conn.execute(stmt)
    print(f"  applied {len(statements)} statements from the migration\n")


async def main() -> int:
    url = dsn()
    print(f"scratch schema: {SCHEMA}\n")

    admin = await asyncpg.connect(url, timeout=10)
    await admin.execute(f'CREATE SCHEMA "{SCHEMA}"')

    async def _init(c):
        await c.execute(f'SET search_path TO "{SCHEMA}", public')

    try:
        pool = await asyncpg.create_pool(url, min_size=2, max_size=4, setup=_init)
        try:
            async with pool.acquire() as conn:
                await apply_migration_ddl(conn)
                # A shadow tenants table so the credit path has something to
                # move without going anywhere near the real one.
                await conn.execute(
                    "CREATE TABLE tenants (id UUID PRIMARY KEY, "
                    " minutes_allocated INTEGER NOT NULL DEFAULT 0)"
                )
                await conn.execute(
                    "INSERT INTO tenants (id, minutes_allocated) VALUES "
                    "($1::uuid, 1000), ($2::uuid, 0)", TENANT, UNLIMITED_TENANT,
                )
                # A shadow `calls` so the REAL quota computation can run
                # against this schema. §9's last acceptance criterion is that
                # a top-up shows up in call quota enforcement, and the only
                # honest way to show that is to run the function the dialler
                # actually gates on.
                await conn.execute(
                    "CREATE TABLE calls ("
                    " id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                    " tenant_id UUID NOT NULL,"
                    " duration_seconds INTEGER NOT NULL DEFAULT 0,"
                    " is_test BOOLEAN NOT NULL DEFAULT FALSE,"
                    " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                )
                # 950 minutes burned against a 1000-minute allocation: nearly
                # out, which is the state a customer tops up from.
                await conn.execute(
                    "INSERT INTO calls (tenant_id, duration_seconds) "
                    "VALUES ($1::uuid, 57000)", TENANT,
                )

            from app.domain.services.topup_service import TopupError, TopupService

            svc = TopupService(pool)

            # ── the catalogue seeded by the migration ──────────────────────
            pkgs = await svc.packages()
            check("the migration seeds a closed catalogue", len(pkgs) == 3,
                  ", ".join(p["code"] for p in pkgs))
            check("bundles get cheaper per minute as they get bigger",
                  all(
                      pkgs[i]["price_cents"] / pkgs[i]["minutes"]
                      > pkgs[i + 1]["price_cents"] / pkgs[i + 1]["minutes"]
                      for i in range(len(pkgs) - 1)
                  ))

            # ── an order snapshots the price ───────────────────────────────
            order = await svc.create_order(
                tenant_id=TENANT, user_id=None, package_code="mins_250")
            check("an order snapshots minutes and price from the package",
                  order["minutes"] == 250 and order["price_cents"] == 2500)

            try:
                await svc.create_order(
                    tenant_id=TENANT, user_id=None, package_code="does_not_exist")
                check("an unknown package is refused", False)
            except TopupError:
                check("an unknown package is refused", True)

            await svc.attach_session(str(order["id"]), "cs_verify_1")

            # ── the quota gate, BEFORE the top-up ──────────────────────────
            from app.domain.services.minutes_quota import compute_minutes_status

            async with pool.acquire() as c:
                q_before = await compute_minutes_status(c, TENANT)
            check("the quota gate sees the tenant nearly out",
                  q_before.remaining_minutes == 50 and not q_before.exhausted,
                  f"remaining={q_before.remaining_minutes}")

            # ── THE INVARIANT: one event, one credit ───────────────────────
            before = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid", TENANT)

            first = await svc.credit_paid_order(
                session_id="cs_verify_1", event_id="evt_verify_1",
                payment_id="pi_verify_1")
            after_one = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid", TENANT)
            check("a paid checkout credits its minutes",
                  first is True and after_one == before + 250,
                  f"{before} -> {after_one}")

            # §9 acceptance: "New balance is reflected in call quota
            # enforcement." Asserted through the function the dialler and the
            # start-campaign endpoint both call, not by re-reading the column.
            async with pool.acquire() as c:
                q_after = await compute_minutes_status(c, TENANT)
            check("the top-up reaches the gate that blocks calls",
                  q_after.remaining_minutes == q_before.remaining_minutes + 250,
                  f"remaining {q_before.remaining_minutes} -> "
                  f"{q_after.remaining_minutes}")

            # Redelivery, exactly as Stripe sends it.
            again = await svc.credit_paid_order(
                session_id="cs_verify_1", event_id="evt_verify_1",
                payment_id="pi_verify_1")
            after_two = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid", TENANT)
            check("a redelivered webhook credits nothing",
                  again is False and after_two == after_one,
                  f"{after_one} -> {after_two}")

            # ── the constraint itself, not the code path around it ─────────
            try:
                await pool.execute(
                    "INSERT INTO billing_ledger (tenant_id, order_id, kind, "
                    " minutes_delta, provider_event_id) "
                    "VALUES ($1::uuid, $2::uuid, 'topup', 250, 'evt_verify_1')",
                    TENANT, str(order["id"]),
                )
                check("the database refuses a duplicate event id", False,
                      "the second insert was accepted")
            except asyncpg.UniqueViolationError:
                check("the database refuses a duplicate event id", True)

            # ── concurrency: ten deliveries at once ────────────────────────
            o2 = await svc.create_order(
                tenant_id=TENANT, user_id=None, package_code="mins_600")
            await svc.attach_session(str(o2["id"]), "cs_verify_2")
            base = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid", TENANT)

            results = await asyncio.gather(*[
                svc.credit_paid_order(
                    session_id="cs_verify_2", event_id="evt_verify_2")
                for _ in range(10)
            ], return_exceptions=True)
            credited = sum(1 for r in results if r is True)
            errors = [r for r in results if isinstance(r, Exception)]
            final = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid", TENANT)
            check("ten simultaneous deliveries credit exactly once",
                  credited == 1 and final == base + 600 and not errors,
                  f"credited={credited} errors={len(errors)} {base} -> {final}")

            # ── the unlimited sentinel ─────────────────────────────────────
            o3 = await svc.create_order(
                tenant_id=UNLIMITED_TENANT, user_id=None, package_code="mins_250")
            await svc.attach_session(str(o3["id"]), "cs_verify_3")
            await svc.credit_paid_order(
                session_id="cs_verify_3", event_id="evt_verify_3")
            unlimited_after = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid",
                UNLIMITED_TENANT)
            ledger_rows = await pool.fetchval(
                "SELECT count(*) FROM billing_ledger WHERE tenant_id = $1::uuid",
                UNLIMITED_TENANT)
            check("an unlimited tenant is not given a cap",
                  unlimited_after == 0, f"minutes_allocated={unlimited_after}")
            check("the money is still recorded for them", ledger_rows == 1)

            # ── reversal ───────────────────────────────────────────────────
            pre_refund = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid", TENANT)
            reversed_ok = await svc.reverse(
                event_id="evt_verify_refund", kind="refund",
                payment_id="pi_verify_1")
            post_refund = await pool.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1::uuid", TENANT)
            neg = await pool.fetchval(
                "SELECT minutes_delta FROM billing_ledger "
                " WHERE provider_event_id = 'evt_verify_refund'")
            original_intact = await pool.fetchval(
                "SELECT minutes_delta FROM billing_ledger "
                " WHERE provider_event_id = 'evt_verify_1'")
            check("a refund takes the minutes back",
                  reversed_ok and post_refund == pre_refund - 250,
                  f"{pre_refund} -> {post_refund}")
            check("the refund is its own negative row", neg == -250)
            check("the original purchase row is untouched", original_intact == 250,
                  "a refund must not rewrite history")

            # ── the CHECK constraints ──────────────────────────────────────
            for label, sql in (
                ("a package cannot sell zero minutes",
                 "INSERT INTO topup_packages (code,name,minutes,price_cents) "
                 "VALUES ('bad','bad',0,100)"),
                ("an order cannot carry a negative price",
                 f"INSERT INTO topup_orders (tenant_id,package_code,minutes,"
                 f"price_cents,currency) VALUES ('{TENANT}'::uuid,'x',1,-1,'GBP')"),
                ("an order cannot hold an invented status",
                 f"INSERT INTO topup_orders (tenant_id,package_code,minutes,"
                 f"price_cents,currency,status) "
                 f"VALUES ('{TENANT}'::uuid,'x',1,1,'GBP','free')"),
                ("the ledger cannot hold an invented kind",
                 f"INSERT INTO billing_ledger (tenant_id,kind,minutes_delta) "
                 f"VALUES ('{TENANT}'::uuid,'gift',1)"),
            ):
                async with pool.acquire() as c:
                    tx = c.transaction()
                    await tx.start()
                    try:
                        await c.execute(sql)
                        check(label, False, "the row was accepted")
                    except (asyncpg.CheckViolationError,
                            asyncpg.IntegrityConstraintViolationError):
                        check(label, True)
                    finally:
                        await tx.rollback()

            # ── the reconciliation view ────────────────────────────────────
            total = await svc.purchased_total(TENANT)
            check("the ledger sums to the net minutes bought", total == 600,
                  f"250 + 600 - 250 = {total}")

        finally:
            await pool.close()
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await admin.close()
        print(f"\nscratch schema {SCHEMA} removed")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + "; ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
