"""bughunt billing-money-credentials-2 - the admin api-keys routes write
tenant_ai_credentials, so they must drop the resolver cache for that tenant
(otherwise a revoked key keeps being served from memory)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import api_keys
from app.domain.services.credential_resolver import CredentialResolver

TENANT = "11111111-1111-4111-8111-111111111111"


class _Res:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def insert(self, payload):
        self._rows.append(dict(payload))
        return self

    def update(self, _payload):
        return self

    def eq(self, *_a):
        return self

    def execute(self):
        return _Res(list(self._rows))


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _Q(self.rows)


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        CredentialResolver,
        "invalidate_cache",
        staticmethod(lambda **kw: seen.append(kw)),
    )
    return seen


def _admin():
    return SimpleNamespace(tenant_id=TENANT)


@pytest.mark.asyncio
async def test_revoke_invalidates_resolver_cache(calls):
    db = _DB([{"id": "k1", "tenant_id": TENANT, "provider": "openai"}])
    await api_keys.revoke_api_key(key_id="k1", current_user=_admin(), db_client=db)
    assert calls == [{"tenant_id": TENANT}]


@pytest.mark.asyncio
async def test_failed_revoke_does_not_invalidate(calls):
    db = _DB([])
    with pytest.raises(HTTPException):
        await api_keys.revoke_api_key(key_id="nope", current_user=_admin(), db_client=db)
    assert calls == []


@pytest.mark.asyncio
async def test_create_invalidates_resolver_cache(calls):
    db = _DB([])
    await api_keys.create_api_key(
        request=api_keys.CreateApiKeyRequest(name="k", provider="openai"),
        current_user=_admin(),
        db_client=db,
    )
    assert calls == [{"tenant_id": TENANT}]
