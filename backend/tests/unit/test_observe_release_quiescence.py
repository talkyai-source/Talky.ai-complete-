"""Offline observation contracts; no live service is contacted."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest


SPEC = importlib.util.spec_from_file_location("quiescence_observer", Path(__file__).parents[2] / "scripts/observe_release_quiescence.py")
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import inspect
    import socket

    original = socket.socket.connect
    pair_code = getattr(socket.socketpair, "__code__", None)
    def denied(sock, address):
        # Windows asyncio builds its private wake-up socket pair through connect.
        # Permit only that stdlib function's frame, never arbitrary loopback I/O.
        frame = inspect.currentframe()
        while frame:
            if pair_code is not None and frame.f_code is pair_code:
                return original(sock, address)
            frame = frame.f_back
        raise AssertionError("No live connection is allowed in these tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.mark.parametrize("dsn", ["postgresql:///calls", "postgresql://user@host/calls",
    "postgresql://host:5432/calls", "postgresql://user@host:5432/", "postgresql://user@host:5432/calls?host=other",
    "postgresql://user@one,two:5432/calls", "postgresql://user@host:5432/calls?service=other"])
def test_incomplete_or_ambiguous_db_target_cannot_use_inherited_pg_defaults(dsn, monkeypatch):
    monkeypatch.setenv("PGHOST", "unrelated.invalid")
    with pytest.raises(observer.ObservationError, match="configuration_invalid"):
        observer.database_dsn({"DATABASE_URL": dsn})


def test_complete_db_target_preserves_explicit_ssl_contract():
    dsn = "postgresql://user:private@primary.invalid:5432/calls?sslmode=require"
    assert observer.database_dsn({"DATABASE_URL": dsn}) == dsn


@pytest.mark.asyncio
async def test_actual_cluster_master_is_not_misreported_as_whole_redis(monkeypatch):
    import redis.asyncio as redis

    client = SimpleNamespace(info=AsyncMock(side_effect=[{"role": "master"}, {"cluster_enabled": 1}]),
                             aclose=AsyncMock(), scard=AsyncMock())
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: client)
    with pytest.raises(observer.ObservationError, match="unsupported_topology"):
        await observer.redis_observation({"REDIS_HOST": "synthetic.invalid"})
    client.scard.assert_not_awaited()
    client.aclose.assert_awaited_once()


@pytest.mark.parametrize("payload", [{}, {"active_sessions": True}, {"active_sessions": -1}, {"active_sessions": "0"}])
def test_gateway_unknown_is_never_zero(payload):
    with pytest.raises(observer.ObservationError, match="invalid_response"):
        observer.gateway_count(payload)


def test_ari_counts_all_distinct_legs_without_exporting_identities():
    assert observer.ari_count([{"id": "caller"}, {"id": "external-media"}, {"id": "ringing"}]) == {"count": 3}
    assert observer.ari_count([]) == {"count": 0}
    with pytest.raises(observer.ObservationError):
        observer.ari_count([{"id": "same"}, {"id": "same"}])
    with pytest.raises(observer.ObservationError):
        observer.ari_count([{}])


def fake_redis(members=(), leases=(), ttl=1000, after=None):
    return SimpleNamespace(
        scard=AsyncMock(side_effect=[len(members), len(members) if after is None else after]),
        sscan=AsyncMock(return_value=(0, list(members))),
        scan=AsyncMock(return_value=(0, [observer.LEASE_PREFIX + item for item in leases])),
        pttl=AsyncMock(return_value=ttl))


@pytest.mark.asyncio
@pytest.mark.parametrize("members,leases,ttl,after", [
    (("private-member",), (), -2, None), ((), ("private-lease",), 1000, None),
    (("private",), ("private",), -1, None), (("private",), ("private",), 1000, 2),
])
async def test_redis_inconsistency_is_reported_without_mutation_or_identity(members, leases, ttl, after):
    client = fake_redis(members, leases, ttl, after)
    result = await observer.observe(lambda: observer.redis_counts(client))
    assert result["error"] == "inconsistent_observation"
    assert result["consistent"] is False
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_consistent_zero_and_nonzero_redis_reads():
    for ids in ((), ("private-call",)):
        client = fake_redis(ids, ids)
        result = await observer.redis_counts(client)
        assert result["consistent"] and result["count"] == len(ids)
        client.scan.assert_awaited_once_with(cursor=0, match="telephony:lease:*", count=100)
        client.sscan.assert_awaited_once_with("telephony:active_call_ids", cursor=0, count=100)


@pytest.mark.asyncio
async def test_redis_scan_has_page_and_item_bounds(monkeypatch):
    monkeypatch.setattr(observer, "MAX_SCAN_PAGES", 2)
    client = fake_redis()
    client.scan.return_value = (1, [])
    with pytest.raises(observer.ObservationError, match="limit_exceeded"):
        await observer.scan_ids(client, members=False)
    assert client.scan.await_count == 2
    monkeypatch.setattr(observer, "MAX_ITEMS", 1)
    client.scan.return_value = (0, ["telephony:lease:a", "telephony:lease:b"])
    with pytest.raises(observer.ObservationError, match="limit_exceeded"):
        await observer.scan_ids(client, members=False)


@pytest.mark.asyncio
async def test_actual_canonical_db_context_and_live_states_used_readonly():
    from app.domain.services.dialer.job_states import LIVE_CALL_STATUSES

    calls = []
    class Connection:
        @asynccontextmanager
        async def transaction(self):
            calls.append("transaction")
            yield

        async def execute(self, query):
            calls.append(query)

        async def fetchval(self, query, *args):
            calls.append(query)
            if query == "SHOW transaction_read_only":
                return "on"
            if query == "SELECT pg_is_in_recovery()":
                return False
            assert query == "SELECT count(*) FROM public.calls WHERE status = ANY($1::text[])"
            assert args == (list(LIVE_CALL_STATUSES),)
            assert "termination_pending" in args[0]
            return 7

    class Pool:
        @asynccontextmanager
        async def acquire(self, *, timeout):
            assert timeout == 3
            yield Connection()

    assert await observer.database_counts(Pool()) == {"count": 7}
    assert calls[:3] == ["transaction", "SET LOCAL app.bypass_rls = 'on'",
                         "SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000000'"]


@pytest.mark.asyncio
async def test_failed_or_timed_out_source_never_certifies_zero(monkeypatch):
    monkeypatch.setattr(observer, "http_observation", AsyncMock(return_value={"count": 0}))
    monkeypatch.setattr(observer, "redis_observation", AsyncMock(return_value={"count": 0, "consistent": True}))
    monkeypatch.setattr(observer, "database_observation", AsyncMock(side_effect=RuntimeError("secret DSN")))
    report = await observer.collect({})
    assert not report["complete"] and not report["all_zero_observed"]
    assert report["observations"]["db_live_calls"]["error"] == "unavailable"
    assert "secret" not in json.dumps(report)
    monkeypatch.setattr(observer, "TIMEOUT_SECONDS", 0.01)
    event = asyncio.Event()
    result = await observer.observe(event.wait)
    assert result["error"] == "timeout" and result["count"] is None


@pytest.mark.asyncio
async def test_even_four_zero_reads_do_not_authorize_or_freeze(monkeypatch):
    for name in ("http_observation", "redis_observation", "database_observation"):
        monkeypatch.setattr(observer, name, AsyncMock(return_value={"count": 0}))
    report = await observer.collect({})
    assert report["complete"] and report["all_zero_observed"]
    assert report["traffic_freeze_verified"] is False and report["deployment_authorized"] is False
    assert report["kind"] == "read_only_non_atomic_observation"


@pytest.mark.asyncio
async def test_http_requests_are_get_only_with_scoped_auth_and_bounded_bodies(monkeypatch):
    config = {"TELEPHONY_ADAPTER": "asterisk", "ASTERISK_ARI_HOST": "pbx.invalid",
              "ASTERISK_ARI_PORT": "8088", "ASTERISK_ARI_USER": "private-user",
              "ASTERISK_ARI_PASSWORD": "private-password", "ASTERISK_GATEWAY_BASE_URL": "http://gateway.invalid"}
    real_client, requests = httpx.AsyncClient, []
    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[] if request.url.path == "/ari/channels" else {"active_sessions": 2})
    def client(**kwargs):
        assert kwargs == {"timeout": 3, "follow_redirects": False, "trust_env": False}
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert await observer.http_observation(config, "ari") == {"count": 0}
    assert await observer.http_observation(config, "gateway") == {"count": 2}
    assert all(request.method == "GET" for request in requests)
    assert "authorization" in requests[0].headers and "authorization" not in requests[1].headers
    monkeypatch.setattr(observer, "MAX_BODY_BYTES", 1)
    with pytest.raises(observer.ObservationError, match="limit_exceeded"):
        await observer.http_observation(config, "gateway")
