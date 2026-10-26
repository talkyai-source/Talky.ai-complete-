"""Offline fault cases for the release gate; no database role or grants created."""
from __future__ import annotations

import json
import socket
from unittest.mock import AsyncMock

import pytest

from scripts import verify_admin_runtime_privileges as gate


@pytest.fixture(autouse=True)
async def no_network(monkeypatch):
    # Patch after the Windows event loop creates its local socketpair.
    def denied(*args, **kwargs):
        raise AssertionError("No network is permitted in this synthetic catalog test")
    with monkeypatch.context() as scoped:
        scoped.setattr(socket.socket, "connect", denied)
        scoped.setattr(socket.socket, "connect_ex", denied)
        scoped.setattr(socket, "create_connection", denied)
        yield


def healthy_snapshot(manifest=None):
    manifest = manifest or gate.contract()
    return {
        "role": {"rolsuper": False, "rolbypassrls": False, "rolcreaterole": False,
                 "session_rolsuper": False, "session_rolbypassrls": False, "row_security_on": True,
                 "rolcreatedb": False, "rolinherit": True, "session_role_matches": True,
                 "schema_usage": True, "read_only": True},
        "heads": ["test-head"],
        "tables": [{"name": name, "relkind": "r", "relrowsecurity": item["force_rls"],
                    "relforcerowsecurity": item["force_rls"], "owner_capability": False,
                    "unqualified_resolves_public": True,
                    "policy_count": 1 if item["force_rls"] else 0,
                    "columns": list(manifest["required_columns"].get(name, ())),
                    **{f"{operation.lower()}_ok": True for operation in gate.OPERATIONS.values()},
                    "select_column": True, "insert_column": True, "update_column": True}
                   for name, item in manifest["tables"].items()],
        "functions": [{"function_name": name, "executable": True} for name in gate.FUNCTIONS],
        "defaults": [], "sequences": [],
    }


def evaluate(snapshot, **kwargs):
    return gate.evaluate_catalog(snapshot, gate.contract(), ["test-head"], require_admin=kwargs.get("require_admin", True))


def table(snapshot, name):
    return next(row for row in snapshot["tables"] if row["name"] == name)


def test_restricted_role_with_effective_privileges_passes_without_ownership():
    result = evaluate(healthy_snapshot())
    assert result["status"] == "passed"
    assert result["warnings"] == []


def test_owned_tables_satisfy_acl_but_report_ddl_authority():
    snapshot = healthy_snapshot()
    table(snapshot, "incidents")["owner_capability"] = True
    result = evaluate(snapshot)
    assert result["status"] == "passed"
    assert "runtime_can_own_or_alter_some_application_tables" in result["warnings"]


def test_ownership_does_not_override_an_explicitly_revoked_effective_privilege():
    snapshot = healthy_snapshot()
    table(snapshot, "incidents").update(owner_capability=True, insert_ok=False, insert_column=False)
    assert "missing:incidents:INSERT" in evaluate(snapshot)["failures"]


@pytest.mark.parametrize("flag", ["rolsuper", "rolbypassrls"])
def test_privileged_role_cannot_make_force_rls_gate_green(flag):
    snapshot = healthy_snapshot()
    snapshot["role"][flag] = True
    assert "runtime_role_bypasses_rls" in evaluate(snapshot)["failures"]


@pytest.mark.parametrize("flag", ["session_rolsuper", "session_rolbypassrls"])
def test_set_role_does_not_hide_privileged_login_authority(flag):
    snapshot = healthy_snapshot()
    snapshot["role"][flag] = True
    snapshot["role"]["session_role_matches"] = False
    assert "login_role_retains_rls_bypass_authority" in evaluate(snapshot)["failures"]


def test_shadow_schema_or_disabled_row_security_cannot_pass():
    snapshot = healthy_snapshot()
    table(snapshot, "incidents")["unqualified_resolves_public"] = False
    snapshot["role"]["row_security_on"] = False
    assert {"unqualified_table_resolution_mismatch:incidents", "row_security_not_enabled"} <= set(evaluate(snapshot)["failures"])


