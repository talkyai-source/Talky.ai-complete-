"""Run the unchanged opt-in SQL tests on private copies of canonical tables.

This task-only evidence script never points the tests' TRUNCATE at public.
LIKE copies columns/defaults/checks/indexes, not foreign keys, RLS or triggers.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, urlparse, urlunparse
from uuid import uuid4
import xml.etree.ElementTree as ET

import asyncpg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MODULE = "tests/unit/test_dialer_cooldown_query_real_db.py"


async def retained_snapshot(conn):
    names = await conn.fetch("""SELECT tablename FROM pg_tables
        WHERE schemaname='public' AND (tablename IN ('calls','dialer_jobs')
            OR tablename LIKE '%audit%' OR tablename LIKE '%ledger%')
        ORDER BY tablename""")
    rows = {}
    for item in names:
        name = item["tablename"]
        # Catalog-derived identifier; quote it, never interpolate data values.
        identifier = '"' + name.replace('"', '""') + '"'
        row = await conn.fetchrow(f"""SELECT count(*) n,
            md5(coalesce(string_agg(h, '|' ORDER BY h), '')) digest
            FROM (SELECT md5(row_to_json(t)::text) h FROM public.{identifier} t) s""")
        rows[name] = dict(row)
    return {"head": await conn.fetchval("SELECT version_num FROM public.alembic_version"),
            "rows": rows,
            "relations": [dict(r) for r in await conn.fetch("""SELECT c.relname,
                c.relrowsecurity,c.relforcerowsecurity,c.relacl::text
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname IN ('calls','dialer_jobs')
                ORDER BY c.relname""")]}


async def run(dsn):
    parsed = urlparse(dsn)
    if (parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or not parsed.path.endswith("_test") or parsed.query):
        raise ValueError("Explicit loopback *_test PostgreSQL URL without query required")
    owner = await asyncpg.connect(dsn, timeout=5, command_timeout=15)
    suffix = uuid4().hex
    schema, role = "cooldown_sql_" + suffix, "cooldown_sql_" + suffix
    report = {"source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "test_module": MODULE, "test_module_sha256": hashlib.sha256((ROOT / "backend" / MODULE).read_bytes()).hexdigest(),
              "schema": schema, "role": role, "role_bypassrls": False,
              "scope": "Unmodified eight SQL tests, canonical0061 LIKE INCLUDING ALL shape; not foreign-key, trigger or RLS-policy acceptance."}
    role_created = schema_created = False
    try:
        report["public_before"] = await retained_snapshot(owner)
        assert report["public_before"]["head"] == "0061_dnc_runtime_contract"
        await owner.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        role_created = True
        await owner.execute(f'CREATE SCHEMA "{schema}"')
        schema_created = True
        for table in ("calls", "dialer_jobs"):
            await owner.execute(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)')
        await owner.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        for table in ("calls", "dialer_jobs"):
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await owner.fetchval("SELECT has_table_privilege($1,$2,$3)", role, "public." + table, privilege)
        # The disposable server's trust authentication allows a new local role
        # without any password/credential in the child command or artifacts.
        port = ":" + str(parsed.port) if parsed.port else ""
        private_dsn = urlunparse((parsed.scheme, quote(role) + "@" + parsed.hostname + port,
                                 parsed.path, "", "search_path=" + quote(schema), ""))
        private = await asyncpg.connect(private_dsn, timeout=5, command_timeout=10)
        try:
            report["connection"] = dict(await private.fetchrow("""SELECT current_user role,current_schema() schema,
                'calls'::regclass::oid calls_oid,'dialer_jobs'::regclass::oid jobs_oid"""))
            assert report["connection"]["schema"] == schema
            assert report["connection"]["calls_oid"] != await owner.fetchval("SELECT 'public.calls'::regclass::oid")
            assert report["connection"]["jobs_oid"] != await owner.fetchval("SELECT 'public.dialer_jobs'::regclass::oid")
            report["private_relation_flags"] = [dict(r) for r in await private.fetch("""SELECT c.relname,
                c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND c.relkind='r' ORDER BY c.relname""")]
        finally:
            await private.close()
        env = os.environ.copy()
        env["TALKY_DIALER_COOLDOWN_TEST_DATABASE_URL"] = private_dsn
        env["ENVIRONMENT"] = "test"
        command = [sys.executable, "-m", "pytest", MODULE, "-q", "--tb=short", "--junitxml=" + str(HERE / "results.xml")]
        report["command"] = command
        report["working_directory"] = str(ROOT / "backend")
        report["environment"] = {"ENVIRONMENT": "test", "PYTHONPATH": env.get("PYTHONPATH"),
                                 "TALKY_DIALER_COOLDOWN_TEST_DATABASE_URL": "restricted local role; search_path=" + schema}
        with (HERE / "tests.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*command, cwd=ROOT / "backend", env=env,
                                                           stdout=log, stderr=asyncio.subprocess.STDOUT)
            report["pytest_exit_code"] = await asyncio.wait_for(process.wait(), 120)
        suites = ET.parse(HERE / "results.xml").getroot().findall("testsuite")
        report["results"] = {key: sum(int(s.get(key, 0)) for s in suites)
                             for key in ("tests", "failures", "errors", "skipped")}
        assert report["pytest_exit_code"] == 0 and report["results"] == {
            "tests": 8, "failures": 0, "errors": 0, "skipped": 0}
    finally:
        if schema_created:
            actual_owner = await owner.fetchval("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=$1", schema)
            assert actual_owner == await owner.fetchval("SELECT current_user")
            await owner.execute(f'DROP SCHEMA "{schema}" CASCADE')
        if role_created:
            await owner.execute(f'DROP ROLE "{role}"')
        report["public_after"] = await retained_snapshot(owner)
        report["public_unchanged"] = report.get("public_before") == report["public_after"]
        report["owned_schema_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)", schema)
        report["owned_role_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        (HERE / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        await owner.close()
    assert report["public_unchanged"]
    print(json.dumps({k: report[k] for k in ("source_revision", "results", "public_unchanged", "owned_schema_removed", "owned_role_removed")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    asyncio.run(run(parser.parse_args().dsn))
