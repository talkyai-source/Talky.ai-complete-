"""Focused regressions for connector health and Gmail error handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import connectors as connector_endpoints
from app.infrastructure.assistant.tools.inbox import _email_provider_error, read_emails
from app.infrastructure.connectors.base import ConnectorProviderError, OAuthTokens
from app.infrastructure.connectors.email.base import EmailMessage
from app.infrastructure.connectors.email.gmail import _gmail_error_from_response
from app.infrastructure.connectors.email.gmail import GmailConnector
from app.services import connector_resolver


def _response(data=None, error=None):
    return SimpleNamespace(data=data, error=error)


class _Query:
    def __init__(self, db: "_ScriptedDB", table: str):
        self.db = db
        self.table_name = table
        self.operation = "select"

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, *_args, **_kwargs):
        self.operation = "update"
        return self

    def insert(self, *_args, **_kwargs):
        self.operation = "insert"
        return self

    def delete(self, *_args, **_kwargs):
        self.operation = "delete"
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def neq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        self.db.operations.append((self.table_name, self.operation))
        return self.db.responses[self.table_name].pop(0)


class _ScriptedDB:
    def __init__(self, **responses):
        self.responses = {table: list(values) for table, values in responses.items()}
        self.operations = []

    def table(self, name: str):
        return _Query(self, name)


class _Encryption:
    def decrypt(self, value):
        values = {
            "encrypted-access": "access-token",
            "encrypted-refresh": "refresh-token",
        }
        if value not in values:
            raise ValueError("cannot decrypt")
        return values[value]

    def encrypt(self, value):
        return f"encrypted:{value}"


def _active_connector_response():
    return _response(
        data=[
            {
                "id": "connector-1",
                "provider": "gmail",
                "status": "active",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
    )


def _account_response(*, expires_at, refresh="encrypted-refresh"):
    return _response(
        data=[
            {
                "id": "account-1",
                "access_token_encrypted": "encrypted-access",
                "refresh_token_encrypted": refresh,
                "token_expires_at": expires_at,
                "last_refreshed_at": "2026-01-01T00:00:00Z",
            }
        ]
    )


@pytest.mark.asyncio
async def test_resolver_refreshes_inside_safety_margin_and_checks_writeback(monkeypatch):
    expires_soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    db = _ScriptedDB(
        connectors=[_active_connector_response()],
        connector_accounts=[
            _account_response(expires_at=expires_soon),
            _response(data=[{"id": "account-1"}]),
        ],
    )
    connector = SimpleNamespace(
        provider_name="gmail",
        refresh_tokens=AsyncMock(
            return_value=OAuthTokens(
                access_token="new-access",
                refresh_token="new-refresh",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        ),
        set_access_token=AsyncMock(),
    )
    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: _Encryption())
    monkeypatch.setattr(connector_resolver.ConnectorFactory, "create", lambda **_kwargs: connector)

    resolved, _, _ = await connector_resolver.resolve_active_connector(db, "tenant-1", "email")

    assert resolved is connector
    connector.refresh_tokens.assert_awaited_once_with("refresh-token")
    connector.set_access_token.assert_awaited_once_with("new-access")
    assert db.operations[-1] == ("connector_accounts", "update")


@pytest.mark.asyncio
async def test_resolver_expired_without_refresh_token_requires_reconnect(monkeypatch):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db = _ScriptedDB(
        connectors=[_active_connector_response()],
        connector_accounts=[_account_response(expires_at=expired, refresh="")],
    )
    connector = SimpleNamespace(provider_name="gmail")
    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: _Encryption())
    monkeypatch.setattr(connector_resolver.ConnectorFactory, "create", lambda **_kwargs: connector)

    with pytest.raises(connector_resolver.ConnectorNotConnectedError, match="cannot be refreshed"):
        await connector_resolver.resolve_active_connector(db, "tenant-1", "email")


@pytest.mark.asyncio
async def test_resolver_writeback_failure_is_temporary_not_reconnect(monkeypatch):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db = _ScriptedDB(
        connectors=[_active_connector_response()],
        connector_accounts=[
            _account_response(expires_at=expired),
            _response(error="database unavailable"),
        ],
    )
    connector = SimpleNamespace(
        provider_name="gmail",
        refresh_tokens=AsyncMock(return_value=OAuthTokens(access_token="new-access")),
    )
    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: _Encryption())
    monkeypatch.setattr(connector_resolver.ConnectorFactory, "create", lambda **_kwargs: connector)

    with pytest.raises(connector_resolver.ConnectorLookupError) as caught:
        await connector_resolver.resolve_active_connector(db, "tenant-1", "email")

    assert "temporary lookup error" in caught.value.message
    assert "reconnect" not in caught.value.message.lower()


@pytest.mark.asyncio
async def test_resolver_preserves_non_auth_refresh_category(monkeypatch):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db = _ScriptedDB(
        connectors=[_active_connector_response()],
        connector_accounts=[_account_response(expires_at=expired)],
    )
    provider_error = ConnectorProviderError(
        provider="gmail",
        operation="refresh_tokens",
        category="rate_limit",
        message="quota",
        status_code=429,
    )
    connector = SimpleNamespace(
        provider_name="gmail",
        refresh_tokens=AsyncMock(side_effect=provider_error),
    )
    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: _Encryption())
    monkeypatch.setattr(connector_resolver.ConnectorFactory, "create", lambda **_kwargs: connector)

    with pytest.raises(ConnectorProviderError) as caught:
        await connector_resolver.resolve_active_connector(db, "tenant-1", "email")

    assert caught.value.category == "rate_limit"


def test_gmail_errors_are_structured_by_status_and_token_reason():
    permission = _gmail_error_from_response(
        httpx.Response(
            403, json={"error": {"message": "API disabled", "status": "PERMISSION_DENIED"}}
        ),
        "list_emails",
    )
    invalid_grant = _gmail_error_from_response(
        httpx.Response(400, json={"error": "invalid_grant"}),
        "refresh_tokens",
        token_endpoint=True,
    )
    invalid_client = _gmail_error_from_response(
        httpx.Response(401, json={"error": "invalid_client"}),
        "refresh_tokens",
        token_endpoint=True,
    )
    rate_limit = _gmail_error_from_response(
        httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}},
        ),
        "list_emails",
    )

    assert permission.category == "permission"
    assert invalid_grant.category == "authentication"
    assert invalid_client.category == "configuration"
    assert rate_limit.category == "rate_limit"


@pytest.mark.parametrize(
    ("category", "expected_code"),
    [
        ("permission", "email_permission_denied"),
        ("rate_limit", "email_rate_limited"),
        ("temporary", "email_temporary_error"),
        ("configuration", "email_configuration_error"),
    ],
)
def test_non_auth_provider_failures_never_advise_reconnect(category, expected_code):
    result = _email_provider_error(
        ConnectorProviderError(
            provider="gmail",
            operation="list_emails",
            category=category,
            message="provider detail",
            status_code=403,
        )
    )

    assert result["error_code"] == expected_code
    assert "reconnect" not in result["error"].lower()
    assert "email_required" not in result


def test_python310_asyncio_timeout_is_classified_as_timeout():
    result = _email_provider_error(asyncio.TimeoutError())

    assert result["error_code"] == "email_timeout"


@pytest.mark.asyncio
async def test_read_emails_retries_one_401_after_forced_refresh(monkeypatch):
    rejected = ConnectorProviderError(
        provider="gmail",
        operation="list_emails",
        category="authentication",
        message="invalid credentials",
        status_code=401,
    )
    old = SimpleNamespace(list_emails=AsyncMock(side_effect=rejected))
    refreshed = SimpleNamespace(
        list_emails=AsyncMock(
            return_value=[EmailMessage(id="m-1", subject="Hello", body="Preview")]
        )
    )
    resolver = AsyncMock(
        side_effect=[
            (old, "connector-1", "gmail"),
            (refreshed, "connector-1", "gmail"),
        ]
    )
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)

    result = await read_emails("tenant-1", SimpleNamespace(), max_results=5)

    assert result["success"] is True
    assert result["count"] == 1
    assert resolver.await_args_list[1].kwargs["force_refresh"] is True
    old.list_emails.assert_awaited_once()
    refreshed.list_emails.assert_awaited_once()


@pytest.mark.asyncio
async def test_unrecoverable_401_downgrades_connector_status(monkeypatch):
    rejected = ConnectorProviderError(
        provider="gmail",
        operation="list_emails",
        category="authentication",
        message="invalid credentials",
        status_code=401,
    )
    old = SimpleNamespace(list_emails=AsyncMock(side_effect=rejected))
    resolver = AsyncMock(
        side_effect=[
            (old, "connector-1", "gmail"),
            connector_resolver.ConnectorNotConnectedError(
                "email",
                "Authorization expired",
                connector_id="connector-1",
                provider_confirmed=True,
            ),
        ]
    )
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    db = _ScriptedDB(
        connector_accounts=[_response(data=[{"id": "account-1"}])],
        connectors=[_response(data=[{"id": "connector-1"}])],
    )

    result = await read_emails("tenant-1", db, max_results=5)

    assert result["success"] is False
    assert result["email_required"] is True
    assert result["error_code"] == "email_not_connected"
    assert ("connector_accounts", "update") in db.operations
    assert ("connectors", "update") in db.operations


@pytest.mark.asyncio
async def test_local_connector_failure_does_not_expire_database_status(monkeypatch):
    resolver = AsyncMock(
        side_effect=connector_resolver.ConnectorNotConnectedError(
            "email",
            "Stored credentials could not be decrypted",
            connector_id="connector-1",
        )
    )
    marker = AsyncMock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    monkeypatch.setattr(
        "app.infrastructure.assistant.tools.inbox._mark_email_authorization_expired",
        marker,
    )

    result = await read_emails("tenant-1", SimpleNamespace(), max_results=5)

    assert result["error_code"] == "email_not_connected"
    marker.assert_not_called()


@pytest.mark.asyncio
async def test_401_then_same_connector_missing_refresh_marks_expired(monkeypatch):
    rejected = ConnectorProviderError(
        provider="gmail",
        operation="list_emails",
        category="authentication",
        message="invalid access token",
        status_code=401,
    )
    old = SimpleNamespace(list_emails=AsyncMock(side_effect=rejected))
    resolver = AsyncMock(
        side_effect=[
            (old, "connector-1", "gmail"),
            connector_resolver.ConnectorNotConnectedError(
                "email",
                "No refresh token",
                connector_id="connector-1",
                reason="refresh_unavailable",
            ),
        ]
    )
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    db = _ScriptedDB(
        connector_accounts=[_response(data=[{"id": "account-1"}])],
        connectors=[_response(data=[{"id": "connector-1"}])],
    )

    result = await read_emails("tenant-1", db, max_results=5)

    assert result["error_code"] == "email_not_connected"
    assert ("connector_accounts", "update") in db.operations
    assert ("connectors", "update") in db.operations


def test_status_health_rejects_missing_gmail_read_scope(monkeypatch):
    monkeypatch.setattr(connector_endpoints, "get_encryption_service", lambda: _Encryption())
    account = {
        "access_token_encrypted": "encrypted-access",
        "refresh_token_encrypted": "encrypted-refresh",
        "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
    }

    status, message = connector_endpoints._active_account_health("gmail", account)

    assert status == "error"
    assert "permissions" in message.lower()


def test_status_health_rejects_undecryptable_credentials(monkeypatch):
    class _BrokenEncryption:
        def decrypt(self, _value):
            raise ValueError("wrong key")

    monkeypatch.setattr(
        connector_endpoints, "get_encryption_service", lambda: _BrokenEncryption()
    )
    account = {
        "access_token_encrypted": "ciphertext",
        "refresh_token_encrypted": "refresh-ciphertext",
        "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "scopes": list(_CallbackConnector.oauth_scopes),
    }

    status, message = connector_endpoints._active_account_health("gmail", account)

    assert status == "error"
    assert "reconnect" in message.lower()


def test_status_health_requires_gmail_refresh_token(monkeypatch):
    monkeypatch.setattr(connector_endpoints, "get_encryption_service", lambda: _Encryption())
    account = {
        "access_token_encrypted": "encrypted-access",
        "refresh_token_encrypted": "",
        "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "scopes": list(_CallbackConnector.oauth_scopes),
    }

    status, message = connector_endpoints._active_account_health("gmail", account)

    assert status == "error"
    assert "refreshed" in message.lower()


@pytest.mark.asyncio
async def test_resolver_never_falls_back_to_older_connector_identity(monkeypatch):
    db = _ScriptedDB(
        connectors=[
            _response(
                data=[
                    {
                        "id": "connector-new",
                        "provider": "gmail",
                        "status": "active",
                        "created_at": "2026-01-02T00:00:00Z",
                    },
                    {
                        "id": "connector-old",
                        "provider": "gmail",
                        "status": "active",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                ]
            )
        ],
        connector_accounts=[
            _response(data=[{
                "id": "account-new",
                "access_token_encrypted": "corrupt",
                "refresh_token_encrypted": "encrypted-refresh",
                "token_expires_at": None,
            }]),
        ],
    )
    connector = SimpleNamespace(set_access_token=AsyncMock())
    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: _Encryption())
    monkeypatch.setattr(connector_resolver.ConnectorFactory, "create", lambda **_kwargs: connector)

    with pytest.raises(connector_resolver.ConnectorNotConnectedError) as caught:
        await connector_resolver.resolve_active_connector(db, "tenant-1", "email")

    assert caught.value.connector_id == "connector-new"
    assert db.responses["connector_accounts"] == []
    connector.set_access_token.assert_not_awaited()


class _CallbackConnector:
    connector_type = "email"
    oauth_scopes = [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
    ]

    def __init__(self, tokens: OAuthTokens, *, profile_error: Exception | None = None):
        self.exchange_code = AsyncMock(return_value=tokens)
        self.set_access_token = AsyncMock()
        self.get_profile = AsyncMock(
            side_effect=profile_error,
            return_value={"emailAddress": "owner@example.com"},
        )


@pytest.mark.asyncio
async def test_oauth_callback_refuses_missing_gmail_scope_before_token_store(monkeypatch):
    state_data = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "provider": "gmail",
        "redirect_uri": "https://api.example.test/api/v1/connectors/callback",
        "code_verifier": "verifier",
        "connector_id": "connector-1",
    }
    manager = SimpleNamespace(validate_state=AsyncMock(return_value=state_data))
    connector = _CallbackConnector(
        OAuthTokens(
            access_token="access",
            refresh_token="refresh",
            scope="https://www.googleapis.com/auth/gmail.send",
        )
    )
    db = _ScriptedDB(
        connectors=[
            _response(
                data={
                    "id": "connector-1",
                    "type": "email",
                    "provider": "gmail",
                    "status": "pending",
                }
            ),
            _response(data=[{"id": "connector-1"}]),
        ]
    )
    monkeypatch.setattr(connector_endpoints, "get_oauth_state_manager", lambda: manager)
    monkeypatch.setattr(connector_endpoints.ConnectorFactory, "create", lambda **_kwargs: connector)
    monkeypatch.setattr(
        "app.core.security.tenant_isolation.set_current_tenant_id",
        lambda _tenant_id: None,
    )

    response = await connector_endpoints.oauth_callback(
        Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        state="state",
        code="code",
        error=None,
        db_client=db,
    )

    assert "error=insufficient_scope" in response.headers["location"]
    assert ("connector_accounts", "insert") not in db.operations
    connector.get_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_callback_refuses_gmail_when_live_capability_probe_fails(monkeypatch):
    state_data = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "provider": "gmail",
        "redirect_uri": "https://api.example.test/api/v1/connectors/callback",
        "code_verifier": "verifier",
        "connector_id": "connector-1",
    }
    manager = SimpleNamespace(validate_state=AsyncMock(return_value=state_data))
    connector = _CallbackConnector(
        OAuthTokens(
            access_token="access",
            refresh_token="refresh",
            scope=" ".join(_CallbackConnector.oauth_scopes),
        ),
        profile_error=ConnectorProviderError(
            provider="gmail",
            operation="get_profile",
            category="permission",
            message="Gmail API disabled",
            status_code=403,
        ),
    )
    db = _ScriptedDB(
        connectors=[
            _response(
                data={
                    "id": "connector-1",
                    "type": "email",
                    "provider": "gmail",
                    "status": "pending",
                }
            ),
            _response(data=[{"id": "connector-1"}]),
        ]
    )
    monkeypatch.setattr(connector_endpoints, "get_oauth_state_manager", lambda: manager)
    monkeypatch.setattr(connector_endpoints.ConnectorFactory, "create", lambda **_kwargs: connector)
    monkeypatch.setattr(
        "app.core.security.tenant_isolation.set_current_tenant_id",
        lambda _tenant_id: None,
    )

    response = await connector_endpoints.oauth_callback(
        Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        state="state",
        code="code",
        error=None,
        db_client=db,
    )

    assert "error=capability_check_failed" in response.headers["location"]
    assert ("connector_accounts", "insert") not in db.operations


@pytest.mark.asyncio
async def test_oauth_callback_activates_new_connector_before_retiring_old(monkeypatch):
    state_data = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "provider": "gmail",
        "redirect_uri": "https://api.example.test/api/v1/connectors/callback",
        "code_verifier": "verifier",
        "connector_id": "connector-new",
    }
    manager = SimpleNamespace(validate_state=AsyncMock(return_value=state_data))
    connector = _CallbackConnector(
        OAuthTokens(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            # OAuth 2.0 may omit scope when it is unchanged from the request.
            scope=None,
        )
    )
    db = _ScriptedDB(
        connectors=[
            _response(
                data={
                    "id": "connector-new",
                    "type": "email",
                    "provider": "gmail",
                    "status": "pending",
                }
            ),
            _response(data=[{"id": "connector-new", "status": "active"}]),
            _response(
                data=[
                    {"id": "connector-new", "created_at": "2026-01-02T00:00:00Z"},
                    {"id": "connector-old", "created_at": "2026-01-01T00:00:00Z"},
                ]
            ),
            _response(data=[{"id": "connector-old", "status": "revoked"}]),
        ],
        connector_accounts=[
            _response(data=[{"id": "account-new"}]),
            _response(data=[]),
            _response(data=[{"id": "account-old"}]),
        ],
    )
    monkeypatch.setattr(connector_endpoints, "get_oauth_state_manager", lambda: manager)
    monkeypatch.setattr(connector_endpoints.ConnectorFactory, "create", lambda **_kwargs: connector)
    monkeypatch.setattr(connector_endpoints, "get_encryption_service", lambda: _Encryption())
    monkeypatch.setattr(
        "app.core.security.tenant_isolation.set_current_tenant_id",
        lambda _tenant_id: None,
    )

    response = await connector_endpoints.oauth_callback(
        Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        state="state",
        code="code",
        error=None,
        db_client=db,
    )

    assert "status=success" in response.headers["location"]
    assert db.operations == [
        ("connectors", "select"),
        ("connector_accounts", "insert"),
        ("connectors", "update"),
        ("connector_accounts", "delete"),
        ("connectors", "select"),
        ("connectors", "update"),
        ("connector_accounts", "delete"),
    ]


@pytest.mark.asyncio
async def test_generic_authorize_rejects_provider_type_mismatch():
    request = connector_endpoints.CreateConnectorRequest(
        type="email", provider="google_drive"
    )
    http_request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []}
    )

    with pytest.raises(HTTPException) as caught:
        await connector_endpoints.authorize_connector(
            request,
            http_request,
            SimpleNamespace(id="user-1", tenant_id="tenant-1"),
            _ScriptedDB(),
        )

    assert caught.value.status_code == 400
    assert "belongs to connector type drive" in caught.value.detail


@pytest.mark.asyncio
async def test_gmail_list_propagates_per_message_transport_failure(monkeypatch):
    class _ListClient:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls > 1:
                request = httpx.Request(
                    "GET", "https://gmail.test/messages/message-1"
                )
                raise httpx.ConnectError("network down", request=request)
            return httpx.Response(
                200,
                json={"messages": [{"id": "message-1"}]},
                request=httpx.Request("GET", "https://gmail.test/messages"),
            )

    connector = GmailConnector("tenant-1", "connector-1")
    await connector.set_access_token("access")
    monkeypatch.setattr(
        "app.infrastructure.connectors.email.gmail.httpx.AsyncClient",
        lambda **_kwargs: _ListClient(),
    )

    with pytest.raises(httpx.ConnectError):
        await connector.list_emails(max_results=5)


@pytest.mark.asyncio
async def test_gmail_list_uses_metadata_not_full_message_bodies(monkeypatch):
    class _MetadataClient:
        def __init__(self):
            self.requests = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            self.requests.append((url, kwargs.get("params")))
            if url.endswith("/messages"):
                payload = {"messages": [{"id": "message-1"}]}
            else:
                payload = {
                    "id": "message-1",
                    "snippet": "Bounded preview",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Status"},
                            {"name": "From", "value": "sender@example.com"},
                        ]
                    },
                }
            return httpx.Response(
                200,
                json=payload,
                request=httpx.Request("GET", url),
            )

    client = _MetadataClient()
    connector = GmailConnector("tenant-1", "connector-1")
    await connector.set_access_token("access")
    monkeypatch.setattr(
        "app.infrastructure.connectors.email.gmail.httpx.AsyncClient",
        lambda **_kwargs: client,
    )

    messages = await connector.list_emails(max_results=5, query="in:inbox")

    assert messages[0].body == "Bounded preview"
    detail_params = client.requests[1][1]
    assert ("format", "metadata") in detail_params
    assert ("format", "full") not in detail_params


def test_gmail_parser_walks_nested_mime_parts():
    connector = GmailConnector("tenant-1", "connector-1")
    encoded = "TmVzdGVkIHBsYWluIHRleHQ"  # "Nested plain text", no base64 padding
    message = connector._parse_message(
        {
            "id": "message-1",
            "threadId": "thread-1",
            "internalDate": "1704067200000",
            "snippet": "fallback",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [{"name": "Subject", "value": "Nested"}],
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "parts": [
                            {"mimeType": "text/plain", "body": {"data": encoded}}
                        ],
                    }
                ],
            },
        }
    )

    assert message.body == "Nested plain text"
    assert message.subject == "Nested"


def test_gmail_parser_uses_snippet_when_body_encoding_is_invalid():
    connector = GmailConnector("tenant-1", "connector-1")
    message = connector._parse_message(
        {
            "id": "message-1",
            "snippet": "Readable Gmail snippet",
            "payload": {
                "mimeType": "text/plain",
                "headers": [],
                "body": {"data": "%%%not-base64%%%"},
            },
        }
    )

    assert message.body == "Readable Gmail snippet"


def test_gmail_parser_bounds_and_normalizes_metadata_headers():
    connector = GmailConnector("tenant-1", "connector-1")
    oversized = "x" * 50_000
    message = connector._parse_message(
        {
            "id": "message-1",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "line one\r\n" + oversized},
                    {"name": "From", "value": oversized},
                    {"name": "To", "value": oversized},
                    {"name": "Cc", "value": oversized},
                ]
            },
        }
    )

    assert len(message.subject) <= 500
    assert "\r" not in message.subject and "\n" not in message.subject
    assert message.from_email is not None and len(message.from_email) <= 512
    assert len(message.to[0]) <= 2048
    assert len(message.cc[0]) <= 2048
