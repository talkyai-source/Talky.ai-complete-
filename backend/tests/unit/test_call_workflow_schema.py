"""Deployment and export contracts for durable call workflow data (F1-26)."""
from importlib import import_module

import httpx
import pytest
from fastapi import FastAPI

from tests.unit.test_call_workflow import harness, request, BODY
from tests.unit.test_round3_dsar_export import harness as dsar_harness, scope, read_rows, TENANT, OTHER, uid, ACTOR


def test_workflow_migration_has_tenant_parent_integrity_and_safe_downgrade(monkeypatch):
    migration = import_module("Alembic.versions.0086_call_workflows")
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    sql = " ".join(" ".join(statements).split())
    assert migration.down_revision == "0085_legal_acceptance"
    assert len(migration.revision) <= 32
    assert "CREATE TABLE IF NOT EXISTS call_workflows" in sql
    assert "PRIMARY KEY (tenant_id, call_id)" in sql
    assert "FOREIGN KEY (tenant_id, call_id) REFERENCES calls (tenant_id, id)" in sql
    assert "ON DELETE CASCADE" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql and "FORCE ROW LEVEL SECURITY" in sql
    assert "WITH CHECK" in sql and "CREATE POLICY" in sql
    assert "to_regclass(" in sql and "::regclass" not in sql
    assert "CONCURRENTLY" not in sql
    for field, bound in (("notes", 4000), ("contact", 160), ("interest", 1000), ("next_step", 1000)):
        assert f"char_length({field}) <= {bound}" in sql
    statements.clear()
    migration.downgrade()
    sql = " ".join(statements)
    assert "IF EXISTS (SELECT 1 FROM call_workflows" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP TABLE")


async def test_workflow_is_reachable_through_real_api_router(harness):
    from app.api.v1.routes import api_router
    fixture_app, _, _ = harness
    mounted = FastAPI()
    mounted.include_router(api_router, prefix="/api/v1")
    mounted.dependency_overrides.update(fixture_app.dependency_overrides)
    response = await request(mounted, "PUT", BODY)
    assert response.status_code == 200, response.text
    response = await request(mounted, "GET")
    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["notes"] == BODY["notes"]


async def test_workflow_notes_are_exported_only_for_selected_person_and_tenant(dsar_harness, tmp_path):
    mod, service, conn, *_ = dsar_harness
    conn.rows["call_workflows"] = [
        {"tenant_id": TENANT, "call_id": uid(11), "notes": "selected person's note"},
        {"tenant_id": TENANT, "call_id": uid(12), "notes": "another person's note"},
        {"tenant_id": OTHER, "call_id": uid(13), "notes": "another tenant's note"},
    ]
    result = await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "workflow.zip")
    _, rows, _ = read_rows(result.path)
    assert rows.get("call_workflows") == [conn.rows["call_workflows"][0]]
