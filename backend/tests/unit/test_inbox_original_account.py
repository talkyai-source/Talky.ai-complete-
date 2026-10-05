"""A read retry may refresh credentials, never replace the selected mailbox."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.infrastructure.assistant.tools import inbox
from app.infrastructure.connectors.base import ConnectorProviderError
from app.infrastructure.connectors.email.base import EmailMessage
from app.services import connector_resolver


def connector(account="account-a", provider="gmail"):
    return SimpleNamespace(external_account_id=account, provider_name=provider)


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
    }


@pytest.mark.asyncio
async def test_identity_is_frozen_before_first_await_even_if_instance_changes(monkeypatch):
    original = connector()
    calls = []

    async def operation(current):
        calls.append(current.external_account_id)
        if len(calls) == 1:
            original.external_account_id = "account-b"
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
@pytest.mark.parametrize("selected", [connector(None), connector("account-a", None)])
async def test_missing_initial_identity_is_unavailable_before_read(monkeypatch, selected):
    operation, resolver = AsyncMock(return_value="read-result"), AsyncMock()
    monkeypatch.setattr(connector_resolver, "resolve_active_connector", resolver)
    with pytest.raises(connector_resolver.ReviewedConnectorChanged):
        await invoke(selected, operation)
    operation.assert_not_awaited()
    resolver.assert_not_awaited()


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
