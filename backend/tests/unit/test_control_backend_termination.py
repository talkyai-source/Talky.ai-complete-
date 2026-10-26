"""Original PBX identity boundary using real adapter/termination code, fake ARI."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.telephony.control_backend_identity import (
    ControlBackendIdentityError,
    asterisk_control_backend,
    saved_control_backend,
)
from app.domain.services.telephony import lifecycle, termination
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
from tests.unit.pbx_identity_helpers import INFO


def adapter_fixture():
    adapter = AsteriskAdapter(ari_host="pbx.example", ari_username="original-account")
    adapter._control_backend_system = INFO["system"]["entity_id"]
    adapter._control_backend_startup = INFO["status"]["startup_time"]
    operations = []
    observed = dict(INFO)

    async def ari(method, path, **_kwargs):
        operations.append((method, path))
        return observed if path == "/asterisk/info" else (404, {})

    adapter._ari = ari
    return adapter, operations, observed


async def prove(adapter, binding, **kwargs):
    return await termination.request_confirmed_hangup(
        adapter,
        "parent",
        expected_provider="asterisk",
        expected_backend=binding,
        require_backend_identity=True,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("_ari_host", "replacement.example"),
        ("_ari_port", 8089),
        ("_ari_username", "replacement-account"),
        ("_app_name", "other-app"),
        ("_control_backend_system", "replacement-pbx"),
        ("_control_backend_startup", "2026-10-22T00:00:00Z"),
    ],
)
async def test_saved_backend_change_dispatches_nothing(field, value):
    adapter, operations, _ = adapter_fixture()
    original = adapter.control_backend_identity
    setattr(adapter, field, value)
    result = await prove(adapter, original)
    assert result.code == "control_backend_identity_mismatch"
    assert not result.confirmed and not result.requested
    assert operations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", [None, "", "asterisk", "pbx1:invalid"])
async def test_historical_unbound_or_malformed_identity_never_uses_current_config(binding):
    adapter, operations, _ = adapter_fixture()
    result = await prove(adapter, binding)
    assert not result.requested and not result.confirmed
    assert result.code == "control_backend_identity_unconfirmed"
    assert operations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement",
    [
        {"system": {"entity_id": "replacement-pbx"}},
        {"status": {"startup_time": "2026-10-22T00:00:00Z"}},
        {"system": {}},
    ],
)
async def test_replacement_or_restart_at_same_address_cannot_receive_hangup(replacement):
    adapter, operations, observed = adapter_fixture()
    observed.update(replacement)
    result = await prove(adapter, adapter.control_backend_identity)
    assert not result.requested and not result.confirmed
    assert operations == [("GET", "/asterisk/info")]


@pytest.mark.asyncio
async def test_same_original_backend_proves_all_legs_and_checks_identity_after_effect():
    adapter, operations, _ = adapter_fixture()
    result = await prove(
        adapter,
        adapter.control_backend_identity,
        provider_leg_ids=("child",),
        provider_legs=(("child", "asterisk"),),
    )
    assert result.requested and result.confirmed
    assert operations == [
        ("GET", "/asterisk/info"),
        ("DELETE", "/channels/parent"),
        ("DELETE", "/channels/child"),
        ("GET", "/asterisk/info"),
    ]


@pytest.mark.asyncio
async def test_changed_backend_during_awaited_proof_does_not_confirm():
    adapter, operations, observed = adapter_fixture()
    original_ari = adapter._ari

    async def ari(method, path, **kwargs):
        result = await original_ari(method, path, **kwargs)
        if method == "DELETE":
            observed["system"] = {"entity_id": "replacement"}
        return result

    adapter._ari = ari
    result = await prove(adapter, adapter.control_backend_identity)
    assert result.requested and not result.confirmed
    assert result.code == "control_backend_identity_mismatch"


@pytest.mark.asyncio
async def test_reconfiguration_refuses_before_mutation_during_bound_operation(monkeypatch):
    adapter, _, _ = adapter_fixture()
    entered, release = asyncio.Event(), asyncio.Event()

    async def hangup(_call):
        entered.set()
        await release.wait()
        return True

    adapter.hangup_confirmed = hangup
    reconnect = AsyncMock()
    monkeypatch.setattr(adapter, "_connect_control_backend", reconnect)
    proof_task = asyncio.create_task(prove(adapter, adapter.control_backend_identity))
    await entered.wait()
    with pytest.raises(ControlBackendIdentityError, match="reconfiguration_busy"):
        await adapter.connect({"ari_host": "replacement"})
    reconnect.assert_not_awaited()
    release.set()
    assert (await proof_task).confirmed
    await adapter.connect({"ari_host": "replacement"})
    reconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_distinct_bound_hangups_progress_concurrently():
    adapter, _, _ = adapter_fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def hangup(call_id):
        calls.append(call_id)
        if len(calls) == 2:
            entered.set()
        await release.wait()
        return True

    adapter.hangup_confirmed = hangup
    tasks = [
        asyncio.create_task(
            termination.request_confirmed_hangup(
                adapter,
                call_id,
                expected_provider="asterisk",
                expected_backend=adapter.control_backend_identity,
                require_backend_identity=True,
            )
        )
        for call_id in ("parent-A", "parent-B")
    ]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert adapter._control_backend_operations == 2
    finally:
        release.set()
    results = await asyncio.gather(*tasks)
    assert all(result.confirmed for result in results)
    assert adapter._control_backend_operations == 0


@pytest.mark.asyncio
async def test_bound_operations_refuse_during_reconfiguration_and_cancel_clears_flag(monkeypatch):
    adapter, operations, _ = adapter_fixture()
    entered = asyncio.Event()

    async def connect(_config):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(adapter, "_connect_control_backend", connect)
    task = asyncio.create_task(adapter.connect())
    await entered.wait()
    proof = await prove(adapter, adapter.control_backend_identity)
    assert not proof.confirmed and not proof.requested
    assert operations == []
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not adapter._control_backend_reconfiguring
    assert (await prove(adapter, adapter.control_backend_identity)).confirmed


@pytest.mark.asyncio
async def test_cancelled_bound_operation_releases_reconfiguration_exclusion(monkeypatch):
    adapter, _, _ = adapter_fixture()
    entered = asyncio.Event()

    async def hangup(_call):
        entered.set()
        await asyncio.Event().wait()

    adapter.hangup_confirmed = hangup
    task = asyncio.create_task(prove(adapter, adapter.control_backend_identity))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter._control_backend_operations == 0
    reconnect = AsyncMock()
    monkeypatch.setattr(adapter, "_connect_control_backend", reconnect)
    await adapter.connect()
    reconnect.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", [None, "pbx1:" + "b" * 64])
async def test_automatic_recovery_cannot_bypass_original_backend_guard(monkeypatch, binding):
    adapter, operations, _ = adapter_fixture()
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    ended = AsyncMock()
    monkeypatch.setattr(lifecycle, "_on_call_ended", ended)
    context = {"provider": "asterisk", "route_snapshot": {"control_backend": binding}}
    assert await lifecycle._force_end_and_hangup("parent", recovery_context=context) is False
    assert context["_awaiting_all_leg_absence_proof"] is True
    ended.assert_not_awaited()
    assert operations == []


def test_snapshot_reader_requires_explicit_versioned_binding():
    binding = "pbx1:" + "a" * 64
    assert saved_control_backend('{"control_backend":"' + binding + '"}') == binding
    for value in [None, "invalid", [], {"provider": "asterisk"}, {"control_backend": 1}]:
        assert saved_control_backend(value) is None


def test_binding_is_credential_free_and_namespace_normalized():
    args = dict(
        host="PBX.EXAMPLE.",
        port=8088,
        account="account",
        application="app",
        system="PBX",
        startup="start",
    )
    one = asterisk_control_backend(**args)
    assert one == asterisk_control_backend(**{**args, "host": "pbx.example"})
    assert one.startswith("pbx1:") and len(one) == 69
    assert asterisk_control_backend(**{**args, "host": "user:secret@pbx.example"}) is None


@pytest.mark.asyncio
async def test_identity_lookup_outage_never_dispatches_hangup():
    adapter, _, _ = adapter_fixture()
    adapter._ari = AsyncMock(side_effect=ConnectionError("synthetic outage"))
    result = await prove(adapter, adapter.control_backend_identity)
    assert not result.requested and not result.confirmed
    assert result.code == "control_backend_verification_unavailable"
    adapter._ari.assert_awaited_once_with("GET", "/asterisk/info")


@pytest.mark.asyncio
async def test_identity_lookup_timeout_is_bounded_before_mutation():
    adapter, _, _ = adapter_fixture()

    async def wait_forever(*_args, **_kwargs):
        await asyncio.Event().wait()

    adapter._ari = wait_forever
    result = await prove(adapter, adapter.control_backend_identity, timeout_s=0.1)
    assert not result.requested and not result.confirmed
    assert result.code == "confirmation_timeout"


@pytest.mark.asyncio
async def test_inbound_binding_is_persisted_inside_existing_admission_snapshot(monkeypatch):
    from tests.unit.test_inbound_admission import _AllowedConn, _Limiter, _acquire
    from app.domain.services.telephony import inbound_admission as admission

    adapter, _, _ = adapter_fixture()
    binding = adapter.control_backend_identity
    conn = _AllowedConn()
    monkeypatch.setattr(admission, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await admission.InboundAdmissionService(object(), _Limiter()).admit(
        admission.InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="pbx-A",
            called_did="+15551234567",
            control_backend=binding,
        )
    )
    assert decision.allowed
    assert json.loads(conn.call_insert_args[15])["control_backend"] == binding
    assert decision.config_snapshot["control_backend"] == binding


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [None, "pbx1:" + "b" * 64])
async def test_same_channel_id_from_other_pbx_never_replays_admission(monkeypatch, saved):
    from tests.unit.test_inbound_admission import _ReplayConn, _acquire
    from app.domain.services.telephony import inbound_admission as admission

    adapter, _, _ = adapter_fixture()
    conn = _ReplayConn(route_snapshot={"control_backend": saved})
    monkeypatch.setattr(admission, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await admission.InboundAdmissionService(object()).admit(
        admission.InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="pbx-A",
            called_did="+15551234567",
            control_backend=adapter.control_backend_identity,
        )
    )
    assert not decision.allowed and not decision.is_replay
    assert decision.reason == "provider_backend_identity_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "info,valid", [(INFO, True), ({"system": INFO["system"]}, False), ({}, False)]
)
async def test_connect_pins_existing_ari_info_response_without_extra_startup_probe(
    monkeypatch, info, valid
):
    from app.infrastructure.telephony import asterisk_adapter as module

    adapter = AsteriskAdapter(ari_host="pbx.example", ari_username="account")

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def json(self, **_kwargs):
            return info

    requests = []

    def get(url, **_kwargs):
        requests.append(url)
        return Response()

    session = SimpleNamespace(get=get, close=AsyncMock())
    monkeypatch.setattr(module.aiohttp, "TCPConnector", lambda: object())
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda **_kwargs: session)
    monkeypatch.setattr(adapter, "_ari_event_listener", AsyncMock())
    if valid:
        await adapter.connect()
        assert adapter.connected and adapter.control_backend_identity
        await adapter._ws_task
    else:
        with pytest.raises(RuntimeError, match="identity unavailable"):
            await adapter.connect()
        assert not adapter.connected and adapter.control_backend_identity is None
        session.close.assert_awaited_once()
    assert requests == ["http://pbx.example:8088/ari/asterisk/info"]


@pytest.mark.asyncio
async def test_origination_retains_original_adapter_across_global_replacement(monkeypatch):
    from tests.unit.test_outbound_campaign_boundaries import _install_call_path, _campaign
    from app.api.v1.endpoints import telephony_bridge as bridge
    from tests.unit.pbx_identity_helpers import BINDING, bound_test_adapter

    path = _install_call_path(monkeypatch, campaign_rows=[_campaign(), _campaign()])
    original_prewarm = bridge.prepare_prewarmed_session
    replacement = bound_test_adapter(SimpleNamespace(name="asterisk", originate_call=AsyncMock()))

    async def replace_during_warmup(**kwargs):
        result = await original_prewarm(**kwargs)
        bridge._adapter = replacement
        return result

    monkeypatch.setattr(bridge, "prepare_prewarmed_session", replace_during_warmup)
    result = await bridge.make_call(path.request, path.body)
    assert result.status_code == 200
    assert path.events.index("claim_intent") < path.events.index("originate")
    claim_query, args = next(
        (query, args) for query, args in path.conn.intent_queries if "update calls" in query
    )
    assert "route_snapshot" in claim_query and args[6] == BINDING
    replacement.originate_call.assert_not_awaited()
