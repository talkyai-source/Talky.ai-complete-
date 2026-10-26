"""Configured PBX reads use synthetic HTTP only; never create an adapter."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.v1.endpoints import telephony_bridge
from app.api.v1.endpoints.admin.health import _pbx_probe as probe, _shared
from app.core import container
from app.infrastructure.telephony.adapter_factory import AdapterRegistry, CallControlAdapterFactory


ARI = {"system": {"entity_id": "synthetic-pbx"}, "status": {"startup_time": "2026-10-27T00:00:00Z"}}
GATEWAY = {"status": "ok", "io_loop_healthy": True, "protocol_version": 2,
           "codecs": ["pcmu"], "callback_protocol_versions": [2]}


@pytest.fixture
def configured(monkeypatch):
    values = {"TELEPHONY_ADAPTER": "asterisk", "ASTERISK_ARI_HOST": "pbx.invalid",
              "ASTERISK_ARI_PORT": "8088", "ASTERISK_ARI_USER": "synthetic-admin",
              "ASTERISK_ARI_PASSWORD": "synthetic-password", "ASTERISK_GATEWAY_BASE_URL": "http://gateway.invalid:18080"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    def forbidden(*args, **kwargs):
        raise AssertionError("Read probe must not create, connect or auto-detect adapters")
    for name in ("create", "_make_asterisk", "_make_freeswitch", "_auto_detect"):
        monkeypatch.setattr(CallControlAdapterFactory, name, forbidden)
    monkeypatch.setattr(AdapterRegistry, "get_or_create", forbidden)
    return values


@pytest.fixture
def transport(monkeypatch):
    original_client = httpx.AsyncClient
    requests, options, clients = [], [], []
    def install(handler):
        async def observed(request):
            requests.append(request)
            return await handler(request)
        def client(**kwargs):
            options.append(kwargs)
            value = original_client(transport=httpx.MockTransport(observed), **kwargs)
            clients.append(value)
            return value
        monkeypatch.setattr(probe.httpx, "AsyncClient", client)
    return SimpleNamespace(install=install, requests=requests, options=options, clients=clients)


async def healthy(request):
    return httpx.Response(200, json=ARI if request.url.host == "pbx.invalid" else GATEWAY)


@pytest.mark.asyncio
async def test_explicit_target_reads_have_separate_auth_no_redirects_or_proxy(configured, transport):
    transport.install(healthy)
    assert await probe.configured_pbx_health() is True
    assert {(r.method, r.url.host, r.url.path) for r in transport.requests} == {
        ("GET", "pbx.invalid", "/ari/asterisk/info"), ("GET", "gateway.invalid", "/health")}
    ari = next(r for r in transport.requests if r.url.host == "pbx.invalid")
    gateway = next(r for r in transport.requests if r.url.host == "gateway.invalid")
    assert ari.headers["authorization"].startswith("Basic ")
    assert "authorization" not in gateway.headers
    assert transport.options == [{"timeout": 3.0, "follow_redirects": False, "trust_env": False}]
    assert all(client.is_closed for client in transport.clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["auto", "freeswitch", "unsupported"])
async def test_other_or_automatic_selection_does_not_probe(configured, monkeypatch, transport, selection):
    monkeypatch.setenv("TELEPHONY_ADAPTER", selection)
    transport.install(healthy)
    assert await probe.configured_pbx_health() is None
    assert transport.requests == [] and transport.clients == []


@pytest.mark.asyncio
async def test_yaml_selection_uses_existing_precedence(configured, monkeypatch, transport):
    monkeypatch.delenv("TELEPHONY_ADAPTER")
    monkeypatch.setattr(CallControlAdapterFactory, "_read_pbx_backend_config", lambda: "asterisk")
    transport.install(healthy)
    assert await probe.configured_pbx_health() is True
    monkeypatch.setenv("TELEPHONY_ADAPTER", "freeswitch")
    assert await probe.configured_pbx_health() is None
    assert len(transport.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["ASTERISK_ARI_HOST", "ASTERISK_ARI_PORT", "ASTERISK_ARI_USER",
                                    "ASTERISK_ARI_PASSWORD", "ASTERISK_GATEWAY_BASE_URL"])
async def test_missing_configuration_is_unknown_without_requests(configured, monkeypatch, transport, missing):
    monkeypatch.delenv(missing)
    transport.install(healthy)
    assert await probe.configured_pbx_health() is None
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", [
    ("ASTERISK_ARI_PORT", "0"), ("ASTERISK_ARI_PORT", "65536"), ("ASTERISK_ARI_PORT", "bad"),
    ("ASTERISK_ARI_HOST", "user@other.invalid"), ("ASTERISK_ARI_HOST", "host/path"),
    ("ASTERISK_GATEWAY_BASE_URL", "http://user:secret@gateway.invalid"),
    ("ASTERISK_GATEWAY_BASE_URL", "file:///etc/passwd"),
    ("ASTERISK_GATEWAY_BASE_URL", "http://gateway.invalid?secret=value"),
    ("ASTERISK_GATEWAY_BASE_URL", "http://gateway.invalid#fragment"),
])
async def test_invalid_server_targets_do_not_send_credentials(configured, monkeypatch, transport, name, value):
    monkeypatch.setenv(name, value)
    transport.install(healthy)
    assert await probe.configured_pbx_health() is None
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("host,status", [("pbx.invalid", 401), ("gateway.invalid", 503),
                                        ("pbx.invalid", 302), ("gateway.invalid", 307)])
async def test_failed_or_redirected_response_is_down_without_following(configured, transport, host, status):
    async def handler(request):
        if request.url.host == host:
            return httpx.Response(status, headers={"Location": "https://other.invalid/leak"})
        return await healthy(request)
    transport.install(handler)
    assert await probe.configured_pbx_health() is False
    assert all(request.url.host in {"pbx.invalid", "gateway.invalid"} for request in transport.requests)
    assert all(client.is_closed for client in transport.clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"status": "unhealthy"}, {"io_loop_healthy": 1}, {"io_loop_healthy": False},
    {"protocol_version": 1}, {"protocol_version": "2"}, {"protocol_version": 2.0},
    {"codecs": "pcmu"}, {"codecs": ["opus"]}, {"codecs": ["pcmu", 1]},
    {"callback_protocol_versions": [1]}, {"callback_protocol_versions": [2.0]},
    {"callback_protocol_versions": [2, True]},
])
async def test_gateway_requires_exact_compatible_contract(configured, transport, patch):
    async def handler(request):
        return httpx.Response(200, json={**GATEWAY, **patch}) if request.url.host == "gateway.invalid" else await healthy(request)
    transport.install(handler)
    assert await probe.configured_pbx_health() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], {}, {"system": [], "status": {}},
                                     {"system": {"entity_id": True}, "status": {"startup_time": "now"}},
                                     {"system": {"entity_id": "pbx"}, "status": {"startup_time": ""}}])
async def test_ari_200_requires_actual_info_shape(configured, transport, payload):
    async def handler(request):
        return httpx.Response(200, json=payload) if request.url.host == "pbx.invalid" else await healthy(request)
    transport.install(handler)
    assert await probe.configured_pbx_health() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["invalid_json", "oversized", "connection", "read_timeout"])
async def test_transport_and_response_failure_is_down(configured, transport, fault):
    async def handler(request):
        if fault == "connection":
            raise httpx.ConnectError("synthetic private detail", request=request)
        if fault == "read_timeout":
            raise httpx.ReadTimeout("synthetic private detail", request=request)
        return httpx.Response(200, content=b"x" * (65537 if fault == "oversized" else 1))
    transport.install(handler)
    assert await probe.configured_pbx_health() is False
    assert all(client.is_closed for client in transport.clients)


@pytest.mark.asyncio
async def test_overall_deadline_cancels_both_reads_and_closes_client(configured, monkeypatch, transport):
    cancelled = []
    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(request.url.host)
    monkeypatch.setattr(probe, "_PROBE_TIMEOUT_SECONDS", 0.02)
    transport.install(handler)
    assert await asyncio.wait_for(probe.configured_pbx_health(), timeout=1) is False
    assert set(cancelled) == {"pbx.invalid", "gateway.invalid"}
    assert all(client.is_closed for client in transport.clients)


@pytest.mark.asyncio
async def test_request_cancellation_propagates_and_closes_client(configured, transport):
    entered = asyncio.Event()
    cancelled = []
    async def handler(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(request.url.host)
    transport.install(handler)
    task = asyncio.create_task(probe.configured_pbx_health())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled and all(client.is_closed for client in transport.clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("observed,status", [(True, "operational"), (False, "down"), (None, "unknown")])
async def test_web_process_presents_only_scoped_control_evidence(monkeypatch, observed, status):
    monkeypatch.setattr(telephony_bridge, "_adapter", None)
    read = AsyncMock(return_value=observed)
    monkeypatch.setattr(probe, "configured_pbx_health", read)
    monkeypatch.setattr(_shared, "_database_probe", AsyncMock(return_value=1))
    monkeypatch.setattr(_shared, "_observed_providers", AsyncMock(return_value=[]))
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=False))
    result = next(item for item in await _shared._internal_dependency_health(object()) if item.name == "Telephony")
    assert result.status == status
    assert "not active call ownership, tenant carrier registration or end-to-end audio" in result.scope
    assert (result.last_check is None) is (observed is None)
    assert (result.latency_ms is None) is (observed is not True)
    read.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["asterisk", "freeswitch"])
async def test_existing_adapter_path_never_uses_configured_fallback(monkeypatch, name):
    adapter = SimpleNamespace(name=name, health_check=AsyncMock(return_value=True))
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    read = AsyncMock(side_effect=AssertionError("Existing adapter must not use fallback"))
    monkeypatch.setattr(probe, "configured_pbx_health", read)
    monkeypatch.setattr(_shared, "_database_probe", AsyncMock(return_value=1))
    monkeypatch.setattr(_shared, "_observed_providers", AsyncMock(return_value=[]))
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=False))
    result = next(item for item in await _shared._internal_dependency_health(object()) if item.name == "Telephony")
    assert result.status == ("operational" if name == "asterisk" else "unknown")
    assert adapter.health_check.await_count == (1 if name == "asterisk" else 0)
    read.assert_not_awaited()
