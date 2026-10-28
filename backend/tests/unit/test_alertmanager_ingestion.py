"""Actual ASGI/middleware checks; synthetic persistence, no network or receivers."""
import asyncio
import copy
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.requests import Request

from app.api.v1.dependencies import require_platform_admin
from app.api.v1.endpoints import alertmanager as am
from app.api.v1.endpoints.admin.health import alerts
from app.core import tenant_middleware, session_security_middleware
from app.core.jwt_security import JWTValidationError
from app.core.security.csrf import CSRFMiddleware
from app.core.error_handlers import register_error_handlers

TOKEN = "isolated-AD08-test-token-" + "x" * 32
PATH = "/api/v1/webhooks/alertmanager"


def payload():
    start = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    return {"version": "4", "status": "firing", "truncatedAlerts": 0,
        "receiver": "default", "groupKey": "{}:{alertname=WorkerDown}", "alerts": [{
            "status": "firing", "fingerprint": "0123456789abcdef", "startsAt": start,
            "endsAt": "0001-01-01T00:00:00Z", "labels": {"alertname": "WorkerDown", "severity": "warning"},
            "annotations": {"summary": "Worker heartbeat absent", "description": "Isolated test"}}]}


@pytest_asyncio.fixture
async def harness(monkeypatch):
    settings = SimpleNamespace(alertmanager_ingest_token=SecretStr(TOKEN), alertmanager_source_id="isolated-alertmanager")
    monkeypatch.setattr(am, "get_settings", lambda: settings)
    saved = AsyncMock(return_value={"accepted_alerts": 1, "episode_count": 1, "incident_ids": ["synthetic-id"]})
    db = Mock(return_value=SimpleNamespace(pool=object()))
    monkeypatch.setattr(am, "persist_notification", saved)
    monkeypatch.setattr(am, "get_db_client", db)
    jwt = Mock(side_effect=JWTValidationError("Invalid dedicated token for user authentication", 401))
    session = AsyncMock(side_effect=AssertionError("Webhook must not resolve a user session"))
    session_db = Mock(side_effect=AssertionError("Webhook must not acquire the user-session database"))
    monkeypatch.setattr(tenant_middleware, "decode_and_validate_token", jwt)
    monkeypatch.setattr(session_security_middleware, "validate_session", session)
    monkeypatch.setattr(session_security_middleware, "get_db_pool_from_container", session_db)
    def no_network(*args, **kwargs):
        raise AssertionError("Network forbidden in synthetic ingestion tests")
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(am.router, prefix="/api/v1")
    app.include_router(alerts.router, prefix="/api/v1/admin")
    app.add_middleware(session_security_middleware.SessionSecurityMiddleware)
    app.add_middleware(tenant_middleware.TenantMiddleware)
    app.add_middleware(CSRFMiddleware, allowed_origins=["https://admin.example.invalid"])
    # Install after Windows has created its event-loop socket pair, and undo
    # before the loop closes. The app itself cannot open network connections.
    with monkeypatch.context() as network:
        network.setattr(socket.socket, "connect", no_network)
        network.setattr(socket, "create_connection", no_network)
        yield SimpleNamespace(app=app, settings=settings, saved=saved, db=db, jwt=jwt, session=session, session_db=session_db)


async def post(harness, value=None, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=harness.app), base_url="https://test.invalid") as client:
        return await client.post(PATH, json=payload() if value is None else value,
            headers={"authorization": f"Bearer {TOKEN}"} if headers is None else headers)


async def test_real_middlewares_allow_dedicated_bearer_without_origin_or_user_session(harness):
    result = await post(harness, headers={"authorization": f"Bearer {TOKEN}",
        "cookie": f"{session_security_middleware.SESSION_COOKIE_NAME}=not-a-user-session"})
    assert result.status_code == 200
    harness.saved.assert_awaited_once()
    harness.jwt.assert_not_called(); harness.session.assert_not_awaited()
    harness.session_db.assert_not_called()
    assert harness.saved.call_args.args[1] == "isolated-alertmanager"


