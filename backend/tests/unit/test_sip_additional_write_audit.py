"""Item04: assignments, probe persistence and deletion are audited writes too."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import HTTPException
import pytest

from app.api.v1.endpoints.telephony_sip import trunks
from tests.unit.test_privileged_mutation_audit import (
    ACTOR, CIPHER, PLAIN, RESOURCE, TENANT, Audit, AuditUnavailable, Store, actor, invoke,
)
from tests.unit.test_sip_privileged_audit import TRUNK, TransactionalSip, seed
from tests.unit.test_telephony_sip_api import _make_request
from tests.unit import test_trunk_delete_flow as deletion


class AssignmentStore(Store):
    def __init__(self):
        super().__init__("key")
        self.rows = {"calling_rules": {"pool_trunk": {"id": RESOURCE}},
                     "calling_config": {"trunk": {"id": RESOURCE}},
                     "id": RESOURCE, "tenant_id": TENANT, "direction": "outbound", "status": "draft"}

    async def fetchrow(self, sql, *args):
        query = self.sql(sql)
        if "from tenants" in query:
            assert str(args[0]) == TENANT
        else:
            assert "tenant_id" in query and str(args[1]) == TENANT
            assert "calling_config" in query, "Audit pre-state must be selected from the database"
        return deepcopy(self.rows)

    async def execute(self, sql, *args):
        query = self.sql(sql)
        if query.startswith(("set local", "select set_config")):
            return "SELECT 1"
        self.changed()
        if query.startswith("update tenants"):
            assert str(args[0]) == TENANT
            if "- 'pool_trunk'" in query:
                self.rows["calling_rules"].pop("pool_trunk", None)
            else:
                self.rows["calling_rules"]["pool_trunk"] = deepcopy(args[1])
        elif query.startswith("update campaigns"):
            assert "tenant_id" in query and str(args[1]) == TENANT
            if "- 'trunk'" in query:
                self.rows["calling_config"].pop("trunk", None)
            else:
                self.rows["calling_config"]["trunk"] = deepcopy(args[2])
        else:
            raise AssertionError(query)
        return "UPDATE 1"


async def assignment(monkeypatch, case, *, fail):
    store = AssignmentStore()
    original = deepcopy(store.rows)
    target = {"id": TRUNK, "caller_id": "+12025550123", "auth_username": "synthetic",
              "trunk_name": "synthetic-trunk", "direction": "both"}
    monkeypatch.setattr(trunks, "_fetch_pool_trunk", AsyncMock(return_value=target))
    monkeypatch.setattr(trunks, "_fetch_assignable_trunk", AsyncMock(return_value=target))
    monkeypatch.setattr(trunks, "evaluate_trunk_runtime", lambda *a, **kw: SimpleNamespace(ready=True, detail="ready"))
    family, operation = case.split("_")
    identifier = UUID(TRUNK) if operation == "set" else None
    if family == "pool":
        fn = trunks.set_pool_assignment
        body = trunks.PoolAssignmentBody(pool_trunk_id=identifier)
    else:
        fn = trunks.set_campaign_trunk_assignment
        body = trunks.CampaignTrunkBody(campaign_id=UUID(RESOURCE), trunk_id=identifier)
    try:
        result = await invoke(fn, store, Audit(store, fail=fail), body=body,
                              request=_make_request("/assignment"), current_user=actor())
    except (AuditUnavailable, HTTPException) as exc:
        result = exc
    return store, original, result


@pytest.mark.parametrize("case", ["pool_set", "pool_clear", "campaign_set", "campaign_clear"])
async def test_assignment_change_has_same_transaction_before_after_receipt(monkeypatch, case):
    store, original, result = await assignment(monkeypatch, case, fail=False)
    assert getattr(result, "status_code", 200) < 400
    assert store.rows != original
    assert len(store.receipts) == 1, f"{case}: assignment changed without a transactional audit receipt"
    receipt = store.receipts[0]
    assert str(receipt["actor_id"]) == ACTOR and str(receipt["tenant_id"]) == TENANT
    assert receipt["before_state"] != receipt["after_state"]


@pytest.mark.parametrize("case", ["pool_set", "pool_clear", "campaign_set", "campaign_clear"])
async def test_assignment_audit_failure_does_not_commit_or_report_success(monkeypatch, case):
    store, original, result = await assignment(monkeypatch, case, fail=True)
    assert isinstance(result, AuditUnavailable) or getattr(result, "status_code", 200) >= 400
    assert store.rows == original and not store.receipts and store.mutations == 0


class ProbeStore(TransactionalSip):
    async def fetchrow(self, sql, *args):
        if "FROM tenant_sip_trunks" in sql and "WHERE id = $1 AND tenant_id = $2" in sql:
            assert str(args[1]) == TENANT
            return deepcopy(self.trunks[str(args[0])])
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if "UPDATE tenant_sip_trunks" in sql and "last_tested_at = $1" in sql:
            assert str(args[3]) == TENANT
            self.track(sql)
            self.trunks[str(args[2])].update(last_tested_at=args[0], last_test_result=deepcopy(args[1]))
            return "UPDATE 1"
        return await super().execute(sql, *args)


@pytest.mark.parametrize("fail", [False, True])
async def test_trunk_probe_receipt_is_atomic_without_replaying_external_probe(monkeypatch, fail):
    conn = ProbeStore()
    seed(conn, "trunk_update")
    before = conn.snapshot()
    monkeypatch.setattr(trunks, "_count_trunk_test", AsyncMock(return_value=0))
    monkeypatch.setattr(trunks, "get_encryption_service", lambda: SimpleNamespace(decrypt=lambda value: PLAIN))

    async def probe(**kwargs):
        assert conn.depth == 0 and not conn.locked
        conn.external.append("probe")
        return {"ok": True, "latency_ms": 1, "target": "synthetic", "error": None, "detail": "ready",
                "sip_code": "200", "timeout_code": None, "steps": [], "verdict": "ready", "status": "connected"}

    monkeypatch.setattr(trunks, "run_trunk_test", probe)
    if fail:
        with pytest.raises(AuditUnavailable):
            await invoke(trunks.test_sip_trunk, conn, Audit(conn, fail=True), trunk_id=UUID(TRUNK),
                         request=_make_request("/test"), current_user=actor())
        assert conn.snapshot() == before
    else:
        await invoke(trunks.test_sip_trunk, conn, Audit(conn), trunk_id=UUID(TRUNK),
                     request=_make_request("/test"), current_user=actor())
        assert len(conn.receipts) == 1, "Probe result committed without a transactional receipt"
        receipt = conn.receipts[0]
        assert receipt["before_state"] != receipt["after_state"]
        assert PLAIN not in str(receipt) and CIPHER not in str(receipt)
    assert conn.external == ["probe"]


class DeleteConn(deletion.Conn):
    def __init__(self, db):
        super().__init__(db)
        self.depth = 0
        self.locked = False
        self.order = []
        self.receipts = []

    def is_in_transaction(self):
        return self.depth > 0

    def _record(self, sql, args):
        sql = super()._record(sql, args)
        if sql.startswith(("INSERT", "UPDATE", "DELETE")):
            self.order.append("write")
        return sql


@pytest.mark.parametrize("fail", [False, True])
async def test_delete_audit_refusal_precedes_external_removal_and_rolls_back(monkeypatch, fail):
    from app.infrastructure.telephony import pjsip_config_generator

    db = deletion.FakeDB()
    trunk_id = db.add_trunk(active=True)
    conn = DeleteConn(db)
    external = []

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        assert tenant_id == deletion.TENANT
        before = deepcopy((db.trunks, db.campaigns, db.policies, db.tenants, db.assignments, db.configs, db.audit, conn.receipts))
        conn.depth += 1
        try:
            yield conn
        except BaseException:
            (db.trunks, db.campaigns, db.policies, db.tenants, db.assignments, db.configs, db.audit, conn.receipts) = before
            raise
        finally:
            conn.depth -= 1

    async def remove(*args, **kwargs):
        conn.order.append("external")
        external.append("remove")

    monkeypatch.setattr(trunks, "acquire_with_tenant", scoped)
    monkeypatch.setattr(trunks, "_claim_idempotency", AsyncMock(return_value=("new", None, None)))
    monkeypatch.setattr(trunks, "_store_idempotency_result", AsyncMock())
    monkeypatch.setattr(trunks, "_enforce_ws_i_quota", AsyncMock(return_value=None))
    monkeypatch.setattr(pjsip_config_generator, "remove_trunk_config", remove)
    monkeypatch.setattr(trunks, "_restore_trunk_config_file", AsyncMock())
    kwargs = {"trunk_id": trunk_id, "request": _make_request("/delete"),
              "idempotency_key": "delete-audit", "current_user": actor(tenant=deletion.TENANT)}
    if fail:
        with pytest.raises(AuditUnavailable):
            await invoke(trunks.delete_sip_trunk, conn, Audit(conn, fail=True), **kwargs)
        assert trunk_id in db.trunks and not external and not conn.receipts
    else:
        await invoke(trunks.delete_sip_trunk, conn, Audit(conn), **kwargs)
        assert trunk_id not in db.trunks and external == ["remove"]
        assert len(conn.receipts) == 1
        assert conn.order.index("audit") < conn.order.index("external")
