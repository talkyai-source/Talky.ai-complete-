"""Item04 provider writes: atomic masked receipts, including probe-result writes."""
from __future__ import annotations

from copy import deepcopy
import json
import sys
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.v1.endpoints import telephony_providers as providers
from tests.unit.test_privileged_mutation_audit import (
    ACTOR, CIPHER, PLAIN, TENANT, Audit, AuditUnavailable, Store, invoke,
)


class ProviderStore(Store):
    def __init__(self):
        super().__init__("key")
        self.rows = {
            "credential": {"tenant_id": TENANT, "provider": "twilio", "label": "before",
                           "credentials_encrypted": CIPHER, "from_number": "+12025550123",
                           "status": "inactive", "last_tested_at": None, "last_test_result": None},
            "active_telephony_provider": "twilio",
        }

    async def fetchrow(self, sql, *args):
        query = self.sql(sql)
        if query.startswith("select"):
            assert str(args[0]) == TENANT
            assert "tenant_id" in query or ("from tenants" in query and "id" in query)
            if "from tenants" in query:
                return {"active_telephony_provider": self.rows["active_telephony_provider"]}
            return deepcopy(self.rows["credential"])
        if query.startswith("insert into tenant_telephony_credentials"):
            assert str(args[0]) == TENANT
            self.changed()
            self.rows["credential"] = {"tenant_id": args[0], "provider": args[1], "label": args[2],
                                       "credentials_encrypted": args[3], "from_number": args[4],
                                       "status": "inactive", "last_tested_at": None, "last_test_result": None}
            return deepcopy(self.rows["credential"])
        raise AssertionError(query)

    async def execute(self, sql, *args):
        query = self.sql(sql)
        if query.startswith(("set local", "select set_config")):
            return "SELECT 1"
        if query.startswith("delete from tenant_telephony_credentials"):
            assert "tenant_id" in query and str(args[0]) == TENANT
            self.changed()
            self.rows["credential"] = None
            return "DELETE 1"
        if query.startswith("update tenant_telephony_credentials"):
            assert "tenant_id" in query and str(args[3]) == TENANT
            self.changed()
            self.rows["credential"].update(status=args[0], last_tested_at=args[1], last_test_result=json.loads(args[2]))
            return "UPDATE 1"
        if query.startswith("update tenants"):
            self.changed()
            if "= 'none'" in query:
                assert str(args[0]) == TENANT
                self.rows["active_telephony_provider"] = "none"
            else:
                assert str(args[1]) == TENANT
                self.rows["active_telephony_provider"] = args[0]
            return "UPDATE 1"
        raise AssertionError(query)


def prepare(monkeypatch):
    store = ProviderStore()
    probes = []
    credentials = {"account_sid": "ACsyntheticaccount", "auth_token": PLAIN}
    monkeypatch.setattr(providers, "get_encryption_service", lambda: SimpleNamespace(
        encrypt=lambda value: "new-" + CIPHER, decrypt=lambda value: json.dumps(credentials),
    ))

    class Adapter:
        def __init__(self, **kwargs):
            assert kwargs["auth_token"] == PLAIN

        async def ping_with_detail(self):
            assert store.depth == 0 and not store.locked, "Provider I/O must stay outside the audit transaction"
            probes.append("probe")
            return {"ok": True, "latency_ms": 1, "status_code": 200,
                    "account_status": "active", "friendly_name": "Synthetic"}

    monkeypatch.setitem(sys.modules, "app.infrastructure.telephony.twilio_provider_adapter",
                        SimpleNamespace(TwilioProviderAdapter=Adapter))
    return store, probes, credentials


async def mutate(case, store, audit, credentials):
    if case == "save":
        return await invoke(providers.save_provider, store, audit, provider="twilio",
                            body=providers.ProviderSaveRequest(credentials=credentials, label="after"))
    if case == "delete":
        return await invoke(providers.delete_provider, store, audit, provider="twilio")
    if case == "test":
        return await invoke(providers.test_provider, store, audit, provider="twilio")
    return await invoke(providers.activate_provider, store, audit,
                        body=providers.ProviderActivateRequest(provider="none"))


@pytest.mark.parametrize("case", ["save", "delete", "test", "activate"])
async def test_provider_mutation_and_masked_receipt_commit_together(monkeypatch, case):
    store, probes, credentials = prepare(monkeypatch)
    before = deepcopy(store.rows)
    await mutate(case, store, Audit(store), credentials)
    assert store.rows != before and store.mutations > 0
    assert len(store.receipts) == 1, f"{case}: committed provider write has no transactional receipt"
    receipt = store.receipts[0]
    assert str(receipt["actor_id"]) == ACTOR and str(receipt["tenant_id"]) == TENANT
    assert receipt["before_state"] != receipt["after_state"]
    serialized = json.dumps(receipt, default=str)
    assert PLAIN not in serialized and CIPHER not in serialized
    assert store.order.index("audit_lock") < store.order.index("write") < store.order.index("audit")
    assert probes == (["probe"] if case == "test" else [])
    if case == "delete":
        assert store.rows["credential"] is None and store.rows["active_telephony_provider"] == "none"


@pytest.mark.parametrize("case", ["save", "delete", "test", "activate"])
async def test_provider_audit_failure_rolls_back_write_and_does_not_claim_persistence(monkeypatch, case):
    store, probes, credentials = prepare(monkeypatch)
    before = deepcopy(store.rows)
    with pytest.raises((AuditUnavailable, HTTPException)):
        await mutate(case, store, Audit(store, fail=True), credentials)
    assert store.rows == before and store.mutations == 0 and not store.receipts
    assert "rollback" in store.order
    # A probe cannot be un-sent, but it must never be repeated by audit retries.
    assert probes == (["probe"] if case == "test" else [])
