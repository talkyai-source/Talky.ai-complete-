"""Explicit loopback-only OP07 DNC schema verification; creates/removes its own database."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg


async def main():
    backend = Path(__file__).resolve().parents[4] / "backend"
    base = urlparse(os.environ["TEST_DATABASE_URL"])
    if base.hostname not in {"localhost", "127.0.0.1"} or not base.path.endswith("_test"):
        raise ValueError("Explicit localhost *_test source DSN required")
    name = "op07_dnc_bootstrap_" + uuid4().hex + "_test"
    fresh_dsn = urlunparse(base._replace(path="/" + name))
    admin = await asyncpg.connect(urlunparse(base), command_timeout=30)
    try:
        assert not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name)
        await admin.execute(f'CREATE DATABASE "{name}"')
        conn = await asyncpg.connect(fresh_dsn, command_timeout=90)
        try:
            await conn.execute((backend / "database/complete_schema.sql").read_text(encoding="utf-8"))
            assert await conn.fetchval("SELECT to_regclass('uq_dnc_entries_tenant_number_source')")
            print("Fresh complete_schema executed; source-specific DNC index present", flush=True)
        finally:
            await conn.close()
        env = {**os.environ, "DATABASE_URL": fresh_dsn, "TEST_DATABASE_URL": fresh_dsn,
               "PYTHONDONTWRITEBYTECODE": "1"}

        def run(*args):
            result = subprocess.run([sys.executable, "-m", *args], cwd=backend, env=env,
                                    text=True, capture_output=True, timeout=120)
            print(result.stdout, end="", flush=True)
            print(result.stderr, end="", flush=True)
            if result.returncode:
                raise RuntimeError("Fresh database verification subprocess failed")

        run("alembic", "stamp", "0008")
        run("alembic", "upgrade", "head")
        conn = await asyncpg.connect(fresh_dsn)
        try:
            before = await conn.fetchval("SELECT 'uq_dnc_entries_tenant_number_source'::regclass::oid")
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0061_dnc_runtime_contract"
        finally:
            await conn.close()
        run("alembic", "downgrade", "0060_dialer_active_job_owner")
        conn = await asyncpg.connect(fresh_dsn)
        try:
            assert await conn.fetchval("SELECT 'uq_dnc_entries_tenant_number_source'::regclass::oid") == before
        finally:
            await conn.close()
        run("alembic", "upgrade", "head")
        run("pytest", "tests/integration/test_dnc_schema_migration.py", "-q", "--tb=short")
        print("PASS fresh bootstrap -> 0008 -> head; retained DNC keys on downgrade/rerun; 18 PG controls", flush=True)
    finally:
        # Only the freshly generated, exclusively owned database is eligible.
        assert name.startswith("op07_dnc_bootstrap_") and name.endswith("_test")
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
