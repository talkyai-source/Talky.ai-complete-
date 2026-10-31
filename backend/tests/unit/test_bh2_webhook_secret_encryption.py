"""calls-recordings-analytics-6 residual: webhook HMAC secrets are encrypted at rest.

Before: ``POST /webhooks/secure/admin/configure`` wrote the raw HMAC secret into
``webhook_configs.secret_key`` and ``WebhookSecretManager.get_secret`` returned
that column verbatim, so a database read (backup, replica, SQL injection, a
support query) disclosed every tenant's webhook signing secret.

Now the stored form is ``enc:v1:<Fernet token>`` using the project's existing
connector encryption service (``CONNECTOR_ENCRYPTION_KEY``). Legacy plaintext
rows keep verifying (one WARNING, no secret material); a value that cannot be
decrypted is "not configured" (401 + ERROR log); configure fails closed with 503
when the key is missing or unusable and never stores plaintext.
"""
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException

import app.api.v1.dependencies  # noqa: F401  (resolve the import cycle first)
import app.api.v1.endpoints.webhooks_secure as ws
import app.core.security.webhook_verification as wv
from app.api.v1.dependencies import get_db_client
from app.core.security.api_security import rate_limit_dependency
from app.core.security.webhook_verification import (
    WebhookSecretManager,
    create_webhook_signature_headers,
)
from app.infrastructure.connectors import encryption as enc_module
from app.infrastructure.connectors.encryption import TokenEncryptionService

TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_TENANT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
NAME = "call_goal_achieved"
PREFIX = "enc:v1:"
INTERNAL_TOKEN = "bh2-internal-token"
VERIFY_LOGGER = "app.core.security.webhook_verification"


class _FakeWebhookConfigs:
    """In-memory webhook_configs behind the exact SQL contract the code uses."""

    def __init__(self):
        self.rows: dict[tuple[str, str], str] = {}
        self.executed: list[tuple[str, tuple]] = []
        self.reads = 0

    async def execute(self, query, *args):
        sql = " ".join(query.split())
        self.executed.append((sql, args))
        assert "INSERT INTO webhook_configs" in sql and "ON CONFLICT (tenant_id, webhook_name)" in sql
        tenant_uuid, name, stored = args
        self.rows[(str(tenant_uuid), name)] = stored

    async def fetchrow(self, query, tenant_id, name):
        sql = " ".join(query.split())
        assert "WHERE tenant_id = $1 AND webhook_name = $2" in sql and "is_active = TRUE" in sql
        self.reads += 1
        stored = self.rows.get((str(tenant_id), name))
        return None if stored is None else {"secret_key": stored}


class _CallService:
    def __init__(self):
        self.calls: list[dict] = []

    async def mark_goal_achieved(self, tenant_id, call_id):
        self.calls.append({"tenant_id": tenant_id, "call_id": call_id})
        return {"message": "Goal marked as achieved", "call_id": call_id}


def _fresh_encryption_service(monkeypatch):
    """Drop the singleton so the next use re-reads the env; monkeypatch restores the old one."""
    monkeypatch.setattr(enc_module, "_encryption_service", None)


@pytest.fixture
def enc_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", key)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    _fresh_encryption_service(monkeypatch)
    return key


@pytest.fixture
def db(monkeypatch):
    conn = _FakeWebhookConfigs()

    @asynccontextmanager
    async def acquire(pool, tenant, **_kw):
        yield conn

    monkeypatch.setattr(ws, "acquire_with_tenant", acquire)
    monkeypatch.setattr(wv, "acquire_with_tenant", acquire)
    monkeypatch.setattr(ws, "_webhook_secret_manager", WebhookSecretManager())
    return conn


@pytest.fixture
async def client(monkeypatch, db):
    call_service = _CallService()
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, redis=None, db_pool=object(), call_service=call_service),
    )
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)
    app = FastAPI()
    app.include_router(ws.router, prefix="/api/v1")
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=object())
    app.dependency_overrides[rate_limit_dependency] = lambda: None  # process-wide limiter state is not under test
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.call_service = call_service
        yield c


