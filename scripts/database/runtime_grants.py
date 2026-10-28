#!/usr/bin/env python3
"""Provision or verify the canonical external-PostgreSQL backend login profile.

Uses protected libpq services, a real migration-owner login and a separate real
runtime login. Never creates roles, changes ownership, migrates, disables RLS,
or grants role membership. Run during a maintenance window with migrations idle.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("grants_recovery", HERE / "recovery.py")
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
PROFILE = json.loads((HERE / "runtime_grants_profile.json").read_text(encoding="utf-8"))

# Reviewed canonical invoker triggers. Trigger execution does not require a
# runtime EXECUTE grant; the owner already installed these triggers. Unknown
# routines and SECURITY DEFINER changes require a new reviewed profile.
TRIGGERS = set("""
prevent_call_event_mutation prevent_tenant_policy_audit_log_mutation
log_tenant_policy_mutation maintain_admin_media_deletion_attempt_actors
protect_admin_media_deletion_intent guard_admin_media_deletion_origin_key
guard_admin_media_deletion_request_key protect_admin_media_deletion_request_key
serialize_compliance_media_hold serialize_tenant_partner_membership
update_updated_at_column prevent_inbound_immutable_mutation
prevent_billing_ledger_mutation enforce_inbound_hold_finalize_approval_transition
talky_require_outbound_campaign_artifact talky_lock_campaign_direction_update
queue_call_crm_deliveries reject_invoice_snapshot_mutation
reject_billing_refund_snapshot_change reject_action_resolution_mutation
dnc_entries_fill_phone_number advance_admin_user_revision
reject_supplier_evidence_rewrite
""".split())
ROUTINES = {
    "update_call_status": ("uuid, text, integer", "json", True),
    "prune_tenant_policy_audit_log": ("integer", "integer", False),
}


class GrantsError(RuntimeError):
    """Fixed, nonsecret operator diagnosis, never a database exception string."""


def require(condition, message):
    if not condition:
        raise GrantsError(message)


def ident(value):
    return recovery.identifier(value)


@contextmanager
def connect(service):
    """Do not let libpq environment variables override protected service data."""
    import psycopg2
    require(not service.local_plaintext and service.values.get("sslmode") == "verify-full",
            "Both services require certificate-verified TLS")
    require(service.passfile is not None and service.values.get("sslrootcert"),
            "Both services require an explicit CA file and protected password file")
    values = dict(service.values, passfile=str(service.passfile), connect_timeout="10",
                  application_name="talklee-runtime-grants",
                  options="-c search_path=pg_catalog,public -c statement_timeout=30000 -c lock_timeout=5000")
    # This single-threaded operator CLI restores the caller environment promptly.
    ambient = {key: value for key, value in os.environ.items() if key.startswith("PG")}
    connection = None
    try:
        for key in ambient:
            os.environ.pop(key)
        try:
            connection = psycopg2.connect(**values)
        except psycopg2.Error:
            raise GrantsError("Database login failed; inspect the protected service privately") from None
    finally:
        os.environ.update(ambient)
    try:
        yield connection
    finally:
        connection.close()


def rows(cursor, query, parameters=()):
    cursor.execute(query, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def inspect(cursor, owner, runtime):
    identity = rows(cursor, """SELECT current_user AS identity, session_user AS login,
        current_database() AS database, inet_server_addr()::text AS address,
        inet_server_port() AS port, current_setting('server_version_num')::int AS version,
        (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()) AS tls,
        (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()) AS owner""")[0]
    roles = rows(cursor, """SELECT oid, rolname, rolcanlogin, rolsuper, rolbypassrls,
        rolcreatedb, rolcreaterole, rolreplication, rolconnlimit,
        (rolvaliduntil IS NULL OR rolvaliduntil>now()) AS unexpired,
        EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS membership
        FROM pg_roles r WHERE rolname IN (%s,%s)""", (owner, runtime))
    require(len(roles) == 2, "Owner and runtime must be distinct existing roles")
    for role in roles:
        require(role["rolcanlogin"] and role["unexpired"] and role["rolconnlimit"] != 0,
                "Owner and runtime must be enabled, unexpired LOGIN roles")
        require(not any(role[key] for key in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolreplication", "membership")),
                "Owner and runtime must have no elevated role flags or role memberships")
    require(identity["tls"] and identity["identity"] == identity["login"] == owner,
            "Provisioning requires the actual restricted migration-owner TLS login")
    require(identity["owner"] == owner and 160000 <= identity["version"] < 170000,
            "The supported profile requires PostgreSQL16 and migration ownership of the database")
    owner_oid = next(role["oid"] for role in roles if role["rolname"] == owner)
    runtime_oid = next(role["oid"] for role in roles if role["rolname"] == runtime)
    cursor.execute("""SELECT count(*) FROM pg_db_role_setting WHERE setrole IN (0,%s)
        AND setdatabase IN (0,(SELECT oid FROM pg_database WHERE datname=current_database()))
        AND array_length(setconfig,1)>0""", (runtime_oid,))
    require(cursor.fetchone()[0] == 0, "Runtime or database login settings require separate review")
    cursor.execute("""SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_'
        AND nspname<>'information_schema' AND (nspname<>'public' OR
        pg_get_userbyid(nspowner) NOT IN (%s,'pg_database_owner'))""", (owner,))
    require(cursor.fetchone()[0] == 0, "Unexpected application schema or schema ownership")
    cursor.execute("""SELECT count(*) FROM pg_namespace n,LATERAL aclexplode(n.nspacl) x
        WHERE n.nspname='public' AND x.privilege_type='CREATE' AND x.grantee NOT IN
        (0,%s,%s,(SELECT oid FROM pg_roles WHERE rolname='pg_database_owner'))""", (owner_oid, runtime_oid))
    require(cursor.fetchone()[0] == 0, "Other roles can create application objects; review schema ACLs first")
    cursor.execute("SELECT count(*) FROM pg_database WHERE datdba=%s", (runtime_oid,))
    require(cursor.fetchone()[0] == 0, "Runtime must not own any database")
    cursor.execute("""SELECT count(*) FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
        AND refobjid=%s AND deptype='o' AND dbid IN
        (0,(SELECT oid FROM pg_database WHERE datname=current_database()))""", (runtime_oid,))
    require(cursor.fetchone()[0] == 0, "Runtime must not own schema objects")
    objects = rows(cursor, """SELECT c.oid,c.relname AS name,c.relkind AS kind,
        c.relowner AS owner,c.relrowsecurity AS rls,c.relforcerowsecurity AS force,
        EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='tenant_id'
            AND a.attnum>0 AND NOT a.attisdropped) AS tenant,
        EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid) AS policies,
        EXISTS(SELECT 1 FROM pg_attribute a, LATERAL aclexplode(a.attacl) x
            WHERE a.attrelid=c.oid AND x.grantee IN (0,%s)) AS column_acl
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f') ORDER BY c.relname""", (runtime_oid,))
    for kind, key in (("r", "tables"), ("v", "views"), ("S", "sequences")):
        require({obj["name"] for obj in objects if obj["kind"] == kind} == set(PROFILE[key]),
                "Database object inventory differs from the reviewed canonical profile")
    for obj in objects:
        require(obj["kind"] in {"r", "v", "S"} and obj["owner"] == owner_oid,
                "Mixed object ownership or unsupported relation kind; no ownership changes performed")
        require(not obj["column_acl"], "Column-specific runtime or PUBLIC grants require separate review")
        if obj["kind"] == "r" and (obj["tenant"] or obj["rls"]):
            require(obj["rls"] and obj["force"] and obj["policies"],
                    "Tenant or protected table lacks enforced RLS policies")
    cursor.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    require([row[0] for row in cursor.fetchall()] == PROFILE["heads"],
            "Migration head does not match the reviewed runtime grants profile")
    extensions = rows(cursor, "SELECT extname,extowner FROM pg_extension")
    require({e["extname"] for e in extensions} == set(PROFILE["extensions"]),
            "Unexpected or missing PostgreSQL extension")
    require(all(e["extowner"] == owner_oid for e in extensions if e["extname"] != "plpgsql"),
            "Application extensions must be owned by the migration owner")
    functions = rows(cursor, """SELECT p.oid,p.proname AS name,p.proowner AS owner,
        p.prosecdef AS definer,p.prokind AS kind,oidvectortypes(p.proargtypes) AS args,
        p.prorettype::regtype::text AS returns,
        (SELECT e.extname FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
            WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') AS extension
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' ORDER BY p.proname,p.oid""")
    require(set(ROUTINES) <= {function["name"] for function in functions if not function["extension"]},
            "A reviewed application function is missing")
    for function in functions:
        require(not function["definer"] and function["kind"] == "f",
                "SECURITY DEFINER routines and non-functions require explicit separate review")
        if function["extension"]:
            require(function["extension"] in PROFILE["extensions"], "Unreviewed extension function")
            continue  # Trusted extensions may give their functions to the bootstrap owner.
        require(function["owner"] == owner_oid, "Mixed application function ownership")
        if function["name"] in TRIGGERS:
            require(function["args"] == "" and function["returns"] == "trigger", "Unexpected trigger signature")
        else:
            expected = ROUTINES.get(function["name"])
            require(expected and (function["args"], function["returns"]) == expected[:2],
                    "Unknown application function or overload requires a reviewed profile")
    cursor.execute("""SELECT json_agg(json_build_array(c.relname,c.relrowsecurity,c.relforcerowsecurity,
        p.polname,p.polcmd,p.polpermissive,p.polroles,pg_get_expr(p.polqual,p.polrelid),
        pg_get_expr(p.polwithcheck,p.polrelid)) ORDER BY c.relname,p.polname)
        FROM pg_class c LEFT JOIN pg_policy p ON p.polrelid=c.oid
        WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'""")
    policy_hash = hashlib.sha256(json.dumps(cursor.fetchone()[0], sort_keys=True).encode()).hexdigest()
    return dict(identity=identity, owner_oid=owner_oid, runtime_oid=runtime_oid,
                objects=objects, functions=functions, policy_hash=policy_hash)


