"""Count F2-04 violations without changing rows, constraints or indexes.

The owner supplies a privileged read-only connection through a named process
environment variable. No dotenv file is loaded; no DSN or row values are logged.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import UUID

import asyncpg


_REVISION = Path(__file__).resolve().parents[1] / "Alembic/versions/0088_qa_data_constraints.py"
_spec = importlib.util.spec_from_file_location("qa_constraint_contract_0088", _REVISION)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tenant-id", type=UUID)
    scope.add_argument("--all-tenants", action="store_true")
    parser.add_argument("--dsn-env", default="MIGRATION_DATABASE_URL", help="Process environment variable containing the owner DSN")
    return parser.parse_args(argv)


async def collect_report(conn, *, tenant_id=None):
    if tenant_id is not None:
        tenant_id = UUID(str(tenant_id))
    rows = [{"table": table, "column": column, "constraint": name, "violations": 0}
            for table, column, name, _ in _contract.CHECKS]
    tenant_count = 0
    async with conn.transaction(readonly=True, isolation="repeatable_read"):
        await conn.execute("SET LOCAL statement_timeout='60s'")
        await conn.execute("SET LOCAL lock_timeout='5s'")
        # This does not grant BYPASSRLS; PostgreSQL refuses a filtered count.
        await conn.execute("SET LOCAL row_security=off")
        for item, (table, _, _, expression) in zip(rows, _contract.CHECKS):
            if table == "plans":
                item["violations"] = await conn.fetchval(
                    f'SELECT count(*) FROM public."{table}" WHERE ({expression}) IS FALSE')

        async def count_tenant(current):
            for item, (table, _, _, expression) in zip(rows, _contract.CHECKS):
                if table == "plans":
                    continue
                scope_column = "id" if table == "tenants" else "tenant_id"
                item["violations"] += await conn.fetchval(
                    f'SELECT count(*) FROM public."{table}" WHERE "{scope_column}" = $1::uuid '
                    f'AND ({expression}) IS FALSE', current)

        if tenant_id is not None:
            await count_tenant(tenant_id)
            tenant_count = 1
        else:
            after = None
            while True:
                # Fixed platform directory read; all customer counts stay scoped.
                page = await conn.fetch(
                    "SELECT id FROM public.tenants WHERE ($1::uuid IS NULL OR id > $1::uuid) ORDER BY id LIMIT $2", after, 500)
                if not page:
                    break
                for tenant in page:
                    await count_tenant(tenant["id"])
                    tenant_count += 1
                after = page[-1]["id"]
        missing = await conn.fetch(_contract.FK_INDEX_GAPS_SQL)
        gaps = [{"table": row["table_name"], "constraint": row["constraint_name"], "columns": list(row["column_names"])} for row in missing]
    return {"mode": "dry_run", "tenant_count": tenant_count, "constraints": rows, "missing_fk_indexes": gaps}


async def run(argv=None):
    args = parse_args(argv)
    conn = None
    try:
        dsn = os.environ.get(args.dsn_env, "").strip()
        if not dsn:
            raise RuntimeError("owner database configuration missing")
        conn = await asyncpg.connect(dsn, timeout=5, command_timeout=60)
        report = await collect_report(conn, tenant_id=args.tenant_id)
        # Do not publish success before the connection is safely closed.
        await conn.close(timeout=5)
        conn = None
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Data-constraint dry-run failed ({type(exc).__name__}); no result published.", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            try:
                await conn.close(timeout=5)
            except Exception:
                try:
                    conn.terminate()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
