"""A failed database call never comes back with an empty error text."""

from __future__ import annotations

import asyncio

import pytest

from app.core import postgres_adapter
from app.core.postgres_adapter import QueryBuilder, RpcBuilder


def _raise_on_connect(monkeypatch, exc: BaseException) -> None:
    async def fake_connect(_dsn: str):
        raise exc

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", fake_connect)


def _query():
    return QueryBuilder(None, "campaigns").select("id")._execute_async()


def _rpc():
    return RpcBuilder(None, "increment_campaign_counter", {})._execute_async()


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_query, _rpc], ids=["query", "rpc"])
@pytest.mark.parametrize(
    "exc, name",
    [
        (TimeoutError(), "TimeoutError"),
        (asyncio.TimeoutError(), "TimeoutError"),
        (AssertionError(), "AssertionError"),
        (KeyError(), "KeyError"),
    ],
)
async def test_empty_text_exception_gives_class_name(monkeypatch, run, exc, name):
    _raise_on_connect(monkeypatch, exc)
    response = await run()
    assert response.error == name
    assert response.error  # truthy: callers test `if response.error:`
    assert response.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_query, _rpc], ids=["query", "rpc"])
async def test_exception_with_text_keeps_the_text(monkeypatch, run):
    _raise_on_connect(monkeypatch, ValueError("boom detail"))
    response = await run()
    assert response.error == "boom detail"
    assert response.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_query, _rpc], ids=["query", "rpc"])
async def test_retryable_error_is_still_raised(monkeypatch, run):
    assert ConnectionRefusedError in postgres_adapter.DATABASE_RETRYABLE_ERRORS
    _raise_on_connect(monkeypatch, ConnectionRefusedError())
    with pytest.raises(ConnectionRefusedError):
        await run()