def apply_acl(cursor, state, owner, runtime):
    owner_q, runtime_q = ident(owner), ident(runtime)
    database_q = ident(state["identity"]["database"])
    cursor.execute(f"REVOKE ALL ON DATABASE {database_q} FROM PUBLIC, {runtime_q}")
    cursor.execute(f"GRANT CONNECT ON DATABASE {database_q} TO {runtime_q}")
    cursor.execute(f"REVOKE ALL ON SCHEMA public FROM PUBLIC, {runtime_q}")
    cursor.execute(f"GRANT USAGE ON SCHEMA public TO {runtime_q}")
    for obj in state["objects"]:
        target = "public." + ident(obj["name"])
        kind = "SEQUENCE" if obj["kind"] == "S" else "TABLE"
        cursor.execute(f"REVOKE ALL ON {kind} {target} FROM PUBLIC, {runtime_q}")
        privileges = ("SELECT" if obj["name"] == "ai_config_migrations_id_seq" else "USAGE, SELECT" if kind == "SEQUENCE" else "SELECT" if
                      obj["kind"] == "v" or obj["name"] in PROFILE["read_only_tables"] else "SELECT, INSERT, UPDATE, DELETE")
        cursor.execute(f"GRANT {privileges} ON {kind} {target} TO {runtime_q}")
    for function in state["functions"]:
        if function["extension"]:
            continue
        target = "public." + ident(function["name"]) + "(" + function["args"] + ")"
        cursor.execute(f"REVOKE ALL ON FUNCTION {target} FROM PUBLIC, {runtime_q}")
        if ROUTINES.get(function["name"], (None, None, False))[2]:
            cursor.execute(f"GRANT EXECUTE ON FUNCTION {target} TO {runtime_q}")
    # Both global and schema defaults matter: schema REVOKE alone cannot undo
    # PostgreSQL's default global PUBLIC EXECUTE. New objects are fail-closed.
    for scope in ("", " IN SCHEMA public"):
        for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            cursor.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_q}{scope} REVOKE ALL ON {kind} FROM PUBLIC, {runtime_q}")


