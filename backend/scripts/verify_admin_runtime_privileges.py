#!/usr/bin/env python3
"""Read-only Admin release preflight; never installs roles or changes grants.

Run with the application's environment, AFTER migration/head verification:
    python scripts/verify_admin_runtime_privileges.py
Use --contract to print the source-derived contract without opening a socket.

A passing result proves catalog capabilities for these existing operations,
not policy semantics, current-principal authorization or external effects.
Column-only grants are reported as unverified, never advised away with GRANT ALL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg


BACKEND_ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = {"S": "SELECT", "I": "INSERT", "U": "UPDATE", "D": "DELETE"}

# Union of operations actually consumed by existing Admin routes and their
# mandatory identity, audit and settlement dependencies. No fixture-cleanup
# DELETE, generic action retry, provider workers or unrelated billing rollout.
# Sources are backend-relative; FOR UPDATE also requires UPDATE permission.
GROUPS = (
    ("app/api/v1/endpoints/admin/actions.py", {
        "assistant_actions": "SU", "assistant_action_resolutions": "SI",
        "calls": "S", "campaigns": "S", "dialer_jobs": "S", "leads": "S", "tenants": "S",
    }),
    ("app/api/v1/endpoints/admin/calls.py", {
        "calls": "SU", "call_events": "SI", "call_legs": "SU", "crm_deliveries": "S",
        "recordings_s3": "S", "call_feedback": "S", "inbound_usage_transactions": "S",
    }),
    # Invoker-security trigger reached by Admin termination/media call updates.
    ("Alembic/versions/0066_crm_skip_test_calls.py", {"crm_deliveries": "SIU"}),
    ("app/api/v1/endpoints/admin/media.py", {
        "admin_media_deletion_intents": "SIU", "admin_media_deletion_request_keys": "SI",
        "recordings_s3": "SUD", "call_feedback": "SUD", "suspension_events": "S",
    }),
    ("app/domain/services/call_feedback_service.py", {"call_feedback": "SIUD"}),
    ("app/api/v1/endpoints/admin/users.py", {
        "user_profiles": "SIUD", "tenant_users": "SIUD", "roles": "S", "tenants": "SU",
        "security_sessions": "SU", "refresh_tokens": "SU", "mfa_challenges": "SU", "webauthn_challenges": "SU",
    }),
    ("app/api/v1/endpoints/admin/user_operations.py", {"audit_logs": "SI"}),
    ("app/core/security/credential_issuance.py", {"user_profiles": "SU", "webauthn_challenges": "S",
                                               "suspension_events": "S", "audit_logs": "S"}),
    ("app/api/v1/endpoints/admin/tenants.py", {"tenants": "SIU", "tenant_quotas": "I", "plans": "S"}),
    ("app/services/connector_lifecycle.py", {
        "connectors": "SU", "connector_accounts": "SIU", "meetings": "S", "reminders": "SU",
    }),
    ("app/api/v1/endpoints/connectors.py", {"connectors": "SIU"}),
    ("app/domain/services/platform_runtime_controls.py", {"platform_runtime_controls": "SIU"}),
    ("app/domain/services/suspension_service.py", {
        "suspension_events": "SIU", "white_label_partners": "SU", "user_profiles": "SU",
        "tenants": "SU", "tenant_users": "SU", "security_sessions": "SU",
        "refresh_tokens": "SU", "mfa_challenges": "SU", "webauthn_challenges": "SU",
    }),
    ("app/domain/services/audit_logger.py", {"audit_logs": "SI", "security_events": "I"}),
    ("app/core/security/rbac.py", {"permissions": "S", "role_permissions": "S", "user_permissions": "S"}),
    ("app/core/security/sessions/lifecycle.py", {"security_sessions": "SIU"}),
    ("app/core/security/refresh_tokens.py", {"refresh_tokens": "SIU"}),
    ("app/core/security/lockout.py", {"login_attempts": "SI"}),
    ("app/api/v1/endpoints/mfa/verify.py", {"user_mfa": "SU", "mfa_challenges": "SU"}),
    ("app/api/v1/endpoints/mfa/challenge.py", {"mfa_challenges": "SIU"}),
    ("app/core/security/recovery.py", {"recovery_codes": "SU"}),
    ("app/domain/services/contact_enquiry_service.py", {"public_contact_enquiries": "SU"}),
    ("app/api/v1/endpoints/alertmanager.py", {"incidents": "SIU"}),
    ("app/domain/services/inbound_campaign_service.py", {
        "campaigns": "SU", "contact_lists": "S", "tenant_ai_configs": "S",
        "inbound_audit_events": "I", "inbound_campaign_configs": "SIU",
        "inbound_did_assignments": "SIU", "inbound_operation_idempotency": "SIU",
        "inbound_reassignment_requests": "SIU", "platform_runtime_controls": "SU",
        "tenant_inbound_controls": "SIU", "tenant_phone_numbers": "SIU",
        "tenant_sip_trunks": "S", "tenant_telephony_concurrency_policies": "SI",
    }),
    ("app/domain/services/telephony/inbound_admission.py", {
        "inbound_billing_hold_finalize_approvals": "SIU", "inbound_usage_transactions": "SI",
    }),
    ("app/domain/services/telephony/inbound_transfer.py", {"call_legs": "SU", "call_events": "I"}),
    ("app/domain/services/telephony_concurrency_limiter.py", {
        "tenant_telephony_concurrency_leases": "SU", "tenant_telephony_concurrency_events": "I",
    }),
)
# Optional hook already called inside Admin tenant creation. Check only when
# its existing complete platform SIP configuration would actually execute it.
SIP_GROUP = ("app/services/scripts/seed_platform_sip_trunk.py", {
    "tenant_sip_trunks": "SIU", "tenant_policy_audit_log": "I",
})

# Explicit source-owned protection, not an assumption that every global/auth
# table has tenant RLS. 0013 canonicalizes existing protected tenant tables;
# 0038 backfills its named tables; later feature migrations own special policies.
RLS_TABLES = frozenset("""
assistant_actions assistant_action_resolutions audit_logs calls call_legs call_events campaigns
leads dialer_jobs recordings_s3 call_feedback crm_deliveries admin_media_deletion_intents
admin_media_deletion_request_keys connectors connector_accounts meetings reminders user_profiles
tenant_users user_permissions white_label_partners refresh_tokens security_events tenant_quotas
public_contact_enquiries incidents tenant_ai_configs inbound_audit_events inbound_campaign_configs
inbound_did_assignments inbound_operation_idempotency inbound_reassignment_requests
tenant_inbound_controls tenant_phone_numbers tenant_sip_trunks tenant_policy_audit_log
tenant_telephony_concurrency_policies tenant_telephony_concurrency_leases
tenant_telephony_concurrency_events inbound_billing_hold_finalize_approvals inbound_usage_transactions
""".split())
NEW_COLUMNS = {
    "user_profiles": ("admin_revision",),
    "tenant_users": ("admin_revision",),
    "public_contact_enquiries": ("revision",),
    "connector_accounts": ("credential_generation", "refresh_claim_id", "refresh_claimed_at", "refresh_state", "refresh_error"),
    "connectors": ("provider_revocation_status",),
    "incidents": ("source_system", "source_fingerprint", "source_started_at", "source_status",
                  "source_recovered_at", "source_last_received_at", "source_labels"),
}
FUNCTIONS = (
    "pg_catalog.pg_advisory_xact_lock(bigint)", "pg_catalog.hashtextextended(text,bigint)",
    "pg_catalog.set_config(text,text,boolean)", "pg_catalog.current_setting(text,boolean)",
    "pg_catalog.clock_timestamp()", "pg_catalog.now()", "pg_catalog.gen_random_uuid()",
    "pg_catalog.pg_database_size(name)",
)
LIMITS = [
    "Catalog checks do not prove RLS policy expressions, HTTP authorization, trigger semantics or external effects.",
    "Function checks cover explicit coordination/time/UUID functions and catalog-linked insert defaults, not every SQL expression builtin.",
    "Table-level ACL contract; column-only grants require separate statement-level review and do not pass this gate.",
    "Ownership is reported separately from effective SQL ACLs; owners retain DDL/regrant authority and FORCE RLS still applies unless role bypasses it.",
    "Trusted backend code can set app.bypass_rls; this is separate from PostgreSQL SUPERUSER/BYPASSRLS.",
    "No active Admin route uses READ_DATABASE_URL; a configured read connection receives posture/head checks, not write requirements.",
    "Pool and legacy direct adapter use DATABASE_URL; adapter snapshots it at import. A fresh preflight cannot prove an already-running process environment.",
    "No hosted observation, grants, role installation, customer-row reads, provider probes or production mutation are performed.",
]


def contract(*, platform_sip: bool = False) -> dict:
    tables = {}
    for source, requirements in (*GROUPS, *((SIP_GROUP,) if platform_sip else ())):
        for name, codes in requirements.items():
            item = tables.setdefault(name, {"operations": [], "sources": [], "force_rls": name in RLS_TABLES})
            item["operations"] = sorted(set(item["operations"]) | {OPERATIONS[c] for c in codes})
            item["sources"].append(source)
    return {"version": 1, "tables": dict(sorted(tables.items())), "required_columns": NEW_COLUMNS,
            "explicit_functions": FUNCTIONS, "platform_sip_hook_enabled": platform_sip, "limits": LIMITS}


def platform_sip_configured(environ) -> bool:
    # Mirror seed_platform_sip_trunk._read_platform_env/seed_for_tenant exactly;
    # this observer must not introduce different credential validation.
    return bool((environ.get("PLATFORM_SIP_DOMAIN") or "").strip()
                and environ.get("PLATFORM_SIP_USERNAME") and environ.get("PLATFORM_SIP_PASSWORD"))


ROLE_SQL = """SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolinherit,
    s.rolsuper AS session_rolsuper, s.rolbypassrls AS session_rolbypassrls,
    current_user=session_user AS session_role_matches,
    has_schema_privilege(current_user,'public','USAGE') AS schema_usage,
    current_setting('row_security')='on' AS row_security_on,
    current_setting('transaction_read_only')='on' AS read_only
  FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_roles s
  WHERE r.rolname=current_user AND s.rolname=session_user"""
TABLE_SQL = """SELECT c.relname AS name, c.relkind::text AS relkind, c.relrowsecurity, c.relforcerowsecurity,
    to_regclass(c.relname)=c.oid AS unqualified_resolves_public,
    pg_has_role(current_user,c.relowner,'USAGE') AS owner_capability,
    has_table_privilege(current_user,c.oid,'SELECT') AS select_ok,
    has_table_privilege(current_user,c.oid,'INSERT') AS insert_ok,
    has_table_privilege(current_user,c.oid,'UPDATE') AS update_ok,
    has_table_privilege(current_user,c.oid,'DELETE') AS delete_ok,
    has_any_column_privilege(current_user,c.oid,'SELECT') AS select_column,
    has_any_column_privilege(current_user,c.oid,'INSERT') AS insert_column,
    has_any_column_privilege(current_user,c.oid,'UPDATE') AS update_column,
    ARRAY(SELECT a.attname::text FROM pg_catalog.pg_attribute a
          WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
    (SELECT COUNT(*) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid) AS policy_count
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname=ANY($1::text[])"""
DEFAULTS_SQL = """SELECT DISTINCT c.relname AS table_name, a.attname AS column_name,
    pn.nspname||'.'||p.proname AS function_name,
    has_schema_privilege(current_user,pn.oid,'USAGE') AND
      has_function_privilege(current_user,p.oid,'EXECUTE') AS executable
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
  JOIN pg_catalog.pg_attrdef ad ON ad.adrelid=c.oid AND ad.adnum=a.attnum
  JOIN pg_catalog.pg_depend d ON d.classid='pg_attrdef'::regclass AND d.objid=ad.oid
      AND d.refclassid='pg_proc'::regclass
  JOIN pg_catalog.pg_proc p ON p.oid=d.refobjid JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
  WHERE n.nspname='public' AND c.relname=ANY($1::text[])"""
SEQUENCES_SQL = """SELECT c.relname AS table_name,a.attname AS column_name,
    has_sequence_privilege(current_user,seq.oid,'USAGE') OR
      has_sequence_privilege(current_user,seq.oid,'UPDATE') AS usable
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
  JOIN pg_catalog.pg_attrdef ad ON ad.adrelid=c.oid AND ad.adnum=a.attnum
  JOIN pg_catalog.pg_depend d ON d.classid='pg_attrdef'::regclass AND d.objid=ad.oid
      AND d.refclassid='pg_class'::regclass
  JOIN pg_catalog.pg_class seq ON seq.oid=d.refobjid AND seq.relkind='S'
  WHERE n.nspname='public' AND c.relname=ANY($1::text[]) AND a.attidentity=''"""


def evaluate_catalog(snapshot: dict, manifest: dict, expected_heads: list[str], *, require_admin: bool) -> dict:
    """Pure decision seam: effective access is valid; missing proof stays explicit."""
    failures, warnings, details = [], [], []
    role = snapshot["role"]
    if not role.get("read_only"):
        failures.append("read_only_transaction_not_confirmed")
    if role.get("rolsuper") or role.get("rolbypassrls"):
        failures.append("runtime_role_bypasses_rls")
    if role.get("session_rolsuper") or role.get("session_rolbypassrls"):
        failures.append("login_role_retains_rls_bypass_authority")
    if not role.get("row_security_on"):
        failures.append("row_security_not_enabled")
    if not role.get("schema_usage"):
        failures.append("public_schema_usage_missing")
    if role.get("rolcreaterole") or role.get("rolcreatedb"):
        warnings.append("runtime_role_has_role_or_database_creation_authority")
    if len(expected_heads) != 1 or sorted(snapshot["heads"]) != sorted(expected_heads):
        failures.append("alembic_head_mismatch")
    tables = {row["name"]: row for row in snapshot["tables"]}
    for name, requirement in manifest["tables"].items():
        row = tables.get(name)
        if row is None or row["relkind"] not in ("r", "p"):
            failures.append(f"table_missing:{name}")
            continue
        if not row.get("unqualified_resolves_public"):
            failures.append(f"unqualified_table_resolution_mismatch:{name}")
        if requirement["force_rls"] and not (
            row["relrowsecurity"] and row["relforcerowsecurity"] and row["policy_count"] > 0
        ):
            failures.append(f"required_rls_posture_missing:{name}")
        missing_columns = sorted(set(manifest["required_columns"].get(name, ())) - set(row["columns"]))
        if missing_columns:
            failures.append(f"required_columns_missing:{name}:{','.join(missing_columns)}")
        checks = {}
        for operation in requirement["operations"] if require_admin else ():
            key = operation.lower()
            status = "satisfied" if row[f"{key}_ok"] else (
                "column_grants_unverified" if row.get(f"{key}_column") else "missing"
            )
            checks[operation] = status
            if status != "satisfied":
                failures.append(f"{status}:{name}:{operation}")
        details.append({"table": name, "operations": checks, "owner_capability": row["owner_capability"],
                        "unqualified_resolves_public": row["unqualified_resolves_public"],
                        "rls_enabled": row["relrowsecurity"], "rls_forced": row["relforcerowsecurity"],
                        "policy_count": row["policy_count"]})
    if any(row["owner_capability"] for row in snapshot["tables"]):
        warnings.append("runtime_can_own_or_alter_some_application_tables")
    if require_admin:
        for row in snapshot["functions"]:
            if not row["executable"]:
                failures.append(f"function_execute_missing:{row['function_name']}")
        for row in snapshot["defaults"]:
            if not row["executable"]:
                failures.append(f"default_function_execute_missing:{row['table_name']}:{row['column_name']}")
        for row in snapshot["sequences"]:
            if not row["usable"]:
                failures.append(f"default_sequence_usage_missing:{row['table_name']}:{row['column_name']}")
    return {"status": "failed" if failures else "passed", "failures": failures, "warnings": warnings,
            "role_posture": role, "database_heads": snapshot["heads"], "repository_heads": expected_heads,
            "admin_operations_checked": require_admin, "tables": details,
            "functions": snapshot["functions"], "default_functions": snapshot["defaults"],
            "default_sequences": snapshot["sequences"]}


async def inspect_connection(conn, manifest: dict) -> dict:
    """Only catalog SELECTs plus the Alembic version metadata; never SET ROLE."""
    names = list(manifest["tables"])
    inserted = [name for name, item in manifest["tables"].items() if "INSERT" in item["operations"]]
    async with conn.transaction(readonly=True):
        role = dict(await conn.fetchrow(ROLE_SQL))
        if not role["read_only"]:
            raise RuntimeError("readonly_not_confirmed")
        heads = [row["version_num"] for row in await conn.fetch("SELECT version_num FROM public.alembic_version")]
        tables = [dict(row) for row in await conn.fetch(TABLE_SQL, names)]
        functions = [dict(row) for row in await conn.fetch("""SELECT f AS function_name,
            COALESCE(has_function_privilege(current_user,to_regprocedure(f),'EXECUTE'),false) AS executable
            FROM unnest($1::text[]) AS f""", list(FUNCTIONS))]
        defaults = [dict(row) for row in await conn.fetch(DEFAULTS_SQL, inserted)]
        sequences = [dict(row) for row in await conn.fetch(SEQUENCES_SQL, inserted)]
    return {"role": role, "heads": heads, "tables": tables, "functions": functions,
            "defaults": defaults, "sequences": sequences}


async def inspect_dsn(dsn: str, manifest: dict, expected_heads: list[str], *, require_admin: bool) -> dict:
    conn = None
    try:
        async with asyncio.timeout(30):
            conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5, server_settings={
                "application_name": "talky-admin-readonly-preflight",
                "default_transaction_read_only": "on", "statement_timeout": "5000", "lock_timeout": "2000",
            })
            snapshot = await inspect_connection(conn, manifest)
            return evaluate_catalog(snapshot, manifest, expected_heads, require_admin=require_admin)
    except Exception as exc:
        # Driver errors can include DSNs, usernames and certificate paths.
        # Only a bounded type/code crosses this release-report boundary.
        code = getattr(exc, "sqlstate", None)
        return {"status": "failed", "failures": ["catalog_observation_unavailable"],
                "error_type": type(exc).__name__, "sqlstate": code if isinstance(code, str) and len(code) == 5 else None}
    finally:
        if conn is not None:
            try:
                await conn.close(timeout=2)
            except Exception:
                conn.terminate()


async def run_configured(manifest: dict, expected_heads: list[str], environ) -> dict:
    primary = environ.get("DATABASE_URL", "").strip()
    if not primary:
        return {"status": "failed", "failures": ["DATABASE_URL_required"], "limits": LIMITS}
    primary_result = await inspect_dsn(primary, manifest, expected_heads, require_admin=True)
    paths = {"primary_pool_and_direct_adapter": primary_result}
    read = environ.get("READ_DATABASE_URL", "").strip()
    if read and read != primary:
        paths["optional_read_pool"] = await inspect_dsn(read, manifest, expected_heads, require_admin=False)
    return {"version": 1, "status": "passed" if all(item["status"] == "passed" for item in paths.values()) else "failed",
            "paths": paths, "read_pool_aliases_primary": not read or read == primary, "limits": LIMITS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", action="store_true", help="Print contract only; no environment file or database access")
    args = parser.parse_args()
    if args.contract:
        print(json.dumps(contract(), indent=2))
        return 0
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.core.dotenv_compat import load_dotenv
    from scripts.verify_alembic_current_heads import _repository_scripts

    load_dotenv(dotenv_path=BACKEND_ROOT / ".env", override=False)
    manifest = contract(platform_sip=platform_sip_configured(os.environ))
    try:
        expected_heads = _repository_scripts(BACKEND_ROOT).get_heads()
        result = asyncio.run(run_configured(manifest, expected_heads, os.environ))
    except Exception as exc:
        result = {"status": "failed", "failures": ["preflight_configuration_unavailable"], "error_type": type(exc).__name__}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