async def _configure_direct(db, tenant=TENANT, name=NAME):
    return await ws.configure_webhook(
        request=SimpleNamespace(),
        webhook_name=name,
        tenant_id=tenant,
        db_client=SimpleNamespace(pool=object()),
        _admin=None,
    )


async def _post_goal_achieved(client, secret, tenant=TENANT, call_id="call-1"):
    payload = ('{"call_id":"%s"}' % call_id).encode()
    headers = create_webhook_signature_headers(payload, secret)
    headers["X-Tenant-ID"] = tenant
    return await client.post("/api/v1/webhooks/secure/call/goal-achieved", content=payload, headers=headers)


# --------------------------------------------------------------------------
# configure: ciphertext at rest
# --------------------------------------------------------------------------
async def test_configure_stores_versioned_ciphertext_never_the_raw_secret(enc_key, db):
    result = await _configure_direct(db)

    stored = db.rows[(TENANT, NAME)]
    assert stored.startswith(PREFIX), f"secret_key stored without the versioned prefix: {stored[:12]!r}"
    assert result["secret"] not in stored
    sql, args = db.executed[0]
    assert args == (UUID(TENANT), NAME, stored), "the INSERT must carry the ciphertext, not the raw secret"
    # the project's own connector encryption service reads it back
    assert enc_module.get_encryption_service().decrypt(stored[len(PREFIX):]) == result["secret"]
    assert result["secret"] and "plaintext" not in result["note"].lower()


async def test_reconfigure_replaces_a_legacy_plaintext_row_with_ciphertext(enc_key, db):
    db.rows[(TENANT, NAME)] = "legacy-plaintext-secret"
    result = await _configure_direct(db)
    stored = db.rows[(TENANT, NAME)]
    assert stored.startswith(PREFIX) and "legacy-plaintext-secret" not in stored
    assert result["secret"] not in stored


async def test_configure_drops_the_cached_secret_so_the_new_one_takes_effect(enc_key, db):
    manager = ws.get_webhook_secret_manager()
    manager._cache[f"{TENANT}:{NAME}"] = ("stale-secret", 9e12)
    result = await _configure_direct(db)
    assert f"{TENANT}:{NAME}" not in manager._cache
    assert await manager.get_secret(TENANT, NAME, object()) == result["secret"]


# --------------------------------------------------------------------------
# configure: fail closed
# --------------------------------------------------------------------------
@pytest.mark.parametrize("scenario", ["key_missing", "key_blank", "key_invalid", "production_key_missing"])
async def test_configure_fails_closed_with_503_and_stores_nothing(monkeypatch, db, scenario):
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    if scenario == "key_missing":
        monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    elif scenario == "key_blank":
        monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", "   ")
    elif scenario == "key_invalid":
        monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", "not-a-fernet-key")
    else:
        monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
    _fresh_encryption_service(monkeypatch)

    with pytest.raises(HTTPException) as err:
        await _configure_direct(db)

    assert err.value.status_code == 503
    assert "CONNECTOR_ENCRYPTION_KEY" in err.value.detail
    assert db.executed == [] and db.rows == {}, "nothing may be stored when the secret cannot be encrypted"


async def test_configure_route_answers_503_and_no_secret_when_the_key_is_missing(client, monkeypatch, db):
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    _fresh_encryption_service(monkeypatch)

    response = await client.post(
        "/api/v1/webhooks/secure/admin/configure",
        params={"webhook_name": NAME, "tenant_id": TENANT},
        headers={"X-Internal-Service-Token": INTERNAL_TOKEN},
    )

    assert response.status_code == 503, response.text
    assert "secret" not in response.json() and db.rows == {}


async def test_configure_route_still_rejects_a_malformed_tenant_before_touching_the_key(client, monkeypatch, db):
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    _fresh_encryption_service(monkeypatch)

    response = await client.post(
        "/api/v1/webhooks/secure/admin/configure",
        params={"webhook_name": NAME, "tenant_id": "not-a-uuid"},
        headers={"X-Internal-Service-Token": INTERNAL_TOKEN},
    )

    assert response.status_code == 400


