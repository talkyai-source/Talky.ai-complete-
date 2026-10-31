"""calls-recordings-analytics-6 residual, against real Postgres: webhook secrets are encrypted at rest.

Runs the real configure INSERT ... ON CONFLICT and the verifier's real SELECT
against ``webhook_configs`` (the table as database/schema/baseline_2026-06-02.sql
defines it, without the FK to tenants). Skipped unless BH_PG_DSN points at a
scratch Postgres server (any database on it will do), e.g.

    BH_PG_DSN=postgresql://postgres@127.0.0.1:53392/postgres

The table lives in a throw-away schema that the test creates and drops.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import asyncpg
import pytest
from cryptography.fernet import Fernet

import app.api.v1.dependencies  # noqa: F401  (resolve the import cycle first)
import app.api.v1.endpoints.webhooks_secure as ws
from app.core.security.webhook_verification import (
    WebhookSecretManager,
    create_webhook_signature_headers,
    verify_signature,
)
from app.infrastructure.connectors import encryption as enc_module

DSN = os.getenv("BH_PG_DSN", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="BH_PG_DSN not set (needs a scratch Postgres)")

SCHEMA = f"bh2_webhook_{uuid.uuid4().hex[:8]}"
NAME = "call_goal_achieved"

DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.webhook_configs (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid,
    webhook_name text NOT NULL,
    secret_key text NOT NULL,
    signature_algorithm text DEFAULT 'hmac-sha256',
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT webhook_configs_tenant_id_webhook_name_key UNIQUE (tenant_id, webhook_name)
);
"""


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(
        DSN, min_size=1, max_size=3, server_settings={"search_path": f"{SCHEMA},public"}
    )
    async with p.acquire() as c:
        await c.execute(DDL)
    try:
        yield p
    finally:
        async with p.acquire() as c:
            await c.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
        await p.close()


@pytest.fixture
def tenant():
    return uuid.uuid4()


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(enc_module, "_encryption_service", None)
    monkeypatch.setattr(ws, "_webhook_secret_manager", WebhookSecretManager())


async def _stored(pool, tenant):
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT secret_key FROM webhook_configs WHERE tenant_id = $1 AND webhook_name = $2",
            tenant, NAME,
        )
        row_text = await conn.fetchval(
            "SELECT webhook_configs::text FROM webhook_configs WHERE tenant_id = $1", tenant
        )
    return value, row_text


async def _configure(pool, tenant):
    return await ws.configure_webhook(
        request=SimpleNamespace(),
        webhook_name=NAME,
        tenant_id=str(tenant),
        db_client=SimpleNamespace(pool=pool),
        _admin=None,
    )


async def test_configure_writes_ciphertext_and_the_verifier_reads_it_back(pool, tenant, encryption_key):
    result = await _configure(pool, tenant)

    stored, row_text = await _stored(pool, tenant)
    assert stored.startswith("enc:v1:")
    assert result["secret"] not in stored and result["secret"] not in row_text

    secret = await WebhookSecretManager().get_secret(str(tenant), NAME, pool)  # real SELECT + decrypt
    assert secret == result["secret"]
    payload = b'{"call_id":"c1"}'
    headers = create_webhook_signature_headers(payload, secret)
    assert verify_signature(
        payload, headers["X-Webhook-Signature"], secret, headers["X-Webhook-Timestamp"]
    ) == (True, None)
    # another tenant's id finds nothing: the SELECT carries its own tenant predicate
    assert await WebhookSecretManager().get_secret(str(uuid.uuid4()), NAME, pool) is None


async def test_legacy_plaintext_row_still_reads_and_reconfigure_upgrades_it(pool, tenant, encryption_key):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO webhook_configs (tenant_id, webhook_name, secret_key) VALUES ($1, $2, $3)",
            tenant, NAME, "legacy-plaintext-secret",
        )

    assert await WebhookSecretManager().get_secret(str(tenant), NAME, pool) == "legacy-plaintext-secret"

    result = await _configure(pool, tenant)  # ON CONFLICT ... DO UPDATE replaces the plaintext
    stored, _ = await _stored(pool, tenant)
    assert stored.startswith("enc:v1:") and "legacy-plaintext-secret" not in stored
    assert await WebhookSecretManager().get_secret(str(tenant), NAME, pool) == result["secret"]


async def test_row_encrypted_under_another_key_is_not_configured(pool, tenant, encryption_key, monkeypatch):
    await _configure(pool, tenant)

    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())  # key lost/rotated away
    monkeypatch.setattr(enc_module, "_encryption_service", None)

    assert await WebhookSecretManager().get_secret(str(tenant), NAME, pool) is None