@pytest.mark.parametrize("mutation,expected", [
    ({"insert_ok": False, "insert_column": False}, "missing:incidents:INSERT"),
    ({"update_ok": False, "update_column": True}, "column_grants_unverified:incidents:UPDATE"),
    ({"relforcerowsecurity": False}, "required_rls_posture_missing:incidents"),
    ({"policy_count": 0}, "required_rls_posture_missing:incidents"),
    ({"columns": []}, "required_columns_missing:incidents:"),
])
def test_incident_split_owner_acl_and_schema_fail_closed(mutation, expected):
    snapshot = healthy_snapshot()
    table(snapshot, "incidents").update(mutation)
    result = evaluate(snapshot)
    assert result["status"] == "failed"
    assert any(item.startswith(expected) for item in result["failures"])


def test_missing_table_and_head_drift_are_not_empty_success():
    snapshot = healthy_snapshot()
    snapshot["tables"] = [row for row in snapshot["tables"] if row["name"] != "connector_accounts"]
    snapshot["heads"] = ["0070_trunk_soft_delete"]
    assert {"table_missing:connector_accounts", "alembic_head_mismatch"} <= set(evaluate(snapshot)["failures"])


def test_functions_and_serial_defaults_require_runtime_access():
    snapshot = healthy_snapshot()
    snapshot["functions"][0]["executable"] = False
    snapshot["defaults"] = [{"table_name": "incidents", "column_name": "id", "executable": False}]
    snapshot["sequences"] = [{"table_name": "audit_logs", "column_name": "sequence", "usable": False}]
    failures = evaluate(snapshot)["failures"]
    assert any(item.startswith("function_execute_missing:") for item in failures)
    assert "default_function_execute_missing:incidents:id" in failures
    assert "default_sequence_usage_missing:audit_logs:sequence" in failures


def test_optional_read_role_does_not_need_admin_writes_but_keeps_rls_and_head_checks():
    snapshot = healthy_snapshot()
    for row in snapshot["tables"]:
        row.update(insert_ok=False, update_ok=False, delete_ok=False)
    assert evaluate(snapshot, require_admin=False)["status"] == "passed"
    table(snapshot, "audit_logs")["relforcerowsecurity"] = False
    assert evaluate(snapshot, require_admin=False)["status"] == "failed"


def test_contract_does_not_require_fixture_cleanup_or_unsupported_retry():
    manifest = gate.contract()
    assert manifest["tables"]["incidents"]["operations"] == ["INSERT", "SELECT", "UPDATE"]
    assert manifest["tables"]["connectors"]["operations"] == ["INSERT", "SELECT", "UPDATE"]
    assert manifest["tables"]["assistant_action_resolutions"]["operations"] == ["INSERT", "SELECT"]
    assert manifest["tables"]["crm_deliveries"]["operations"] == ["INSERT", "SELECT", "UPDATE"]
    assert "Alembic/versions/0066_crm_skip_test_calls.py" in manifest["tables"]["crm_deliveries"]["sources"]
    assert "tenant_policy_audit_log" not in manifest["tables"]
    assert gate.contract(platform_sip=True)["tables"]["tenant_policy_audit_log"]["operations"] == ["INSERT"]
    for item in manifest["tables"].values():
        assert all((gate.BACKEND_ROOT / source).is_file() for source in item["sources"])


@pytest.mark.parametrize("domain,username,password,enabled", [
    (" ", "synthetic", "synthetic", False),
    ("synthetic.invalid", " ", " ", True),
    ("synthetic.invalid", "synthetic", "", False),
])
def test_sip_hook_configuration_matches_existing_seed_truthiness(domain, username, password, enabled):
    assert gate.platform_sip_configured({"PLATFORM_SIP_DOMAIN": domain,
        "PLATFORM_SIP_USERNAME": username, "PLATFORM_SIP_PASSWORD": password}) is enabled


class CatalogConnection:
    def __init__(self):
        self.snapshot = healthy_snapshot()
        self.queries = []
        self.entered = self.exited = False
        self.readonly = None

    def transaction(self, *, readonly):
        self.readonly = readonly
        return self

    async def __aenter__(self):
        self.entered = True

    async def __aexit__(self, *args):
        self.exited = True

    async def fetchrow(self, sql):
        self.queries.append(sql)
        return self.snapshot["role"]

    async def fetch(self, sql, *args):
        assert self.entered and not self.exited
        self.queries.append(sql)
        if "SELECT version_num" in sql:
            return [{"version_num": "test-head"}]
        if sql == gate.TABLE_SQL:
            return self.snapshot["tables"]
        if sql == gate.DEFAULTS_SQL:
            return []
        if sql == gate.SEQUENCES_SQL:
            return []
        return self.snapshot["functions"]