@pytest.mark.parametrize("headers,status", [
    ({}, 403), ({"origin": "https://admin.example.invalid"}, 401),
    ({"authorization": "Bearer wrong"}, 401),
    ({"authorization": "Bearer internal-service-secret"}, 401),
    ({"origin": "https://admin.example.invalid", "x-internal-service-token": TOKEN}, 401),
    ({"origin": "https://admin.example.invalid", "cookie": f"{session_security_middleware.SESSION_COOKIE_NAME}=pretend-admin"}, 401),
])
async def test_cookie_origin_or_other_tokens_do_not_authorize_ingestion(harness, headers, status):
    assert (await post(harness, headers=headers)).status_code == status
    harness.saved.assert_not_awaited(); harness.db.assert_not_called()


async def test_duplicate_authorization_headers_rejected(harness):
    result = await post(harness, headers=[("authorization", f"Bearer {TOKEN}"), ("authorization", f"Bearer {TOKEN}")])
    assert result.status_code == 401
    harness.db.assert_not_called()


async def test_ingestion_token_cannot_read_admin_settings(harness):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=harness.app), base_url="https://test.invalid") as client:
        result = await client.get("/api/v1/admin/alerts/settings", headers={"authorization": f"Bearer {TOKEN}"})
    assert result.status_code == 401
    harness.jwt.assert_called_once()


@pytest.mark.parametrize("value", [None, "short", "x" * 257, "x" * 31 + " ", "x" * 31 + "\n", "x" * 31 + "\x00", "x" * 31 + "é"])
async def test_missing_or_invalid_config_fails_closed(harness, value):
    harness.settings.alertmanager_ingest_token = None if value is None else SecretStr(value)
    assert (await post(harness)).status_code == 503
    harness.db.assert_not_called()


async def test_untrusted_source_id_cannot_override_configuration(harness):
    value = payload(); value["source_system"] = "attacker-selected"; value["alerts"][0]["labels"]["source"] = "another-source"
    assert (await post(harness, value)).status_code == 200
    assert harness.saved.call_args.args[1] == "isolated-alertmanager"
    harness.settings.alertmanager_source_id = "unsafe source"
    assert (await post(harness)).status_code == 503


@pytest.mark.parametrize("case", ["missing-truncated", "truncated", "coerced-truncated", "too-many", "empty", "old-version", "bad-severity", "numeric-start", "naive-start", "future-start", "resolved-no-end", "resolved-before-start", "huge-summary", "huge-description", "bad-label", "many-labels"])
async def test_invalid_complete_batch_rejected_before_any_persistence(harness, case):
    value = payload(); alert = value["alerts"][0]
    if case == "missing-truncated": del value["truncatedAlerts"]
    elif case == "truncated": value["truncatedAlerts"] = 1
    elif case == "coerced-truncated": value["truncatedAlerts"] = False
    elif case == "too-many": value["alerts"] = [copy.deepcopy(alert) for _ in range(101)]
    elif case == "empty": value["alerts"] = []
    elif case == "old-version": value["version"] = "3"
    elif case == "bad-severity": alert["labels"]["severity"] = "page-everybody"
    elif case == "numeric-start": alert["startsAt"] = 1234567890
    elif case == "naive-start": alert["startsAt"] = "2026-01-01T00:00:00"
    elif case == "future-start": alert["startsAt"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    elif case == "resolved-no-end": alert["status"] = "resolved"; del alert["endsAt"]
    elif case == "resolved-before-start": alert["status"] = "resolved"
    elif case == "huge-summary": alert["annotations"]["summary"] = "s" * 513
    elif case == "huge-description": alert["annotations"]["description"] = "d" * 4097
    elif case == "bad-label": alert["labels"]["private"] = "not-echoed\x00-secret"
    elif case == "many-labels": alert["labels"].update({f"label{i}": "x" for i in range(65)})
    result = await post(harness, value)
    assert result.status_code == 422 and "not-echoed" not in result.text
    harness.db.assert_not_called(); harness.saved.assert_not_awaited()


async def test_supported_batch_boundaries_and_urls_never_fetched(harness):
    value = payload(); value["alerts"] *= 100
    value["externalURL"] = "http://169.254.169.254/private"
    value["alerts"][0]["generatorURL"] = "https://receiver.example.invalid/never-fetch"
    assert (await post(harness, value)).status_code == 200
    assert len(harness.saved.call_args.args[2].alerts) == 100


async def test_body_limit_content_type_and_malformed_json(harness):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=harness.app), base_url="https://test.invalid") as client:
        auth = {"authorization": f"Bearer {TOKEN}"}
        assert (await client.post(PATH, content="x", headers=auth)).status_code == 415
        auth["content-type"] = "application/json"
        assert (await client.post(PATH, content="{broken", headers=auth)).status_code == 422
        assert (await client.post(PATH, content="x" * (am.MAX_BODY_BYTES + 1), headers=auth)).status_code == 413
    harness.db.assert_not_called()