# --------------------------------------------------------------------------
# verification path
# --------------------------------------------------------------------------
async def test_signed_request_verifies_against_the_encrypted_row(enc_key, client, db):
    configured = await client.post(
        "/api/v1/webhooks/secure/admin/configure",
        params={"webhook_name": NAME, "tenant_id": TENANT},
        headers={"X-Internal-Service-Token": INTERNAL_TOKEN},
    )
    assert configured.status_code == 200, configured.text
    secret = configured.json()["secret"]
    assert db.rows[(TENANT, NAME)].startswith(PREFIX) and secret not in db.rows[(TENANT, NAME)]

    ok = await _post_goal_achieved(client, secret)
    assert ok.status_code == 200, ok.text
    assert client.call_service.calls == [{"tenant_id": TENANT, "call_id": "call-1"}]

    bad = await _post_goal_achieved(client, secret + "x")
    assert bad.status_code == 401
    assert len(client.call_service.calls) == 1


async def test_decrypted_secret_is_cached_so_the_row_is_read_once(enc_key, client, db):
    result = await _configure_direct(db)
    for _ in range(3):
        assert (await _post_goal_achieved(client, result["secret"])).status_code == 200
    assert db.reads == 1


async def test_legacy_plaintext_row_still_verifies_and_warns_once_without_the_secret(enc_key, client, db, caplog):
    legacy = "legacy-plaintext-hmac-secret"
    db.rows[(TENANT, NAME)] = legacy
    with caplog.at_level(logging.WARNING, logger=VERIFY_LOGGER):
        first = await _post_goal_achieved(client, legacy)
        second = await _post_goal_achieved(client, legacy)  # served from the cache

    assert first.status_code == 200 and second.status_code == 200
    warnings = [r for r in caplog.records if r.name == VERIFY_LOGGER and r.levelno == logging.WARNING
                and "plaintext" in r.getMessage().lower()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    for record in caplog.records:
        assert legacy not in record.getMessage()


def _other_key_ciphertext():
    other = TokenEncryptionService(key=Fernet.generate_key().decode(), old_keys=[])
    return PREFIX + other.encrypt("some-secret")


@pytest.mark.parametrize(
    "stored",
    [PREFIX, PREFIX + "not-a-fernet-token", _other_key_ciphertext()],
    ids=["empty_payload", "garbage", "wrong_key"],
)
async def test_undecryptable_value_is_not_configured_with_an_error_log(enc_key, client, db, caplog, stored):
    db.rows[(TENANT, NAME)] = stored
    with caplog.at_level(logging.ERROR, logger=VERIFY_LOGGER):
        response = await _post_goal_achieved(client, "anything")

    assert response.status_code == 401
    assert response.json()["detail"] == "Webhook not configured"
    assert client.call_service.calls == []
    errors = [r for r in caplog.records if r.name == VERIFY_LOGGER and r.levelno == logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in caplog.records]
    text = errors[0].getMessage()
    assert TENANT in text and NAME in text
    assert enc_key not in text and (stored[len(PREFIX):] or "<empty>") not in text
    # an unreadable value is not cached as a secret: fixing the row takes effect immediately
    db.rows[(TENANT, NAME)] = "repaired-legacy-secret"
    assert (await _post_goal_achieved(client, "repaired-legacy-secret")).status_code == 200


async def test_other_tenants_row_is_never_consulted(enc_key, client, db):
    result = await _configure_direct(db)
    response = await _post_goal_achieved(client, result["secret"], tenant=OTHER_TENANT)
    assert response.status_code == 401


async def test_rows_encrypted_under_a_rotated_out_key_still_verify(monkeypatch, client, db):
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", old_key)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    _fresh_encryption_service(monkeypatch)
    result = await _configure_direct(db)

    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", new_key)
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEYS_OLD", old_key)
    _fresh_encryption_service(monkeypatch)
    ws.get_webhook_secret_manager()._cache.clear()

    assert (await _post_goal_achieved(client, result["secret"])).status_code == 200
