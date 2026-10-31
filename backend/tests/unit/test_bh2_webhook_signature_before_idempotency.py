"""POST /webhooks/secure/idempotent-example must verify the signature BEFORE any idempotency work.

Before: FastAPI resolved ``Depends(idempotency_dependency)`` ahead of the handler body,
which only then looked up the tenant secret and verified the HMAC. An unauthenticated
caller who forged ``X-Tenant-ID`` and knew a live ``Idempotency-Key`` (and the original
body) got the cached response replayed without any valid signature - and any caller,
signed or not, could take or hold the in-progress lock for the key.

Now the signature is checked by a route-level dependency, which FastAPI resolves
before every parameter dependency, so a request that fails authentication never
reads, replays, locks or stores idempotency state.
"""
from types import SimpleNamespace

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
from fastapi import FastAPI

import app.api.v1.dependencies  # noqa: F401  (resolve the import cycle first)
import app.api.v1.endpoints.webhooks_secure as ws
from app.api.v1.dependencies import get_db_client
from app.core.security import idempotency
from app.core.security.webhook_verification import create_webhook_signature_headers

SECRET = "whsec_bh2_signature_before_idempotency"
TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_TENANT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
KEY = "idem-key-0001"
PATH = "/api/v1/webhooks/secure/idempotent-example"


def _headers(body: bytes, *, tenant=TENANT, key=KEY, signature=None, drop=()):
    headers = create_webhook_signature_headers(body, SECRET)
    if signature is not None:
        headers["X-Webhook-Signature"] = signature
    headers["X-Tenant-ID"] = tenant
    headers["Idempotency-Key"] = key
    headers["Content-Type"] = "application/json"
    for name in drop:
        headers.pop(name, None)
    return headers


@pytest.fixture
async def env(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    commands: list[str] = []
    original = redis.execute_command

    async def spy(*args, **kwargs):
        commands.append(str(args[0]).upper())
        return await original(*args, **kwargs)

    redis.execute_command = spy

    idempotency.reset_idempotency_manager()
    monkeypatch.setattr("app.core.container.get_container",
                        lambda: SimpleNamespace(redis=redis, is_initialized=True))

    async def secret_for(tenant_id, name):
        return SECRET if (tenant_id == TENANT and name == "idempotent_example") else None

    monkeypatch.setattr(ws, "get_webhook_secret_from_db", secret_for)

    app = FastAPI()
    app.include_router(ws.router, prefix="/api/v1")
    app.dependency_overrides[get_db_client] = lambda: None
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client, redis=redis, commands=commands)
    idempotency.reset_idempotency_manager()
    await redis.aclose()


async def test_bad_signature_with_a_cached_key_gets_401_not_the_cached_body(env):
    body = b'{"order":"A-1"}'
    first = await env.client.post(PATH, content=body, headers=_headers(body))
    assert first.status_code == 200 and first.json()["status"] == "processed"

    forged = await env.client.post(PATH, content=body, headers=_headers(body, signature="0" * 64))

    assert forged.status_code == 401, f"cached response replayed without a valid signature: {forged.text}"
    assert "processed" not in forged.text
    assert "idempotent-replay" not in {name.lower() for name in forged.headers}


async def test_unsigned_request_with_a_cached_key_gets_401_not_the_cached_body(env):
    body = b'{"order":"A-1b"}'
    first = await env.client.post(PATH, content=body, headers=_headers(body))
    assert first.status_code == 200

    unsigned = await env.client.post(PATH, content=body, headers=_headers(body, drop=("X-Webhook-Signature",)))

    assert unsigned.status_code == 401, unsigned.text
    assert "processed" not in unsigned.text


@pytest.mark.parametrize(
    "scenario",
    ["forged_signature", "missing_signature", "tenant_without_a_secret", "missing_tenant"],
)
async def test_failed_authentication_never_touches_idempotency_state(env, scenario):
    body = b'{"order":"A-2"}'
    headers = {
        "forged_signature": _headers(body, signature="0" * 64),
        "missing_signature": _headers(body, drop=("X-Webhook-Signature",)),
        "tenant_without_a_secret": _headers(body, tenant=OTHER_TENANT),
        "missing_tenant": _headers(body, drop=("X-Tenant-ID",)),
    }[scenario]

    response = await env.client.post(PATH, content=body, headers=headers)

    assert response.status_code == 401, response.text
    assert env.commands == [], f"idempotency state touched before authentication: {env.commands}"


async def test_valid_signature_processes_once_then_replays(env):
    body = b'{"order":"A-3"}'
    first = await env.client.post(PATH, content=body, headers=_headers(body))
    assert first.status_code == 200 and first.json() == {"status": "processed", "data": {"order": "A-3"}}

    again = await env.client.post(PATH, content=body, headers=_headers(body))
    assert again.status_code == 200
    assert again.headers["idempotent-replay"] == "true"
    assert again.json()["detail"]["status"] == "processed"


async def test_valid_signature_with_a_different_body_for_a_used_key_is_a_conflict(env):
    first_body = b'{"order":"A-4"}'
    assert (await env.client.post(PATH, content=first_body, headers=_headers(first_body))).status_code == 200

    other_body = b'{"order":"A-4-CHANGED"}'
    conflict = await env.client.post(PATH, content=other_body, headers=_headers(other_body))

    assert conflict.status_code == 409
    assert "different request" in conflict.json()["detail"]


async def test_handler_failure_after_authentication_releases_the_lock_for_a_retry(env):
    garbage = b"this is not json"
    failed = await env.client.post(PATH, content=garbage, headers=_headers(garbage))
    assert failed.status_code == 500

    keys = [k async for k in env.redis.scan_iter("idempotency:lock:*")]
    assert keys == [], "the failed request must not leave its lock behind"

    good = b'{"order":"A-5"}'
    retry = await env.client.post(PATH, content=good, headers=_headers(good))
    assert retry.status_code == 200 and retry.json()["status"] == "processed"
