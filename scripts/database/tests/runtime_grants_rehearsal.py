#!/usr/bin/env python3
"""Real PostgreSQL16/TLS runtime-grants acceptance on unique disposable fixtures.

No existing endpoint option is accepted. Reuses the canonical recovery fixture's
local-Docker boundary, migration bootstrap and exact owned-resource cleanup.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import traceback
from unittest.mock import patch


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


HERE = Path(__file__).resolve().parent
fixture_module = module("grants_tls_fixture", HERE / "tls_canonical_rehearsal.py")
grants = module("tested_runtime_grants", HERE.parent / "runtime_grants.py")
ROOT = fixture_module.ROOT
recovery = fixture_module.recovery
require = fixture_module.require


def reject(operation, fragment):
    try:
        operation()
    except grants.GrantsError as error:
        require(fragment in str(error), "Rejected for an unexpected grants boundary")
    else:
        raise RuntimeError("Unsafe profile was accepted")


def sql(service, statement, parameters=()):
    with grants.connect(service) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            result = cursor.fetchall() if cursor.description else None
        connection.commit()
    return result


def checks(fixture, pg, receipt):
    owner, runtime = fixture.service("source_migration"), fixture.service("source_runtime")
    admin = fixture.service("source_admin")
    results = receipt["checks"]
    receipt["stage"] = "initial grants plan/apply/verify"
    plan = grants.execute(owner, runtime, "plan")
    require(plan["status"] == "plan_ready" and not plan["grants_committed"], "Plan performed a mutation")
    reject(lambda: grants.execute(owner, runtime, "verify"), "ACL differs")
    result = grants.execute(owner, runtime, "apply")
    require(result["runtime_login_verified"] and result["grants_committed"], "Actual restricted runtime login was not verified")
    require(grants.execute(owner, runtime, "apply")["policies_sha256"] == result["policies_sha256"],
            "Reapplying grants changed tenant policies")
    require(grants.execute(owner, runtime, "verify")["status"] == "verified", "Independent verify failed")
    results.append("plan, apply, repeat apply and verify with real separate restricted TLS logins; RLS unchanged")
    receipt["stage"] = "initial administrator acceptance"

    # Installer acceptance supplies its own real auth checks against this fresh
    # schema, using the same protected service objects and no retained databases.
    bootstrap_path = HERE / "bootstrap_admin_acceptance.py"
    if bootstrap_path.is_file():
        results += module("grants_bootstrap_admin", bootstrap_path).run_checks(owner, runtime)
    else:
        raise RuntimeError("Initial-admin acceptance helper is missing")

    # Reuse populated tenant fixture setup, then replace its deliberately broad
    # recovery-only grants with the actual supported profile again.
    receipt["stage"] = "populated tenant and system runtime operations"
    fixture_module.seed(pg, fixture)
    grants.execute(owner, runtime, "apply")
    fixture_module.runtime_checks(fixture, "source", grants.PROFILE["heads"])
    results.append("real populated campaign/lead/call tenant CRUD, foreign-tenant refusal and blank-scope isolation")
    results += module("grants_support_contracts", HERE / "support_contracts_acceptance.py").run_checks(runtime, fixture_module.TENANTS)
    import psycopg2
    with grants.connect(runtime) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL app.bypass_rls='on'; SET LOCAL app.current_tenant_id='00000000-0000-0000-0000-000000000000'")
            cursor.execute("SELECT count(*) FROM public.campaigns")
            require(cursor.fetchone()[0] == 2, "Trusted system context cannot read both seeded tenants")
            for statement in (
                "CREATE TABLE public.runtime_must_not_create(id integer)",
                "CREATE TEMP TABLE runtime_must_not_create(id integer)",
                "UPDATE public.alembic_version SET version_num=version_num",
                "DELETE FROM public.ai_config_migrations",
                "TRUNCATE public.campaigns",
                "SET ROLE fixture_migration",
                "SELECT public.prune_tenant_policy_audit_log(0)",
                "SELECT setval('public.billing_ledger_id_seq',1)",
            ):
                cursor.execute("SAVEPOINT refused_operation")
                try:
                    cursor.execute(statement)
                except psycopg2.errors.InsufficientPrivilege:
                    cursor.execute("ROLLBACK TO SAVEPOINT refused_operation")
                else:
                    raise RuntimeError("Restricted runtime executed a forbidden operation")
                cursor.execute("RELEASE SAVEPOINT refused_operation")
            # Usage of a real application sequence is permitted; rollback does
            # not undo nextval, so this runs only inside the disposable fixture.
            cursor.execute("SELECT nextval('public.billing_ledger_id_seq')")
            require(cursor.fetchone()[0] > 0, "Application sequence usage failed")
        connection.rollback()
    results.append("trusted system scope works; DDL, TEMP, migration writes, TRUNCATE, SET ROLE, maintenance EXECUTE and setval refused")

    receipt["stage"] = "future object default privileges"
    sql(owner, "CREATE TABLE public.future_grants_probe(id integer); CREATE SEQUENCE public.future_grants_probe_seq; "
               "CREATE FUNCTION public.future_grants_probe_fn() RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'")
    try:
        observed = sql(runtime, "SELECT has_table_privilege(current_user,'public.future_grants_probe','SELECT'), "
                       "has_sequence_privilege(current_user,'public.future_grants_probe_seq','USAGE'), "
                       "has_function_privilege(current_user,'public.future_grants_probe_fn()','EXECUTE')")
        require(observed == [(False, False, False)], "Future objects received automatic runtime grants")
        reject(lambda: grants.execute(owner, runtime, "apply"), "inventory differs")
    finally:
        sql(owner, "DROP TABLE public.future_grants_probe; DROP SEQUENCE public.future_grants_probe_seq; DROP FUNCTION public.future_grants_probe_fn()")
    results.append("new tables, sequences and SECURITY DEFINER functions default closed; unknown inventory is refused")

    receipt["stage"] = "unsafe role refusals"
    # Each negative mutation is limited to these generated fixture objects and
    # restored before the next case. Failed apply must not repair unsafe roles.
    for change, undo, message in (
        ("ALTER ROLE fixture_runtime BYPASSRLS", "ALTER ROLE fixture_runtime NOBYPASSRLS", "elevated role flags"),
        ("ALTER ROLE fixture_runtime NOLOGIN", "ALTER ROLE fixture_runtime LOGIN", "LOGIN roles"),
        ("GRANT fixture_migration TO fixture_runtime", "REVOKE fixture_migration FROM fixture_runtime", "role memberships"),
    ):
        pg.sql(admin, change, readonly=False)
        try:
            reject(lambda: grants.execute(owner, runtime, "apply"), message)
        finally:
            pg.sql(admin, undo, readonly=False)
    results.append("BYPASSRLS, NOLOGIN and migration-role membership refused before grants")

    receipt["stage"] = "mixed table ownership refusal"
    # Table ownership change requires fixture superuser, never the grants tool.
    pg.sql(admin, "ALTER TABLE public.plans OWNER TO fixture_admin", database=fixture_module.SOURCE_DB, readonly=False)
    try:
        reject(lambda: grants.execute(owner, runtime, "apply"), "Mixed object ownership")
    finally:
        pg.sql(admin, "ALTER TABLE public.plans OWNER TO fixture_migration", database=fixture_module.SOURCE_DB, readonly=False)
    receipt["stage"] = "application SECURITY DEFINER and overload refusal"
    sql(owner, "ALTER FUNCTION public.update_call_status(uuid,text,integer) SECURITY DEFINER")
    try:
        reject(lambda: grants.execute(owner, runtime, "apply"), "SECURITY DEFINER")
    finally:
        sql(owner, "ALTER FUNCTION public.update_call_status(uuid,text,integer) SECURITY INVOKER")
    sql(owner, "CREATE FUNCTION public.update_call_status(text) RETURNS integer LANGUAGE sql AS 'SELECT 1'")
    try:
        reject(lambda: grants.execute(owner, runtime, "apply"), "Unknown application function")
    finally:
        sql(owner, "DROP FUNCTION public.update_call_status(text)")
    results.append("mixed ownership, SECURITY DEFINER conversion and unexpected overload refused")
    receipt["stage"] = "extension SECURITY DEFINER refusal"
    # A trusted extension's membership alone cannot excuse a privileged
    # function. Only fixture admin can change the extension member in this test.
    pg.sql(admin, "ALTER FUNCTION public.uuid_generate_v4() SECURITY DEFINER", database=fixture_module.SOURCE_DB, readonly=False)
    try:
        reject(lambda: grants.execute(owner, runtime, "apply"), "SECURITY DEFINER")
    finally:
        pg.sql(admin, "ALTER FUNCTION public.uuid_generate_v4() SECURITY INVOKER", database=fixture_module.SOURCE_DB, readonly=False)
    results.append("SECURITY DEFINER on a trusted extension member is also refused")
    receipt["stage"] = "RLS and migration head refusal"
    sql(owner, "ALTER TABLE public.campaigns NO FORCE ROW LEVEL SECURITY")
    try:
        reject(lambda: grants.execute(owner, runtime, "apply"), "enforced RLS")
    finally:
        sql(owner, "ALTER TABLE public.campaigns FORCE ROW LEVEL SECURITY")
    sql(owner, "UPDATE public.alembic_version SET version_num='unexpected_head'")
    try:
        reject(lambda: grants.execute(owner, runtime, "apply"), "Migration head")
    finally:
        sql(owner, "UPDATE public.alembic_version SET version_num=%s", (grants.PROFILE["heads"][0],))
    results.append("missing forced RLS and wrong migration head refused without policy repair")

    receipt["stage"] = "actual wrong-credential LOGIN refusal"
    # Bad credentials must be a real failed LOGIN, not a successful SET ROLE
    # probe. Verify is read-only; apply failure explicitly reports committed ACLs.
    badpass = fixture.work / "wrong-runtime-password"
    fixture_module.write_private(badpass, f"{runtime.values['host']}:5432:*:fixture_runtime:wrong-generated-fixture-password\n")
    previous = runtime.passfile
    runtime.passfile = badpass
    try:
        reject(lambda: grants.execute(owner, runtime, "verify"), "Runtime LOGIN verification failed")
        reject(lambda: grants.execute(owner, runtime, "apply"), "Grants committed but runtime LOGIN")
    finally:
        runtime.passfile = previous
    results.append("wrong runtime password fails actual LOGIN and explicitly distinguishes already-committed apply")
    receipt["stage"] = "actual redirected identity and endpoint refusal"
    # Model a user-dependent endpoint redirect with a controlled connector
    # substitution. Every connection remains a REAL verified-TLS LOGIN. This
    # independently proves that matching configured strings do not replace the
    # observed identity/database/server-address checks after connection.
    pg.sql(fixture.service("target_admin"),
           f"CREATE DATABASE {fixture_module.RESTORED_DB} OWNER fixture_migration TEMPLATE template0", readonly=False)
    real_connect = grants.connect
    for wrong in (owner, fixture.service("target_runtime")):
        def redirect(service):
            return real_connect(wrong if service is runtime else service)
        with patch.object(grants, "connect", side_effect=redirect):
            reject(lambda: grants.execute(owner, runtime, "verify"), "Runtime LOGIN verification failed")
    results.append("actual runtime LOGIN is rejected when redirected to owner identity or the other real TLS server")
    receipt["stage"] = "final privilege and policy verification"
    require(grants.execute(owner, runtime, "verify")["policies_sha256"] == result["policies_sha256"], "Final policy fingerprint drift")
    receipt["stage"] = "explicit preserved production inventory qualification"
    results += module("grants_preserved_inventory", HERE / "preserved_inventory_acceptance.py").run_checks(grants, owner, runtime)
    receipt["preserved_profile"] = grants.PRESERVED_PROFILE["profile"]
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, default=Path("/usr/lib/postgresql/16/bin"))
    args = parser.parse_args()
    require(sys.platform == "linux", "This fixture requires Linux and a local Docker daemon")
    require(not args.receipt.exists(), "Refusing to replace a prior receipt")
    for key in list(os.environ):
        if key.startswith("PG") or key in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "READ_DATABASE_URL"}:
            os.environ.pop(key)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    pg = recovery.Postgres(args.bin_dir, timeout=180)
    require(re.search(r"\b16\.", pg.run("psql", ["--version"])), "PostgreSQL16 clients required")
    work = Path(tempfile.mkdtemp(prefix="talklee-runtime-grants-"))
    work.chmod(0o700)
    fixture = fixture_module.Fixture(work, "postgres:16-bookworm")
    receipt = dict(status="failed", production_accessed=False, profile=grants.PROFILE["profile"], checks=[],
                   scope="canonical external-PostgreSQL runtime grants on generated local TLS fixtures")
    receipt["source_git_sha"] = fixture_module.run(["git", "rev-parse", "HEAD"], cwd=ROOT)
    receipt["source_sha256"] = {str(path.relative_to(ROOT)): recovery.digest(path) for path in
        (HERE.parent / "runtime_grants.py", HERE.parent / "runtime_grants_profile.json",
         HERE.parent / "runtime_grants_preserved_profile.json", HERE.parent / "customer_support_catalog.sql",
         HERE / "preserved_inventory_acceptance.py", HERE / "support_contracts_acceptance.py", Path(__file__))}
    try:
        fixture.prepare()
        receipt["postgres_image_digests"] = fixture.image_digests
        fixture_module.bootstrap(pg, fixture)
        checks(fixture, pg, receipt)
        receipt["status"] = "passed"
    except Exception as error:
        receipt["failure"] = str(error) if isinstance(error, grants.GrantsError) else "Fixture acceptance failed; inspect the bounded CI diagnostics"
        receipt["exception_type"] = type(error).__name__
        receipt["failure_locations"] = [f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                                        for frame in traceback.extract_tb(error.__traceback__)[-6:]]
        print(json.dumps({key: receipt.get(key) for key in ("stage", "checks", "exception_type", "failure_locations")}), file=sys.stderr)
        raise
    finally:
        try:
            fixture.cleanup()
            receipt["owned_resources_removed"] = True
        except Exception:
            receipt.update(status="failed", owned_resources_removed=False)
            raise
        finally:
            receipt["completed_at"] = recovery.utcnow()
            recovery.write_json_new(args.receipt, receipt)
            if receipt.get("owned_resources_removed"):
                shutil.rmtree(work)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # No database exception detail, DSN, passwords or customer data in logs.
        detail = str(error) if isinstance(error, (RuntimeError, recovery.RecoveryError)) else type(error).__name__
        print(f"Runtime grants acceptance failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
