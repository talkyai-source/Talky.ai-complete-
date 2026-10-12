"""GET /calls date filters bind real datetimes; live panel excludes Test Agent calls."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.calls as calls_module
from app.api.v1.dependencies import CurrentUser


class _Conn:
    def __init__(self):
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()

    async def execute(self, *_args):
        return None

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return 0


def _db(conn):
    @asynccontextmanager
    async def acquire():
        yield conn

    return SimpleNamespace(pool=SimpleNamespace(acquire=acquire))


def _user():
    return CurrentUser(id=str(uuid4()), email="u@example.com", tenant_id=str(uuid4()), role="tenant_admin")


async def _list(conn, **overrides):
    kwargs = dict(
        page=1,
        page_size=20,
        status=None,
        direction=None,
        inbound_campaign_id=None,
        from_date=None,
        to_date=None,
        current_user=_user(),
        db_client=_db(conn),
    )
    kwargs.update(overrides)
    return await calls_module.list_calls(**kwargs)


@pytest.mark.asyncio
async def test_date_filters_bind_datetimes_not_strings():
    conn = _Conn()
    response = await _list(conn, from_date="2026-10-01", to_date="2026-10-09")
    assert response.total == 0
    query, params = conn.fetch_calls[0]
    datetimes = [p for p in params if isinstance(p, datetime)]
    assert datetimes == [
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 10, tzinfo=timezone.utc),  # exclusive upper bound: day after "to"
    ]
    assert not any(isinstance(p, str) and p.startswith("2026") for p in params)
    assert "c.created_at >= $2" in query
    assert "c.created_at < $3" in query
    # tenant predicate and the count query reuse the same bound parameters
    assert "c.tenant_id = $1" in query
    count_query, count_params = conn.fetchval_calls[0]
    assert count_params == params[:3]
    assert "c.created_at < $3" in count_query


@pytest.mark.asyncio
async def test_only_to_date_keeps_parameter_numbering():
    conn = _Conn()
    await _list(conn, status="completed", to_date="2026-12-31")
    query, params = conn.fetch_calls[0]
    assert "c.status = $2" in query and "c.created_at < $3" in query
    assert params[2] == datetime(2027, 1, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["from_date", "to_date"])
@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-40", "2026-10-01T10:00:00Z"])
async def test_malformed_date_is_a_400_not_a_500(field, bad):
    conn = _Conn()
    with pytest.raises(HTTPException) as err:
        await _list(conn, **{field: bad})
    assert err.value.status_code == 400
    assert conn.fetch_calls == []


@pytest.mark.asyncio
async def test_live_calls_sql_excludes_test_agent_sessions(monkeypatch):
    captured = {}

    class _LiveConn:
        async def fetch(self, query, *args):
            captured["sql"] = " ".join(query.split())
            captured["args"] = args
            return []

    @asynccontextmanager
    async def acquire(_pool, tenant):
        captured["tenant"] = tenant
        yield _LiveConn()

    monkeypatch.setattr(calls_module, "acquire_with_tenant", acquire)
    user = _user()
    result = await calls_module.list_live_calls(
        campaign_id=None,
        direction=None,
        recent_window_seconds=60,
        current_user=user,
        db_client=SimpleNamespace(pool=object()),
    )
    assert result.items == []
    assert "c.tenant_id = $" in captured["sql"]
    # is_test is nullable on old rows: NULL must still count as a real call.
    assert "c.is_test IS NOT TRUE" in captured["sql"]
    assert captured["tenant"] == user.tenant_id