def verify_acl(cursor, state, runtime):
    for obj in state["objects"]:
        allowed = ({"SELECT"} if obj["name"] == "ai_config_migrations_id_seq" else {"USAGE", "SELECT"} if obj["kind"] == "S" else {"SELECT"} if
                   obj["kind"] == "v" or obj["name"] in PROFILE["read_only_tables"] else {"SELECT", "INSERT", "UPDATE", "DELETE"})
        function = "has_sequence_privilege" if obj["kind"] == "S" else "has_table_privilege"
        all_privileges = {"USAGE", "SELECT", "UPDATE"} if obj["kind"] == "S" else {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"}
        for privilege in sorted(all_privileges):
            cursor.execute(f"SELECT {function}(%s,%s,%s),{function}(%s,%s,%s)",
                           (runtime, obj["oid"], privilege, runtime, obj["oid"], privilege + " WITH GRANT OPTION"))
            require(cursor.fetchone() == (privilege in allowed, False), "Runtime table or sequence ACL differs from supported profile")
    cursor.execute("""SELECT has_database_privilege(%s,current_database(),'CONNECT'),
        has_database_privilege(%s,current_database(),'CREATE'),has_database_privilege(%s,current_database(),'TEMP'),
        has_schema_privilege(%s,'public','USAGE'),has_schema_privilege(%s,'public','CREATE')""", (runtime,) * 5)
    require(cursor.fetchone() == (True, False, False, True, False), "Runtime database or schema ACL differs from profile")
    cursor.execute("""SELECT has_database_privilege(%s,current_database(),'CONNECT WITH GRANT OPTION'),
        has_schema_privilege(%s,'public','USAGE WITH GRANT OPTION')""", (runtime, runtime))
    require(cursor.fetchone() == (False, False), "Runtime cannot delegate database or schema privileges")
    cursor.execute("""SELECT count(*) FROM (
        SELECT x.grantee FROM pg_class c, LATERAL aclexplode(c.relacl) x
            WHERE c.relnamespace='public'::regnamespace
        UNION ALL SELECT x.grantee FROM pg_namespace n,LATERAL aclexplode(n.nspacl) x WHERE n.nspname='public'
        UNION ALL SELECT x.grantee FROM pg_database d,LATERAL aclexplode(d.datacl) x WHERE d.datname=current_database()
        ) a WHERE grantee=0""")
    require(cursor.fetchone()[0] == 0, "PUBLIC retains application database, schema or relation privileges")
    for function in state["functions"]:
        if function["extension"]:
            continue
        expected = ROUTINES.get(function["name"], (None, None, False))[2]
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),has_function_privilege(%s,%s,'EXECUTE WITH GRANT OPTION')",
                       (runtime, function["oid"], runtime, function["oid"]))
        require(cursor.fetchone() == (expected, False), "Runtime function ACL differs from reviewed profile")
        cursor.execute("SELECT count(*) FROM pg_proc p,LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x WHERE p.oid=%s AND x.grantee=0",
                       (function["oid"],))
        require(cursor.fetchone()[0] == 0, "PUBLIC retains application function EXECUTE")
    cursor.execute("SELECT has_function_privilege(%s,'public.uuid_generate_v4()','EXECUTE')", (runtime,))
    require(cursor.fetchone()[0], "Runtime cannot execute the canonical UUID default")
    cursor.execute("""SELECT count(*) FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) x
        WHERE d.defaclrole=%s AND d.defaclobjtype IN ('r','S','f')
        AND d.defaclnamespace IN (0,'public'::regnamespace) AND x.grantee IN (0,%s)""",
                   (state["owner_oid"], state["runtime_oid"]))
    require(cursor.fetchone()[0] == 0, "Future-object privileges are broader than the fail-closed profile")
    # A missing global function ACL means built-in PUBLIC EXECUTE still applies.
    cursor.execute("SELECT count(*) FROM pg_default_acl WHERE defaclrole=%s AND defaclnamespace=0 AND defaclobjtype='f'",
                   (state["owner_oid"],))
    require(cursor.fetchone()[0] == 1, "Global PUBLIC function default has not been revoked")


