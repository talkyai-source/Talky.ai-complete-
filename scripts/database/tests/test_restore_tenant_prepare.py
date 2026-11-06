"""Prepare-only tenant recovery: strict synthetic catalog/client, no database I/O."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from uuid import UUID

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "restore_tenant.py"
TENANT = UUID("00000000-0000-4000-8000-000000000001")


@pytest.fixture
def mod(monkeypatch):
    assert SCRIPT.is_file(), "F8-07 has no isolated tenant-restore preparation/catalog manifest tool"
    monkeypatch.syspath_prepend(str(SCRIPT.parent / "database"))
    spec = importlib.util.spec_from_file_location("qa_round3_tenant_restore", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def table(name, columns, *, fks=(), triggers=()):
    primary = "id" if "id" in columns else "event_id" if "event_id" in columns else columns[0]
    return {"table": name, "columns": list(columns), "primary_key": [primary],
            "foreign_keys": list(fks), "triggers": list(triggers),
            "rls_enabled": True, "force_rls": True}


def fk(column, parent, target="id"):
    return {"columns": [column], "parent_table": parent,
            "parent_columns": [target], "deferrable": False}


@pytest.fixture
def catalog():
    return [
        table("tenants", ["id", "name"]),
        table("campaigns", ["id", "tenant_id"], fks=[fk("tenant_id", "tenants")]),
        table("calls", ["id", "tenant_id", "campaign_id"],
              fks=[fk("tenant_id", "tenants"), fk("campaign_id", "campaigns")]),
        table("transcripts", ["id", "call_id", "text"], fks=[fk("call_id", "calls")]),
        table("recordings_s3", ["id", "tenant_id", "call_id", "s3_key", "s3_bucket"],
              fks=[fk("call_id", "calls")]),
        table("user_profiles", ["id", "tenant_id", "email", "password_hash"]),
        table("tenant_users", ["id", "tenant_id", "user_id"], fks=[fk("user_id", "user_profiles")]),
        table("audit_logs", ["event_id", "tenant_id", "previous_hash", "event_hash"]),
        table("audit_chain_state", ["id", "last_event_id"]),
        table("billing_ledger", ["id", "tenant_id", "amount"], triggers=["billing_ledger_immutable"]),
        table("legal_acceptances", ["id", "tenant_id", "user_id"],
              fks=[fk("user_id", "user_profiles")], triggers=["legal_acceptances_immutable"]),
        table("alembic_version", ["version_num"]),
        table("unmapped_extension_state", ["id", "payload"]),
    ]


def test_plan_accounts_for_every_catalog_table_and_topological_parent_order(mod, catalog):
    counts = {row["table"]: {"total_rows": 7, "tenant_rows": 2} for row in catalog}
    result = mod.build_plan(catalog, counts, tenant_id=TENANT, media={"missing": 0, "external": 0})
    assert {row["table"] for row in result["tables"]} == set(counts)
    assert result["catalog_complete"] is True
    order = result["foreign_key_order"]
    assert order.index("tenants") < order.index("campaigns") < order.index("calls") < order.index("transcripts")
    assert result["apply_allowed"] is False
    assert result["status"] == "prepared_only"


def test_shared_identity_global_chain_and_immutable_receipts_are_explicit_not_copied(mod, catalog):
    counts = {row["table"]: {"total_rows": 7, "tenant_rows": 2} for row in catalog}
    result = mod.build_plan(catalog, counts, tenant_id=TENANT, media={"missing": 0, "external": 0})
    by_name = {row["table"]: row for row in result["tables"]}
    assert by_name["user_profiles"]["classification"] == "shared_identity"
    assert by_name["audit_logs"]["classification"] == "global_audit_chain"
    assert by_name["audit_chain_state"]["classification"] == "global_audit_chain"
    assert by_name["billing_ledger"]["classification"] == "immutable_evidence"
    assert by_name["legal_acceptances"]["classification"] == "immutable_evidence"
    assert by_name["unmapped_extension_state"]["classification"] == "unresolved"
    assert result["full_tenant_restore_complete"] is False
    assert {"shared_identity", "global_audit_chain", "immutable_evidence", "unresolved_ownership"} <= set(result["blockers"])


def test_unknown_immutable_trigger_is_not_ignored(mod, catalog):
    catalog.append(table("new_receipts", ["id", "tenant_id"], triggers=["custom_guard"]))
    counts = {row["table"]: {"total_rows": 0, "tenant_rows": 0} for row in catalog}
    result = mod.build_plan(catalog, counts, tenant_id=TENANT, media={"missing": 0, "external": 0})
    row = next(row for row in result["tables"] if row["table"] == "new_receipts")
    assert row["triggers"] == ["custom_guard"]
    assert "unreviewed_triggers" in result["blockers"]


def test_missing_counts_refuses_instead_of_certifying_subset(mod, catalog):
    counts = {row["table"]: {"total_rows": 0, "tenant_rows": 0} for row in catalog[:-1]}
    with pytest.raises(mod.RecoveryError):
        mod.build_plan(catalog, counts, tenant_id=TENANT, media={"missing": 0, "external": 0})


def test_fk_cycles_are_reported_without_inventing_safe_copy_order(mod):
    catalog = [table("tenants", ["id"]),
               table("a", ["id", "tenant_id", "b_id"], fks=[fk("b_id", "b")]),
               table("b", ["id", "tenant_id", "a_id"], fks=[fk("a_id", "a")])]
    counts = {row["table"]: {"total_rows": 1, "tenant_rows": 1} for row in catalog}
    result = mod.build_plan(catalog, counts, tenant_id=TENANT, media={"missing": 0, "external": 0})
    assert set(result["cyclic_tables"]) == {"a", "b"}
    assert "foreign_key_cycle" in result["blockers"]


@pytest.mark.parametrize("name", ["bad;DROP TABLE tenants", 'a"b', "public.calls", ""])
def test_catalog_identifiers_are_validated_before_dynamic_sql(mod, name):
    with pytest.raises(mod.RecoveryError):
        mod.scope_predicate(table(name, ["id", "tenant_id"]), [], TENANT)


def test_direct_and_parent_owned_scopes_always_bind_requested_tenant(mod, catalog):
    direct = mod.scope_predicate(catalog[2], catalog, TENANT)
    child = mod.scope_predicate(catalog[3], catalog, TENANT)
    nullable = mod.scope_predicate(catalog[4], catalog, TENANT)
    for query in (direct, child, nullable):
        assert str(TENANT) in query
        assert "tenant_id" in query
    assert "EXISTS" in child and "calls" in child
    assert "IS NULL" in nullable and "EXISTS" in nullable
    assert str(UUID(int=2)) not in direct + child + nullable


@pytest.mark.parametrize("tenant", ["not-a-uuid", "x' OR TRUE --"])
def test_tenant_identifier_is_not_sql_text(mod, catalog, tenant):
    with pytest.raises((mod.RecoveryError, ValueError)):
        mod.scope_predicate(catalog[2], catalog, tenant)


@pytest.fixture
def payload(tmp_path):
    folder = tmp_path / "payload"
    folder.mkdir()
    dump = folder / "database.dump"
    dump.write_bytes(b"PGDMP-synthetic-only\x00" * 32)
    media = folder / "recordings" / "tenant-a" / "call.wav"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"RIFF synthetic")
    manifest = {"format": 1, "snapshot_time": "2026-11-06T11:00:00+00:00", "files": {
        "database.dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "bytes": dump.stat().st_size},
        "recordings/tenant-a/call.wav": {"sha256": hashlib.sha256(media.read_bytes()).hexdigest(), "bytes": media.stat().st_size},
    }}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def test_media_inventory_accounts_for_legacy_local_missing_and_external(mod, payload):
    rows = [
        {"table": "recordings_s3", "storage": "local", "path": "tenant-a/call.wav"},
        {"table": "recordings", "storage": "local", "path": "tenant-a/missing.wav"},
        {"table": "recordings_s3", "storage": "s3", "path": "private/cloud/key"},
    ]
    result = mod.media_inventory(payload, rows)
    assert result["verified"] == 1 and result["missing"] == 1 and result["external"] == 1
    assert result["complete"] is False
    assert "private/cloud/key" not in json.dumps(result)
    assert "tenant-a/missing.wav" not in json.dumps(result)


@pytest.mark.parametrize("path", ["../escape.wav", "/root/private", "C:/private", "a\\..\\private"])
def test_media_inventory_refuses_escape_paths(mod, payload, path):
    with pytest.raises(mod.RecoveryError):
        mod.media_inventory(payload, [{"table": "recordings", "storage": "local", "path": path}])


def test_actual_inventory_queries_all_tables_readonly_with_explicit_scope_and_rls_failclosed(mod, catalog, payload):
    observed = []
    class InventoryPg:
        def json(self, service, query, *, database=None, readonly=True):
            observed.append(query)
            assert database == "talky_restore_tenant_test" and readonly is True
            if "pg_catalog.pg_class" in query:
                assert "pg_attribute" in query and "pg_constraint" in query and "pg_trigger" in query
                return catalog
            assert "row_security = off" in query
            assert "BEGIN READ ONLY" in query and "ROLLBACK" in query
            if "total_rows" in query:
                names = re.findall(r'FROM public\."([a-z_]+)"', query)
                assert names and names[0] in {row["table"] for row in catalog}
                if names[0] not in {"audit_chain_state", "alembic_version", "unmapped_extension_state"}:
                    assert str(TENANT) in query
                return {"total_rows": 7, "tenant_rows": None if names[0] in {
                    "audit_chain_state", "alembic_version", "unmapped_extension_state"} else 2}
            if "s3_key" in query:
                assert str(TENANT) in query and "recordings_s3" in query
                return [{"table": "recordings_s3", "storage": "local", "path": "tenant-a/call.wav"}]
            raise AssertionError("unexpected inventory SQL: " + query)
    result = mod.inspect_restored(InventoryPg(), object(), "talky_restore_tenant_test", TENANT, payload)
    assert len([query for query in observed if "total_rows" in query]) == len(catalog)
    assert result["media"]["verified"] == 1
    assert result["apply_allowed"] is False
    assert len(result["tables"]) == len(catalog)
    assert "tenant-a/call.wav" not in json.dumps(result)


class PgDouble:
    def __init__(self):
        self.calls = []
        self.cluster = "222222"
        self.exists = False
        self.fail_restore = False
        self.heads = ["0087_bootstrap_manual_parity"]

    def sql(self, service, query, *, database=None, readonly=True):
        self.calls.append(("sql", query, database, readonly))
        if "pg_control_system" in query:
            return self.cluster
        if query.startswith("CREATE DATABASE "):
            assert database is None and readonly is False
            assert '"talky_restore_tenant_test"' in query
            return ""
        raise AssertionError("unexpected SQL: " + query)

    def json(self, service, query, *, database=None, readonly=True):
        self.calls.append(("json", query, database, readonly))
        assert readonly is True
        if "inet_server_addr" in query:
            return {"address": "127.0.0.1", "port": 55439}
        if "pg_database" in query:
            return self.exists
        if "alembic_version" in query:
            assert database == "talky_restore_tenant_test"
            return self.heads
        raise AssertionError("unexpected JSON SQL: " + query)

    def run(self, name, args, service=None, *, readonly=True, **kwargs):
        self.calls.append((name, args, readonly))
        assert name == "pg_restore"
        assert "--no-owner" in args and "--no-privileges" in args and "--single-transaction" in args
        assert readonly is False
        if self.fail_restore:
            raise RuntimeError("SECRET database diagnostic with customer@email.invalid")
        return ""


@pytest.fixture
def restore_args(mod, payload, tmp_path, monkeypatch):
    cfg = tmp_path / "synthetic-pg-service"
    cfg.write_text("[restore]\nhost=127.0.0.1\nport=55439\ndbname=postgres\nuser=restore_operator\nsslmode=disable\n")
    cfg.chmod(0o600)
    service = mod.recovery.Service(cfg, "restore", True)
    pg = PgDouble()
    inspection = {"status": "prepared_only", "catalog_complete": True,
                  "full_tenant_restore_complete": False, "apply_allowed": False,
                  "tables": [], "blockers": ["shared_identity", "global_audit_chain"]}
    observed = []
    def inspect(*args, **kwargs):
        observed.append((args, kwargs))
        return inspection
    monkeypatch.setattr(mod, "inspect_restored", inspect)
    return pg, service, dict(payload=payload, database="talky_restore_tenant_test",
        source_cluster_id="111111", source_endpoint_fingerprint="a" * 64,
        expected_heads=["0087_bootstrap_manual_parity"], tenant_id=TENANT,
        receipt_path=tmp_path / "prepare-receipt.json"), observed


def test_prepare_restores_only_new_isolated_db_and_never_copies_live_rows(mod, restore_args):
    pg, service, kwargs, observed = restore_args
    result = mod.prepare_restore(pg, service, **kwargs)
    assert result["status"] == "prepared_only" and result["apply_allowed"] is False
    assert result["isolated_database_retained"] is True
    assert len(observed) == 1
    sql = " ".join(str(call) for call in pg.calls)
    assert "CREATE DATABASE" in sql and "pg_restore" in sql
    for forbidden in ("DROP DATABASE", "UPDATE ", "DELETE FROM", "INSERT INTO", "ALTER ROLE", "GRANT "):
        assert forbidden not in sql
    assert json.loads(kwargs["receipt_path"].read_text())["full_tenant_restore_complete"] is False


def test_corrupt_payload_is_refused_before_database_contact(mod, restore_args):
    pg, service, kwargs, _ = restore_args
    (kwargs["payload"] / "database.dump").write_bytes(b"truncated")
    with pytest.raises(mod.RecoveryError):
        mod.prepare_restore(pg, service, **kwargs)
    assert pg.calls == []


@pytest.mark.parametrize("database", ["talkyai", "postgres", "talky_restore_x;DROP DATABASE x"])
def test_live_or_injected_database_names_are_refused_before_contact(mod, restore_args, database):
    pg, service, kwargs, _ = restore_args
    kwargs["database"] = database
    with pytest.raises(mod.RecoveryError):
        mod.prepare_restore(pg, service, **kwargs)
    assert pg.calls == []


def test_same_source_cluster_is_refused_before_create(mod, restore_args):
    pg, service, kwargs, _ = restore_args
    pg.cluster = kwargs["source_cluster_id"]
    with pytest.raises(mod.RecoveryError):
        mod.prepare_restore(pg, service, **kwargs)
    assert not any("CREATE DATABASE" in str(call) for call in pg.calls)


def test_existing_isolated_database_is_never_overwritten(mod, restore_args):
    pg, service, kwargs, _ = restore_args
    pg.exists = True
    with pytest.raises(mod.RecoveryError):
        mod.prepare_restore(pg, service, **kwargs)
    assert not any("CREATE DATABASE" in str(call) or call[0] == "pg_restore" for call in pg.calls)


@pytest.mark.parametrize("failure", ["restore", "heads"])
def test_failed_isolated_restore_retains_evidence_without_secret_or_destructive_cleanup(mod, restore_args, failure):
    pg, service, kwargs, observed = restore_args
    if failure == "restore":
        pg.fail_restore = True
    else:
        pg.heads = ["unknown_schema"]
    with pytest.raises(mod.RecoveryError) as caught:
        mod.prepare_restore(pg, service, **kwargs)
    saved = json.loads(kwargs["receipt_path"].read_text())
    assert saved["status"] == "failed" and saved["isolated_database_retained"] is True
    assert not observed
    assert "SECRET" not in json.dumps(saved) + str(caught.value)
    assert not any("DROP DATABASE" in str(call) for call in pg.calls)


def test_no_apply_cli_is_advertised_or_accepted(mod, capsys):
    with pytest.raises(SystemExit) as caught:
        mod.main(["--service-file", "synthetic-never-read.conf", "--service", "restore",
                  "--payload", "synthetic-payload", "--database", "talky_restore_tenant_test",
                  "--tenant-id", str(TENANT), "--source-cluster-id", "111111",
                  "--source-endpoint-fingerprint", "a" * 64,
                  "--expected-head", "0087_bootstrap_manual_parity", "--receipt", "synthetic-receipt.json",
                  "--apply"])
    assert caught.value.code != 0
    assert "--apply" in capsys.readouterr().err
