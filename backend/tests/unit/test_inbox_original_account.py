"""A read retry may refresh credentials, never replace the selected mailbox."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1.endpoints import connectors as connector_endpoints

from app.infrastructure.assistant.tools import inbox
from app.infrastructure.connectors.base import ConnectorProviderError, OAuthTokens
from app.infrastructure.connectors.email.base import EmailMessage
from app.infrastructure.connectors.email.gmail import GmailConnector
from app.services import connector_resolver


def connector(account="account-a", provider="gmail", row="authorization-a"):
    return SimpleNamespace(external_account_id=account, provider_name=provider, account_row_id=row)


def rejected(category="authentication", status=401):
    return ConnectorProviderError(
        provider="gmail",
        operation="get_email",
        category=category,
        status_code=status,
        message="Synthetic provider rejection",
    )


async def invoke(selected, operation):
    return await inbox._call_with_one_auth_refresh(
        selected,
        connector_id="connector-a",
        tenant_id="tenant-a",
        db_client=object(),
        operation=operation,
    )


@pytest.mark.asyncio
async def test_same_original_account_refresh_preserves_success(monkeypatch):
    original, refreshed = connector(), connector()
    result = {"message_id": "synthetic-message", "account": "account-a"}
    operation = AsyncMock(side_effect=[rejected(), result])
    resolver = AsyncMock(return_value=(refreshed, "connector-a", "gmail"))
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)

    assert await invoke(original, operation) == result
    assert operation.await_args_list[0].args == (original,)
    assert operation.await_args_list[1].args == (refreshed,)
    assert operation.await_count == 2
    assert resolver.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account,row,provider",
    [
        ("account-b", "connector-a", "gmail"),
        ("account-a", "connector-b", "gmail"),
        ("account-a", "connector-a", "smtp"),
        (None, "connector-a", "gmail"),
    ],
)
async def test_refresh_cannot_read_a_replacement_identity(monkeypatch, account, row, provider):
    original, replacement = connector(), connector(account, provider)
    operation = AsyncMock(side_effect=[rejected(), {"wrong_account": True}])
    resolver = AsyncMock(return_value=(replacement, row, provider))
    marker = Mock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    monkeypatch.setattr(inbox, "_mark_email_authorization_expired", marker)

    with pytest.raises(connector_resolver.ReviewedConnectorChanged):
        await invoke(original, operation)
    operation.assert_awaited_once_with(original)
    marker.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_query_remains_pinned_to_initial_connector_and_provider(monkeypatch):
    resolver = AsyncMock(return_value=(connector(), "connector-a", "gmail"))
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    await invoke(connector(), AsyncMock(side_effect=[rejected(), "read-result"]))
    assert resolver.await_args.kwargs == {
        "force_refresh": True,
        "connector_id": "connector-a",
        "provider": "gmail",
        "account_id": "authorization-a",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["external_account_id", "account_row_id"])
async def test_identity_is_frozen_before_first_await_even_if_instance_changes(
    monkeypatch, changed_field
):
    original = connector()
    calls = []

    async def operation(current):
        calls.append(current.external_account_id)
        if len(calls) == 1:
            setattr(original, changed_field, "changed-identity")
            raise rejected()
        return "wrong-account-result"

    monkeypatch.setattr(
        connector_resolver,
        "resolve_active_connector",
        AsyncMock(return_value=(original, "connector-a", "gmail")),
    )
    with pytest.raises(connector_resolver.ReviewedConnectorChanged):
        await invoke(original, operation)
    assert calls == ["account-a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [connector(None, row=None), connector("account-a", None)])
async def test_missing_retry_proof_preserves_first_read(monkeypatch, selected):
    operation, resolver = AsyncMock(return_value="read-result"), AsyncMock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    assert await invoke(selected, operation) == "read-result"
    operation.assert_awaited_once_with(selected)
    resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [connector(None, row=None), connector("account-a", None)])
async def test_missing_retry_proof_cannot_retry_401(monkeypatch, selected):
    operation, resolver = AsyncMock(side_effect=rejected()), AsyncMock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    with pytest.raises(connector_resolver.ReviewedConnectorChanged):
        await invoke(selected, operation)
    operation.assert_awaited_once_with(selected)
    resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_row", [None, "authorization-b"])
async def test_same_external_account_on_reconnected_row_cannot_retry(monkeypatch, replacement_row):
    selected = connector()
    operation = AsyncMock(side_effect=[rejected(), "wrong-row-result"])
    monkeypatch.setattr(
        connector_resolver,
        "resolve_active_connector",
        AsyncMock(return_value=(connector(row=replacement_row), "connector-a", "gmail")),
    )
    with pytest.raises(connector_resolver.ReviewedConnectorChanged):
        await invoke(selected, operation)
    operation.assert_awaited_once_with(selected)


@pytest.mark.asyncio
async def test_successful_first_read_does_not_refresh(monkeypatch):
    resolver = AsyncMock()
    operation = AsyncMock(return_value={"message_id": "synthetic-message"})
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    assert await invoke(connector(), operation) == {"message_id": "synthetic-message"}
    operation.assert_awaited_once()
    resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [rejected("permission", 403), TimeoutError(), asyncio.CancelledError()]
)
async def test_other_failure_does_not_refresh(monkeypatch, failure):
    resolver = AsyncMock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    with pytest.raises(type(failure)):
        await invoke(connector(), AsyncMock(side_effect=failure))
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_same_account_401_does_not_start_another_read(monkeypatch):
    operation = AsyncMock(side_effect=rejected())
    resolver = AsyncMock(return_value=(connector(), "connector-a", "gmail"))
    marker = Mock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    monkeypatch.setattr(inbox, "_mark_email_authorization_expired", marker)
    with pytest.raises(ConnectorProviderError):
        await invoke(connector(), operation)
    assert operation.await_count == 2 and resolver.await_count == 1
    marker.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("list_mode", [False, True])
async def test_foreign_refresh_failure_cannot_expire_or_read_replacement(monkeypatch, list_mode):
    original = connector()
    method = "list_emails" if list_mode else "get_email"
    setattr(original, method, AsyncMock(side_effect=rejected()))
    resolver = AsyncMock(
        side_effect=[
            (original, "connector-a", "gmail"),
            connector_resolver.ConnectorNotConnectedError(
                "email", connector_id="connector-b", provider_confirmed=True
            ),
        ]
    )
    marker = Mock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    monkeypatch.setattr(inbox, "_mark_email_authorization_expired", marker)
    result = (
        await inbox.read_emails("tenant-a", object())
        if list_mode
        else await inbox.read_email("tenant-a", object(), "synthetic-message")
    )
    assert result["success"] is False
    assert result["error_code"] == "email_account_changed"
    marker.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("list_mode", [False, True])
@pytest.mark.parametrize("same_account", [False, True])
async def test_actual_list_and_read_keep_result_contract(monkeypatch, list_mode, same_account):
    original, refreshed = connector(), connector("account-a" if same_account else "account-b")
    message = EmailMessage(
        id="synthetic-message",
        subject="Synthetic subject",
        body="x" * 4100,
        attachments=[{"filename": "synthetic.txt"}],
    )
    method = "list_emails" if list_mode else "get_email"
    setattr(original, method, AsyncMock(side_effect=rejected()))
    setattr(refreshed, method, AsyncMock(return_value=[message] if list_mode else message))
    resolver = AsyncMock(
        side_effect=[(original, "connector-a", "gmail"), (refreshed, "connector-a", "gmail")]
    )
    marker = Mock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    monkeypatch.setattr(inbox, "_mark_email_authorization_expired", marker)

    result = (
        await inbox.read_emails(
            "tenant-a", object(), query="in:sent", unread_only="false", max_results=99
        )
        if list_mode
        else await inbox.read_email("tenant-a", object(), "  synthetic-message  ")
    )
    assert result["success"] is same_account
    if not same_account:
        assert result["error_code"] == "email_account_changed"
        assert "emails" not in result and "email" not in result
        getattr(refreshed, method).assert_not_awaited()
        marker.assert_not_called()
    elif list_mode:
        assert result["count"] == 1 and len(result["emails"][0]["snippet"]) == 201
        refreshed.list_emails.assert_awaited_once_with(
            max_results=25, query="in:sent", unread_only=False
        )
    else:
        assert result["email"]["id"] == "synthetic-message"
        assert result["email"]["body_truncated"] is True and len(result["email"]["body"]) == 4001
        assert "attachments" not in result["email"]  # Existing public projection stays bounded.
        refreshed.get_email.assert_awaited_once_with("synthetic-message")


class _Rows:
    """Synthetic DB port applying real callback/resolver equality predicates."""

    def __init__(self):
        self.rows = {
            "connectors": [
                {
                    "id": "connector-a",
                    "tenant_id": "tenant-a",
                    "provider": "gmail",
                    "type": "email",
                    "status": "pending",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "connector_accounts": [],
        }
        self.queries = []

    def table(self, table):
        return _RowQuery(self, table)


class _RowQuery:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.action, self.filters, self.payload = "select", [], None
        self.one, self.maximum, self.ordering = False, None, None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value, True))
        return self

    def neq(self, key, value):
        self.filters.append((key, value, False))
        return self

    def order(self, key, desc=False):
        self.ordering = (key, desc)
        return self

    def limit(self, maximum):
        self.maximum = maximum
        return self

    def single(self):
        self.one = True
        return self

    def insert(self, payload):
        self.action, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.action, self.payload = "update", payload
        return self

    def delete(self):
        self.action = "delete"
        return self

    def execute(self):
        self.db.queries.append((self.table_name, self.action, tuple(self.filters)))
        rows = self.db.rows[self.table_name]
        matching = [
            row
            for row in rows
            if all((row.get(key) == value) == equality for key, value, equality in self.filters)
        ]
        if self.action == "insert":
            matching = [{"id": str(uuid4()), **self.payload}]
            rows.extend(matching)
        elif self.action == "update":
            for row in matching:
                row.update(self.payload)
        elif self.action == "delete":
            self.db.rows[self.table_name] = [row for row in rows if row not in matching]
        if self.ordering:
            key, desc = self.ordering
            matching = sorted(matching, key=lambda row: str(row.get(key) or ""), reverse=desc)
        if self.maximum is not None:
            matching = matching[: self.maximum]
        data = [dict(row) for row in matching]
        return SimpleNamespace(data=(data[0] if data else None) if self.one else data, error=None)


@pytest.fixture
async def canonical_gmail(monkeypatch):
    """Actual OAuth callback, real Gmail object, real resolver; synthetic IO only."""
    db = _Rows()
    enc = SimpleNamespace(
        encrypt=lambda value: "synthetic:" + value,
        decrypt=lambda value: value.removeprefix("synthetic:"),
    )
    monkeypatch.setattr(connector_endpoints, "get_encryption_service", lambda: enc)
    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: enc)
    monkeypatch.setattr("app.core.security.tenant_isolation.set_current_tenant_id", lambda _: None)
    monkeypatch.setattr(
        connector_endpoints,
        "get_oauth_state_manager",
        lambda: SimpleNamespace(
            validate_state=AsyncMock(
                return_value={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "provider": "gmail",
                    "redirect_uri": "https://example.invalid/callback",
                    "code_verifier": "synthetic",
                    "connector_id": "connector-a",
                }
            )
        ),
    )
    tokens = OAuthTokens(
        access_token="synthetic-access",
        refresh_token="synthetic-refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope=None,
    )
    monkeypatch.setattr(GmailConnector, "exchange_code", AsyncMock(return_value=tokens))
    monkeypatch.setattr(
        GmailConnector,
        "get_profile",
        AsyncMock(return_value={"emailAddress": "synthetic@example.invalid"}),
    )
    refresh = AsyncMock(return_value=tokens)
    monkeypatch.setattr(GmailConnector, "refresh_tokens", refresh)
    response = await connector_endpoints.oauth_callback(
        Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        state="synthetic",
        code="synthetic",
        error=None,
        db_client=db,
    )
    assert "status=success" in response.headers["location"]
    assert len(db.rows["connector_accounts"]) == 1
    account = db.rows["connector_accounts"][0]
    assert account["external_account_id"] is None
    assert account["account_email"] == "synthetic@example.invalid"
    return db, account, refresh


@pytest.mark.asyncio
@pytest.mark.parametrize("needs_refresh", [False, True])
async def test_canonical_gmail_callback_to_actual_resolver_read(
    monkeypatch, canonical_gmail, needs_refresh
):
    db, account, refresh = canonical_gmail
    message = EmailMessage(id="synthetic-message", subject="Synthetic", body="Synthetic")
    operation = AsyncMock(side_effect=[rejected(), message] if needs_refresh else [message])
    monkeypatch.setattr(GmailConnector, "get_email", operation)
    result = await inbox.read_email("tenant-a", db, "synthetic-message")
    assert result["success"] is True
    assert result["email"]["id"] == "synthetic-message"
    assert operation.await_count == (2 if needs_refresh else 1)
    assert refresh.await_count == int(needs_refresh)
    if needs_refresh:
        account_reads = [
            filters
            for table, action, filters in db.queries
            if table == "connector_accounts" and action == "select"
        ]
        assert ("id", account["id"], True) in account_reads[-1]
        assert ("tenant_id", "tenant-a", True) in account_reads[-1]
        assert ("connector_id", "connector-a", True) in account_reads[-1]
        assert ("status", "active", True) in account_reads[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["reconnect", "deleted", "inactive", "foreign_tenant", "foreign_connector"]
)
async def test_actual_resolver_never_refreshes_replacement_after_first_401(
    monkeypatch, canonical_gmail, change
):
    db, account, refresh = canonical_gmail
    calls = []

    async def first_read(_self, _message_id):
        calls.append("original")
        replacement = {**account, "id": str(uuid4()), "last_refreshed_at": "2099-01-01T00:00:00Z"}
        if change == "reconnect":
            db.rows["connector_accounts"] = [replacement]
        elif change == "deleted":
            db.rows["connector_accounts"] = []
        else:
            account[
                {
                    "inactive": "status",
                    "foreign_tenant": "tenant_id",
                    "foreign_connector": "connector_id",
                }[change]
            ] = {
                "inactive": "revoked",
                "foreign_tenant": "tenant-b",
                "foreign_connector": "connector-b",
            }[
                change
            ]
            db.rows["connector_accounts"].append(replacement)
        raise rejected()

    monkeypatch.setattr(GmailConnector, "get_email", first_read)
    marker = Mock()
    monkeypatch.setattr(inbox, "_mark_email_authorization_expired", marker)
    result = await inbox.read_email("tenant-a", db, "synthetic-message")
    assert result["success"] is False
    assert calls == ["original"]
    assert result["error_code"] == "email_account_changed"
    refresh.assert_not_awaited()
    marker.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reconnect", "revoked"])
async def test_actual_resolver_does_not_retry_when_row_lost_during_refresh(
    monkeypatch, canonical_gmail, change
):
    db, account, refresh = canonical_gmail
    tokens = refresh.return_value

    async def changed_during_refresh(_token):
        if change == "reconnect":
            db.rows["connector_accounts"] = [{**account, "id": str(uuid4())}]
        else:
            account["status"] = "revoked"
        return tokens

    refresh.side_effect = changed_during_refresh
    operation = AsyncMock(side_effect=[rejected(), EmailMessage(id="should-not-read")])
    monkeypatch.setattr(GmailConnector, "get_email", operation)
    marker = Mock()
    monkeypatch.setattr(inbox, "_mark_email_authorization_expired", marker)
    result = await inbox.read_email("tenant-a", db, "synthetic-message")
    assert result["success"] is False
    assert result["error_code"] == "email_lookup_error"
    operation.assert_awaited_once()
    refresh.assert_awaited_once()
    marker.assert_not_called()