async def test_stream_limit_without_content_length():
    chunks = iter([b"x" * am.MAX_BODY_BYTES, b"x"])
    async def receive():
        return {"type": "http.request", "body": next(chunks), "more_body": True}
    request = Request({"type": "http", "method": "POST", "path": PATH, "headers": []}, receive)
    with pytest.raises(Exception) as error:
        await am._read_body(request)
    assert error.value.status_code == 413


async def test_body_timeout_does_not_start_database_work(harness, monkeypatch):
    monkeypatch.setattr(am, "_read_body", AsyncMock(side_effect=asyncio.TimeoutError))
    assert (await post(harness)).status_code == 503
    harness.db.assert_not_called()


@pytest.mark.parametrize("where", ["container", "persistence"])
async def test_unavailable_storage_returns_retryable_no_false_success(harness, where):
    target = harness.db if where == "container" else harness.saved
    target.side_effect = RuntimeError("synthetic failure with private data")
    result = await post(harness)
    assert result.status_code == 503
    assert "private data" not in result.text and "retry the complete" in result.text


async def test_alert_settings_cannot_claim_saved_thresholds_or_receiver_delivery(harness):
    harness.app.dependency_overrides[require_platform_admin] = lambda: SimpleNamespace(id="synthetic-admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=harness.app), base_url="https://test.invalid") as client:
        first = await client.get("/api/v1/admin/alerts/settings")
        assert first.status_code == 200
        assert first.json()["editable"] is False and first.json()["notification_delivery"] == "unverified"
        result = await client.put("/api/v1/admin/alerts/settings", json={"slack_notifications": True, "slack_webhook_url": "http://169.254.169.254"}, headers={"origin": "https://admin.example.invalid"})
        assert result.status_code == 409
        assert (await client.get("/api/v1/admin/alerts/settings")).json() == first.json()
    assert await alerts.send_slack_alert("x", "http://169.254.169.254") is False
    assert await alerts.send_email_alert("x", "x", ["no-send@example.invalid"]) is False
    with pytest.raises(RuntimeError, match="unavailable"):
        await alerts.trigger_alert("x", "warning", "x", object())


def test_router_registration_is_on_existing_api_router():
    # Parse the actual router file without importing unrelated provider SDKs.
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[2] / "app/api/v1/routes.py").read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.ImportFrom) and node.module == "app.api.v1.endpoints.alertmanager" for node in tree.body)
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router"
        and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "alertmanager_router" for node in ast.walk(tree))
    app = FastAPI(); app.include_router(am.router, prefix="/api/v1")
    schema = app.openapi(); operation = schema["paths"][PATH]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert operation["security"] == [{"AlertmanagerIngestBearer": []}]
    assert schema["components"]["securitySchemes"]["AlertmanagerIngestBearer"]["scheme"] == "bearer"
    assert body["properties"]["alerts"]["maxItems"] == 100
    assert "truncatedAlerts" in body["required"] and body["properties"]["truncatedAlerts"]["maximum"] == 0
    assert "fingerprint" in body["properties"]["alerts"]["items"]["properties"]
    assert "$ref" not in str(body)  # child schema is actually inlined
    assert {"200", "401", "413", "415", "422", "503"} <= operation["responses"].keys()
    assert "error" in operation["responses"]["503"]["content"]["application/json"]["schema"]["required"]