async def test_catalog_port_is_readonly_and_never_reads_customer_rows():
    conn = CatalogConnection()
    snapshot = await gate.inspect_connection(conn, gate.contract())
    assert evaluate(snapshot)["status"] == "passed"
    assert conn.readonly is True and conn.exited
    assert all(sql.lstrip().startswith("SELECT ") for sql in conn.queries)
    assert all("SET ROLE" not in sql and "set_config(" not in sql and "GRANT " not in sql for sql in conn.queries)
    assert not any("FROM incidents" in sql or "FROM audit_logs" in sql or "FROM user_profiles" in sql for sql in conn.queries)


async def test_failed_readonly_confirmation_stops_before_catalog_or_version_reads():
    conn = CatalogConnection()
    conn.snapshot["role"]["read_only"] = False
    with pytest.raises(RuntimeError, match="readonly_not_confirmed"):
        await gate.inspect_connection(conn, gate.contract())
    assert conn.exited and conn.queries == [gate.ROLE_SQL]


async def test_connection_error_report_cannot_expose_dsn_or_credentials(monkeypatch):
    secret = "postgresql://sensitive-user:sensitive-password@private-host/private-db"
    monkeypatch.setattr(gate.asyncpg, "connect", AsyncMock(side_effect=RuntimeError(secret)))
    result = await gate.inspect_dsn(secret, gate.contract(), ["test-head"], require_admin=True)
    assert result["status"] == "failed"
    assert "sensitive" not in json.dumps(result) and "private-host" not in json.dumps(result)


async def test_read_failure_closes_connection_and_does_not_return_partial_pass(monkeypatch):
    conn = CatalogConnection()
    conn.fetch = AsyncMock(side_effect=RuntimeError("catalog failure"))
    conn.close = AsyncMock()
    monkeypatch.setattr(gate.asyncpg, "connect", AsyncMock(return_value=conn))
    result = await gate.inspect_dsn("synthetic", gate.contract(), ["test-head"], require_admin=True)
    assert result["status"] == "failed" and conn.exited
    conn.close.assert_awaited_once_with(timeout=2)
    options = gate.asyncpg.connect.call_args.kwargs
    assert options["timeout"] == 5 and options["server_settings"]["default_transaction_read_only"] == "on"


async def test_primary_and_adapter_share_configured_probe_read_replica_checked_separately(monkeypatch):
    probe = AsyncMock(return_value={"status": "passed"})
    monkeypatch.setattr(gate, "inspect_dsn", probe)
    result = await gate.run_configured(gate.contract(), ["test-head"], {
        "DATABASE_URL": "synthetic-primary", "READ_DATABASE_URL": "synthetic-replica"})
    assert result["status"] == "passed" and not result["read_pool_aliases_primary"]
    assert [(call.args[0], call.kwargs["require_admin"]) for call in probe.await_args_list] == [
        ("synthetic-primary", True), ("synthetic-replica", False)]
    assert "synthetic-primary" not in json.dumps(result)


async def test_missing_primary_never_falls_back_to_repository_development_credentials(monkeypatch):
    probe = AsyncMock()
    monkeypatch.setattr(gate, "inspect_dsn", probe)
    result = await gate.run_configured(gate.contract(), ["test-head"], {})
    assert result["failures"] == ["DATABASE_URL_required"]
    probe.assert_not_called()


def test_offline_contract_does_not_load_env_or_connect(monkeypatch, capsys):
    monkeypatch.setattr(gate.sys, "argv", ["preflight", "--contract"])
    monkeypatch.setattr(gate.asyncpg, "connect", AsyncMock(side_effect=AssertionError("offline")))
    assert gate.main() == 0
    manifest = json.loads(capsys.readouterr().out)
    assert "incidents" in manifest["tables"]
    gate.asyncpg.connect.assert_not_called()
