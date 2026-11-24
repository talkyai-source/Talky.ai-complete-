"""A failed database call never comes back with an empty error text."""

from __future__ import annotations

import asyncio

import pytest

from app.core import postgres_adapter
from app.core.postgres_adapter import QueryBuilder, RpcBuilder


def _raise_on_connect(monkeypatch, exc: BaseException) -> None:
    async def fake_connect(_dsn: str, **_kwargs):
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


class _FailsOnFirstUse:
    """An open connection whose first call fails (a statement that runs out of time)."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def close(self) -> None:
        return None

    def __getattr__(self, _name: str):
        async def fail(*_args, **_kwargs):
            raise self._exc

        return fail


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_query, _rpc], ids=["query", "rpc"])
@pytest.mark.parametrize("exc", [TimeoutError(), asyncio.TimeoutError()], ids=["builtin", "asyncio"])
async def test_a_timeout_after_the_connection_is_open_gives_class_name(monkeypatch, run, exc):
    # Once connected, a call that runs out of time is still returned as a failed call,
    # and its empty text becomes the class name.
    async def fake_connect(_dsn: str, **_kwargs):
        return _FailsOnFirstUse(exc)

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", fake_connect)
    response = await run()
    assert response.error == "TimeoutError"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_query, _rpc], ids=["query", "rpc"])
async def test_connect_waits_no_longer_than_the_pools_acquire_timeout(monkeypatch, run):
    from app.core import db as core_db

    monkeypatch.setattr(core_db, "_ACQUIRE_TIMEOUT_S", 3.25)
    seen: list[dict] = []

    async def fake_connect(_dsn: str, **kwargs):
        seen.append(kwargs)
        raise ValueError("stop here")

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", fake_connect)
    await run()
    assert seen == [{"timeout": 3.25}]


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_query, _rpc], ids=["query", "rpc"])
async def test_a_connect_timeout_is_raised_as_a_retryable_error(monkeypatch, run):
    # No connection within the limit is "try again later" (503 with Retry-After at the
    # HTTP boundary), the same as a refused connection. Returned as an error result it
    # was read as "no row" by callers that look only at the data: a signed webhook then
    # answered a final "not found".
    from app.core.db import DatabasePoolTimeoutError

    assert DatabasePoolTimeoutError in postgres_adapter.DATABASE_RETRYABLE_ERRORS

    async def fake_connect(_dsn: str, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", fake_connect)
    with pytest.raises(DatabasePoolTimeoutError) as raised:
        await run()
    assert isinstance(raised.value.__cause__, asyncio.TimeoutError)
    assert str(raised.value) == "database connect timed out"