def execute(owner_service, runtime_service, mode):
    require(mode in {"plan", "apply", "verify"}, "Unknown mode")
    owner, runtime = owner_service.values["user"], runtime_service.values["user"]
    require(owner != runtime, "Migration and runtime identities must differ")
    for key in ("host", "hostaddr", "port", "dbname"):
        require(owner_service.values.get(key) == runtime_service.values.get(key),
                "Owner and runtime services must select the same explicit endpoint and database")
    result = dict(profile=PROFILE["profile"], mode=mode, status="failed", grants_committed=False,
                  runtime_login_verified=False, schema_heads=PROFILE["heads"])
    with connect(owner_service) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY" if mode != "apply" else "SET TRANSACTION READ WRITE")
            # Diagnose an unowned relation before attempting LOCK on it. The
            # migration owner may have no SELECT privilege on a foreign-owned
            # table, so locking first hides the actual ownership refusal.
            state = inspect(cursor, owner, runtime)
            if mode == "apply":
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('talklee:runtime-grants',0))")
                for table in PROFILE["tables"]:
                    cursor.execute("LOCK TABLE public." + ident(table) + " IN ACCESS SHARE MODE")
                # Re-read after locks: preflight does not authorize a stale
                # ownership/policy snapshot if DDL raced with acquisition.
                state = inspect(cursor, owner, runtime)
            result.update(tables=len(PROFILE["tables"]), sequences=len(PROFILE["sequences"]),
                          policies_sha256=state["policy_hash"])
            if mode == "plan":
                connection.rollback()
                result["status"] = "plan_ready"
                return result
            if mode == "apply":
                apply_acl(cursor, state, owner, runtime)
            verify_acl(cursor, state, runtime)
            require(inspect(cursor, owner, runtime)["policy_hash"] == state["policy_hash"],
                    "Tenant policy definitions changed during grants operation")
        connection.commit()
        result["grants_committed"] = mode == "apply"
    # This is a real second LOGIN, not SET ROLE. Provisioning may already have
    # committed if credentials fail here; report that explicitly to the caller.
    try:
        with connect(runtime_service) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("""SELECT current_user,session_user,current_database(),inet_server_addr()::text,
                    inet_server_port(),(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())""")
                expected = (runtime, runtime, state["identity"]["database"], state["identity"]["address"], state["identity"]["port"], True)
                require(cursor.fetchone() == expected, "Runtime LOGIN identity, TLS or actual endpoint differs")
                cursor.execute("""SELECT rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication)
                    AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid)
                    FROM pg_roles r WHERE rolname=current_user""")
                require(cursor.fetchone() == (True,), "Runtime LOGIN privilege posture changed")
                cursor.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num")
                require([row[0] for row in cursor.fetchall()] == PROFILE["heads"], "Runtime cannot read the verified schema head")
                verify_acl(cursor, state, runtime)
            connection.rollback()
    except Exception:
        raise GrantsError("Grants committed but runtime LOGIN verification failed" if result["grants_committed"] else
                          "Runtime LOGIN verification failed") from None
    result.update(status="verified", runtime_login_verified=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply", "verify"))
    parser.add_argument("--service-file", type=Path, required=True)
    parser.add_argument("--owner-service", required=True)
    parser.add_argument("--runtime-service", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    require(not args.receipt.exists(), "Refusing to replace a prior receipt")
    result = execute(recovery.Service(args.service_file, args.owner_service),
                     recovery.Service(args.service_file, args.runtime_service), args.mode)
    recovery.write_json_new(args.receipt, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        detail = str(error) if isinstance(error, (GrantsError, recovery.RecoveryError)) else "Database operation failed; inspect privately"
        print(f"Runtime grants failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
