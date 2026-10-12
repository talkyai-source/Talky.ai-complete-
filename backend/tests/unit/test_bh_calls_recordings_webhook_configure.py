"""POST /webhooks/secure/admin/configure must really provision the HMAC secret."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.api.v1.endpoints import webhooks_secure
from app.core.security.webhook_verification import WebhookSecretManager
from app.infrastructure.connectors import encryption as enc_module


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """configure encrypts the secret at rest (bh2): it needs a real Fernet key."""
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    # Fresh service for this key; monkeypatch restores the previous singleton.
    monkeypatch.setattr(enc_module, "_encryption_service", None)


class _Conn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, query, *args):
        self.executed.append((" ".join(query.split()), args))


def _install(monkeypatch, conn):
    contexts = []

    @asynccontextmanager
    async def acquire(pool, tenant):
        contexts.append((pool, tenant))
        yield conn

    monkeypatch.setattr(webhooks_secure, "acquire_with_tenant", acquire, raising=False)
    manager = WebhookSecretManager()
    monkeypatch.setattr(webhooks_secure, "_webhook_secret_manager", manager)
    return contexts, manager


@pytest.mark.asyncio
async def test_configure_webhook_upserts_with_tenant_scope_and_invalidates_cache(monkeypatch):
    conn = _Conn()
    contexts, manager = _install(monkeypatch, conn)
    tenant = str(uuid4())
    manager._cache[f"{tenant}:goal-achieved"] = ("stale-secret", 9e12)
    pool = object()

    result = await webhooks_secure.configure_webhook(
        request=SimpleNamespace(),
        webhook_name="goal-achieved",
        tenant_id=tenant,
        db_client=SimpleNamespace(pool=pool),
        _admin=None,
    )

    assert contexts == [(pool, tenant)]
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "INSERT INTO webhook_configs" in sql
    assert "ON CONFLICT (tenant_id, webhook_name)" in sql
    assert args[0] == UUID(tenant) and args[1] == "goal-achieved"
    # The stored value is the encrypted form, never the raw secret returned once.
    assert result["secret"]
    assert args[2].startswith("enc:v1:") and result["secret"] not in args[2]
    assert enc_module.get_encryption_service().decrypt(args[2][len("enc:v1:"):]) == result["secret"]
    assert f"{tenant}:goal-achieved" not in manager._cache
    assert "plaintext" not in result["note"].lower()


@pytest.mark.asyncio
async def test_configure_webhook_rejects_a_malformed_tenant_id(monkeypatch):
    conn = _Conn()
    _install(monkeypatch, conn)
    with pytest.raises(HTTPException) as err:
        await webhooks_secure.configure_webhook(
            request=SimpleNamespace(),
            webhook_name="goal-achieved",
            tenant_id="not-a-uuid",
            db_client=SimpleNamespace(pool=object()),
            _admin=None,
        )
    assert err.value.status_code == 400
    assert conn.executed == []
