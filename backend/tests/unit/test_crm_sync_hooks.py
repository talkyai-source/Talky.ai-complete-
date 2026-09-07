"""The two teardown hooks that hand a finished call to the CRM sync, and the
resolver's per-provider lookup they depend on."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from app.domain.services.call_service import CallService  # noqa: E402
from app.services import connector_resolver  # noqa: E402
from app.services.scripts import call_transcript_persister as persister  # noqa: E402

TENANT = "55555555-5555-5555-5555-555555555555"


def test_call_service_hands_the_settled_call_to_the_crm_sync(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.crm_sync_service.schedule_crm_sync",
        lambda call_id, *, tenant_id=None, reason="settlement": captured.update(
            call_id=call_id, tenant_id=tenant_id, reason=reason
        ),
    )
    monkeypatch.setattr("app.core.security.tenant_isolation.get_current_tenant_id", lambda: TENANT)
    CallService._schedule_crm_sync("call-1")
    assert captured == {"call_id": "call-1", "tenant_id": TENANT, "reason": "settlement"}


def test_call_service_crm_hook_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.crm_sync_service.schedule_crm_sync", boom)
    CallService._schedule_crm_sync("call-2")  # must not propagate


def test_handle_call_status_source_calls_the_crm_hook_after_durable_settlement():
    """Guard: the hook sits after the durability check and before event logging."""
    import inspect

    src = inspect.getsource(CallService.handle_call_status)
    assert "self._schedule_crm_sync(call_uuid)" in src
    assert src.index("if not result.durable:") < src.index("self._schedule_crm_sync(call_uuid)")
    assert src.index("self._schedule_crm_sync(call_uuid)") < src.index("Day 1: Event logging")


def test_persister_runs_the_crm_sync_after_the_summary(monkeypatch):
    order = []

    async def fake_generate(pool, tenant_id, call_id):
        order.append(("summary", tenant_id, call_id))
        return {"headline": "ok"}

    async def fake_sync(call_id, *, tenant_id=None, reason="settlement"):
        order.append(("crm", tenant_id, call_id, reason))

    monkeypatch.setattr(persister, "generate_and_store", fake_generate)
    monkeypatch.setattr("app.services.crm_sync_service.run_crm_sync", fake_sync)
    asyncio.get_event_loop().run_until_complete(persister._safe_generate(object(), TENANT, "call-3"))
    assert order == [("summary", TENANT, "call-3"), ("crm", TENANT, "call-3", "summary")]


def test_persister_still_syncs_when_the_summary_fails(monkeypatch):
    seen = []

    async def failing_generate(pool, tenant_id, call_id):
        raise RuntimeError("groq 429")

    async def fake_sync(call_id, *, tenant_id=None, reason="settlement"):
        seen.append(call_id)

    monkeypatch.setattr(persister, "generate_and_store", failing_generate)
    monkeypatch.setattr("app.services.crm_sync_service.run_crm_sync", fake_sync)
    asyncio.get_event_loop().run_until_complete(persister._safe_generate(object(), TENANT, "call-4"))
    assert seen == ["call-4"]


# ---------------------------------------------------------------------------
# Resolver: provider filter + config hand-off
# ---------------------------------------------------------------------------

def _resp(data=None, error=None):
    return SimpleNamespace(data=data, error=error)


class _Query:
    def __init__(self, db, table):
        self.db, self.table_name, self.filters = db, table, []

    def select(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val)); return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self.db.calls.append((self.table_name, list(self.filters)))
        return self.db.responses[self.table_name].pop(0)


class FakeDB:
    def __init__(self, responses):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls = []

    def table(self, name):
        return _Query(self, name)


class _Connector:
    provider_name = "salesforce"

    def __init__(self, tenant_id, connector_id):
        self.tenant_id, self.connector_id = tenant_id, connector_id
        self.applied = None
        self.token = None

    def apply_config(self, config):
        self.applied = config

    async def set_access_token(self, token):
        self.token = token


def test_resolver_filters_by_provider_and_applies_persisted_config(monkeypatch):
    from cryptography.fernet import Fernet

    from app.infrastructure.connectors import encryption as enc_module

    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    enc_module.reset_encryption_service()
    monkeypatch.setattr(connector_resolver, "get_encryption_service", enc_module.get_encryption_service)
    enc = enc_module.get_encryption_service()
    db = FakeDB({
        "connectors": [_resp([{"id": "c-sf", "provider": "salesforce", "status": "active",
                               "created_at": "2026-09-07", "config": {"instance_url": "https://acme.my.salesforce.com"}}])],
        "connector_accounts": [_resp([{"id": "acc-1", "access_token_encrypted": enc.encrypt("AT"),
                                       "refresh_token_encrypted": enc.encrypt("RT"),
                                       "token_expires_at": "2999-01-01T00:00:00+00:00", "last_refreshed_at": None}])],
    })
    monkeypatch.setattr(
        connector_resolver.ConnectorFactory, "create",
        classmethod(lambda cls, provider, tenant_id, connector_id: _Connector(tenant_id, connector_id)),
    )
    connector, cid, provider = asyncio.get_event_loop().run_until_complete(
        connector_resolver.resolve_active_connector(db, TENANT, "crm", provider="salesforce")
    )
    assert cid == "c-sf" and provider == "salesforce"
    assert connector.applied == {"instance_url": "https://acme.my.salesforce.com"}
    assert connector.token == "AT"
    assert ("provider", "salesforce") in db.calls[0][1]
    assert ("type", "crm") in db.calls[0][1]
    enc_module.reset_encryption_service()


def test_list_active_connector_providers_dedupes_newest_first():
    db = FakeDB({"connectors": [_resp([
        {"id": "1", "provider": "salesforce", "status": "active", "created_at": "3"},
        {"id": "2", "provider": "hubspot", "status": "active", "created_at": "2"},
        {"id": "3", "provider": "salesforce", "status": "active", "created_at": "1"},
    ])]})
    assert connector_resolver.list_active_connector_providers(db, TENANT, "crm") == ["salesforce", "hubspot"]


def test_list_active_connector_providers_surfaces_lookup_errors():
    db = FakeDB({"connectors": [_resp(None, error="rls denied")]})
    with pytest.raises(connector_resolver.ConnectorLookupError):
        connector_resolver.list_active_connector_providers(db, TENANT, "crm")
