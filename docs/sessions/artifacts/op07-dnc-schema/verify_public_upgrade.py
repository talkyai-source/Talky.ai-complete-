"""Approved loopback-only 0060→0061 rehearsal; never emits DNC row contents."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

import asyncpg


async def main():
    dsn = os.environ["TEST_DATABASE_URL"]
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.path != "/cp04_acceptance_test":
        raise ValueError("Only the explicitly approved loopback cp04_acceptance_test is allowed")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=15)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0060_dialer_active_job_owner"
        before = {row["id"]: dict(row) for row in await conn.fetch("SELECT * FROM dnc_entries")}
        policies = await conn.fetch("SELECT policyname,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename='dnc_entries' ORDER BY policyname")
        flags = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.dnc_entries'::regclass")
    finally:
        await conn.close()
    backend = Path(__file__).resolve().parents[4] / "backend"
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend, env={**os.environ, "DATABASE_URL": dsn, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=90)
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", flush=True)
    if result.returncode:
        raise RuntimeError("Public disposable migration failed; no bypass permitted")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=15)
    try:
        head = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert head == "0061_dnc_runtime_contract"
        after = {row["id"]: dict(row) for row in await conn.fetch("SELECT * FROM dnc_entries")}
        assert before.keys() == after.keys()
        assert all(all(after[key][column] == value for column, value in row.items()) for key, row in before.items())
        assert await conn.fetch("SELECT policyname,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename='dnc_entries' ORDER BY policyname") == policies
        assert await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.dnc_entries'::regclass") == flags
        assert await conn.fetchval("SELECT to_regclass('uq_dnc_entries_tenant_number_source')")
        assert await conn.fetchval("SELECT to_regclass('uq_dnc_entries_global_number_source')")
        print(f"PASS disposable public head={head}; original_rows={len(before)}; existing values and RLS unchanged; both source keys present", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
