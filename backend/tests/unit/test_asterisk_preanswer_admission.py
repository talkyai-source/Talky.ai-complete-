from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.telephony.asterisk_adapter import (
    AsteriskAdapter,
    _AriResponseError,
)


def _enable_answer_persistence(
    adapter: AsteriskAdapter,
    callback=None,
):
    hook = callback or AsyncMock(return_value=None)
    adapter.set_inbound_answered_persist_callback(hook)
    return hook


def _event(channel_id: str = "inbound-1") -> dict:
    return {
        "type": "StasisStart",
        "args": ["inbound", "+15557778888", "from-opensips"],
        "channel": {
            "id": channel_id,
            "name": "PJSIP/carrier-a-00000001",
            "dialplan": {"context": "from-opensips", "exten": "750"},
            "caller": {"number": "+15553334444"},
        },
    }


def _allowed(channel_id: str = "inbound-1") -> dict:
    return {
        "allowed": True,
        "reason": "admitted",
        "call_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "campaign_id": "33333333-3333-3333-3333-333333333333",
        "provider": "asterisk",
        "provider_call_id": channel_id,
        "config_snapshot": {
            "campaign": {"id": "campaign"},
            "inbound_config": {
                "is_after_hours": False,
                "selected_action": "agent",
                "selected_destination": None,
                "after_hours_message": None,
            },
            "schedule_decision": {
                "valid": True,
                "is_after_hours": False,
                "reason": "within_business_hours",
                "timezone": "UTC",
                "local_datetime": "2026-08-26T12:00:00+00:00",
                "selected_action": "agent",
                "selected_destination": None,
                "after_hours_message": None,
            },
        },
    }


@pytest.mark.asyncio
async def test_inbound_admission_precedes_answer_and_media_allocation(monkeypatch):
    from app.domain.services.telephony import state_backend

    adapter = AsteriskAdapter()
    actions: list[str] = []

    class State:
        async def register_answer_intent_cleanup_obligation(
            self,
            call_id,
            **kwargs,
        ):
            actions.append("persist_answer_intent")
            assert call_id == "inbound-1"
            assert kwargs["durable_call_id"] == _allowed()["call_id"]
            assert kwargs["provider"] == "asterisk"
            assert kwargs["provider_call_id"] == "inbound-1"

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: State())

    async def admit(channel_id, metadata):
        actions.append("admit")
        assert channel_id == "inbound-1"
        assert metadata["called_did"] == "+15557778888"
        return _allowed(channel_id)

    async def ari(method, path, **kwargs):
        actions.append(f"ari:{method}:{path}")
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if path == "/channels/externalMedia":
            return {"id": "external-1"}
        return {}

    async def allocate_port():
        actions.append("allocate_port")
        return 32000

    async def resolve_media(**kwargs):
        actions.append("resolve_media")
        return "127.0.0.1", 41000

    async def gateway(method, path, **kwargs):
        actions.append(f"gateway:{method}:{path}")
        return {}

    async def accept_arrival(channel_id, _admission):
        assert adapter.accept_inbound_handoff(channel_id)

    async def persist_answer(channel_id, admission, *, answered_at):
        actions.append("persist_answer")
        assert channel_id == "inbound-1"
        assert admission["_answered_at_monotonic"] > 0
        assert admission["_answered_at_utc"] == answered_at
        return answered_at

    arrived = AsyncMock(side_effect=accept_arrival)
    adapter.set_inbound_admission_callback(admit)
    adapter.set_inbound_admission_finalizer(AsyncMock())
    answer_hook = _enable_answer_persistence(adapter, AsyncMock(side_effect=persist_answer))
    adapter._ari = ari
    adapter._alloc_rtp_port = allocate_port
    adapter._resolve_unicastrtp_local = resolve_media
    adapter._gateway = gateway
    adapter._on_new_call = arrived

    await adapter._on_stasis_start("inbound-1", _event())
    await asyncio.sleep(0)

    assert actions[0] == "admit"
    assert actions.index("persist_answer_intent") < actions.index(
        "ari:POST:/channels/inbound-1/answer"
    )
    assert actions.index("ari:POST:/channels/inbound-1/answer") < actions.index("persist_answer")
    assert actions.index("persist_answer") < actions.index("allocate_port")
    assert actions.index("allocate_port") < actions.index("gateway:POST:/v1/sessions/start")
    arrived.assert_awaited_once()
    arrived_call_id, arrived_admission = arrived.await_args.args
    assert arrived_call_id == "inbound-1"
    assert arrived_admission["call_id"] == _allowed()["call_id"]
    assert arrived_admission["called_did"] == "+15557778888"
    assert arrived_admission["caller_ani"] == "+15553334444"
    assert adapter.get_inbound_admission("inbound-1")["call_id"] == _allowed()["call_id"]
    answer_hook.assert_awaited_once()


@pytest.mark.asyncio
async def test_answer_persistence_failure_hangs_up_before_media_and_finalizes():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    answer_hook = _enable_answer_persistence(
        adapter,
        AsyncMock(side_effect=ConnectionError("Redis promotion failed")),
    )
    adapter._alloc_rtp_port = AsyncMock(
        side_effect=AssertionError("media allocation must remain fenced")
    )

    async def ari(method, path, **kwargs):
        if method == "DELETE" and path == "/channels/inbound-1" and kwargs.get("return_status"):
            return 404, {}
        return {}

    adapter._ari = ari

    await adapter._on_stasis_start("inbound-1", _event())

    answer_hook.assert_awaited_once()
    adapter._alloc_rtp_port.assert_not_awaited()
    finalizer.assert_awaited_once()
    assert finalizer.await_args.kwargs == {
        "terminal_status": "failed",
        "duration_seconds": 1,
        "reason": "media_setup_failed",
        "release_only": False,
    }


@pytest.mark.asyncio
async def test_ambiguous_answer_request_failure_finalizes_never_releases():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    adapter._alloc_rtp_port = AsyncMock(
        side_effect=AssertionError("media allocation must remain fenced")
    )

    async def ari(method, path, **kwargs):
        if method == "POST" and path == "/channels/inbound-1/answer":
            raise TimeoutError("response lost after possible Answer commit")
        if method == "DELETE" and path == "/channels/inbound-1" and kwargs.get("return_status"):
            return 404, {}
        return {}

    adapter._ari = ari

    await adapter._on_stasis_start("inbound-1", _event())

    adapter._alloc_rtp_port.assert_not_awaited()
    finalizer.assert_awaited_once()
    assert finalizer.await_args.kwargs == {
        "terminal_status": "failed",
        "duration_seconds": 1,
        "reason": "process_restart_answer_ambiguous",
        "release_only": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer_status", "expected_reason", "expected_release_only"),
    [
        (404, "media_setup_failed", True),
        (409, "process_restart_answer_ambiguous", False),
        (500, "process_restart_answer_ambiguous", False),
        (503, "process_restart_answer_ambiguous", False),
    ],
)
async def test_answer_http_response_only_releases_when_noncommit_is_proven(
    answer_status,
    expected_reason,
    expected_release_only,
):
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)

    async def ari(method, path, **kwargs):
        if method == "POST" and path == "/channels/inbound-1/answer":
            raise _AriResponseError(method, path, answer_status, "test response")
        if method == "DELETE" and path == "/channels/inbound-1" and kwargs.get("return_status"):
            return 404, {}
        return {}

    adapter._ari = ari

    await adapter._on_stasis_start("inbound-1", _event())

    finalizer.assert_awaited_once()
    assert finalizer.await_args.kwargs["reason"] == expected_reason
    assert finalizer.await_args.kwargs["release_only"] is expected_release_only
    assert finalizer.await_args.kwargs["duration_seconds"] == (0 if expected_release_only else 1)


@pytest.mark.asyncio
async def test_crash_after_answer_2xx_leaves_durable_ambiguous_intent(
    monkeypatch,
):
    from app.domain.services.telephony import state_backend

    class SimulatedProcessCrash(BaseException):
        pass

    intents: list[dict] = []

    class State:
        async def register_answer_intent_cleanup_obligation(
            self,
            call_id,
            **kwargs,
        ):
            intents.append({"call_id": call_id, **kwargs})

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: State())
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    answer_hook = _enable_answer_persistence(
        adapter,
        AsyncMock(side_effect=SimulatedProcessCrash()),
    )
    adapter._ari = AsyncMock(return_value={})
    adapter._alloc_rtp_port = AsyncMock(
        side_effect=AssertionError("media cannot precede durable Answer confirmation")
    )

    with pytest.raises(SimulatedProcessCrash):
        await adapter._on_stasis_start("inbound-1", _event())

    answer_hook.assert_awaited_once()
    adapter._alloc_rtp_port.assert_not_awaited()
    finalizer.assert_not_awaited()
    assert len(intents) == 1
    assert intents[0]["call_id"] == "inbound-1"
    assert intents[0]["durable_call_id"] == _allowed()["call_id"]
    assert intents[0]["answer_requested_at"]
    cached = adapter.get_inbound_admission("inbound-1")
    assert cached is not None
    assert cached["_answer_intent_at_monotonic"] > 0
    assert cached["_answer_intent_at_utc"]
    assert cached["_answered_at_monotonic"] > 0
    assert cached["_answered_at_utc"]
    assert adapter._answered_elapsed_seconds(cached) >= 1


@pytest.mark.asyncio
async def test_disconnect_cancels_post_answer_handoff_and_cleans_owned_channel():
    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    actions: list[str] = []
    handoff_started = asyncio.Event()
    handoff_cancelled = asyncio.Event()
    never_register = asyncio.Event()

    async def finalize(*_args, **_kwargs):
        actions.append("finalize")

    async def lifecycle_handoff(_channel_id, _admission):
        handoff_started.set()
        try:
            await never_register.wait()
        except asyncio.CancelledError:
            handoff_cancelled.set()
            raise

    async def ari(method, path, **kwargs):
        actions.append(f"ari:{method}:{path}")
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def gateway(method, path, **kwargs):
        actions.append(f"gateway:{method}:{path}")
        return {}

    adapter.set_inbound_admission_finalizer(AsyncMock(side_effect=finalize))
    _enable_answer_persistence(adapter)
    adapter._on_new_call = lifecycle_handoff
    adapter._ari = ari
    adapter._gateway = gateway
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._release_rtp_port = AsyncMock()
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))

    await adapter._on_stasis_start("inbound-1", _event())
    await handoff_started.wait()

    # The caller is answered and adapter media exists, but lifecycle has not
    # registered a VoiceSession yet: this is the ownership-loss race window.
    assert "inbound-1" in adapter._active_sessions
    assert "inbound-1" in adapter._inbound_handoff_tasks

    await adapter.disconnect()

    assert handoff_cancelled.is_set()
    assert "inbound-1" not in adapter._inbound_handoff_tasks
    assert "inbound-1" not in adapter._active_sessions
    assert "inbound-1" not in adapter._ext_channels
    assert "inbound-1" not in adapter._bridges
    assert "inbound-1" not in adapter._gateway_sessions
    assert "inbound-1" not in adapter._inbound_cleanup_pending
    assert adapter.get_inbound_admission("inbound-1") is None

    cleanup_order = [
        actions.index("gateway:POST:/v1/sessions/stop"),
        actions.index("ari:DELETE:/channels/external-1"),
        actions.index("ari:DELETE:/channels/inbound-1"),
        actions.index("ari:DELETE:/bridges/bridge-1"),
        actions.index("finalize"),
    ]
    assert cleanup_order == sorted(cleanup_order)
    adapter._release_rtp_port.assert_awaited_once_with(32000)


def test_pending_handoff_fence_refuses_non_inbound_or_unadmitted_channels():
    adapter = AsteriskAdapter()
    adapter._active_sessions["outbound-1"] = {
        "session_id": "gateway-out",
        "listen_port": 32000,
        "bridge_id": "bridge-out",
        "direction": "outbound",
    }
    adapter._inbound_admissions["outbound-1"] = _allowed("outbound-1")
    adapter._active_sessions["unknown-inbound"] = {
        "session_id": "gateway-unknown",
        "listen_port": 32001,
        "bridge_id": "bridge-unknown",
        "direction": "inbound",
    }

    assert not adapter.reject_pending_inbound_handoff("outbound-1", reason="ownership_lost")
    assert not adapter.reject_pending_inbound_handoff("unknown-inbound", reason="ownership_lost")
    assert set(adapter._active_sessions) == {"outbound-1", "unknown-inbound"}
    assert not adapter._preanswer_hangup_tasks


@pytest.mark.asyncio
async def test_denied_inbound_hangs_up_without_answer_or_media():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(
        AsyncMock(return_value={"allowed": False, "reason": "unknown_did"})
    )
    adapter.set_inbound_admission_finalizer(AsyncMock())
    _enable_answer_persistence(adapter)
    adapter._ari = AsyncMock(side_effect=AssertionError("ARI must not be used"))
    adapter._alloc_rtp_port = AsyncMock(side_effect=AssertionError("media must not be allocated"))
    adapter._gateway = AsyncMock(side_effect=AssertionError("gateway must not start"))
    adapter.hangup_many_confirmed = AsyncMock(return_value=True)

    await adapter._on_stasis_start("inbound-1", _event())
    for _ in range(20):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)

    adapter.hangup_many_confirmed.assert_awaited_once_with(
        ("inbound-1",),
        fence_root=False,
        reason_code=1,
    )
    assert adapter.get_inbound_admission("inbound-1") is None
    assert "inbound-1" not in adapter._inbound_cleanup_pending


@pytest.mark.asyncio
async def test_unclaimed_denial_registers_durable_retry_until_absence_proof(
    monkeypatch,
):
    from app.domain.services.telephony import state_backend

    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(
        AsyncMock(return_value={"allowed": False, "reason": "unknown_did"})
    )
    registered: list[tuple[str, str]] = []
    acknowledged: list[str] = []
    order: list[str] = []
    first_attempt = asyncio.Event()
    second_attempt = asyncio.Event()
    allow_absence = asyncio.Event()
    attempts = 0

    class State:
        async def register_cleanup_obligation(self, call_id, *, state, **_kwargs):
            registered.append((call_id, state))

        async def acknowledge_orphan_recovery(self, call_id):
            order.append("ack")
            acknowledged.append(call_id)

    async def finalize(call_id, admission, **kwargs):
        assert call_id == "unclaimed-1"
        assert admission == {}
        assert kwargs["release_only"] is True
        order.append("global_release_finalizer")

    adapter.set_inbound_admission_finalizer(finalize)
    _enable_answer_persistence(adapter)

    async def confirmed(call_ids, *, fence_root=True, reason_code=None):
        nonlocal attempts
        assert call_ids == ("unclaimed-1",)
        assert fence_root is False
        assert reason_code == 1
        attempts += 1
        if attempts == 1:
            first_attempt.set()
            return False
        second_attempt.set()
        await allow_absence.wait()
        return True

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: State())
    adapter.hangup_many_confirmed = AsyncMock(side_effect=confirmed)

    await adapter._on_stasis_start("unclaimed-1", _event("unclaimed-1"))
    await asyncio.wait_for(first_attempt.wait(), timeout=1)
    await asyncio.wait_for(second_attempt.wait(), timeout=1)

    assert registered == [("unclaimed-1", "termination_pending")]
    assert acknowledged == []
    assert order == []
    assert "unclaimed-1" in adapter._unclaimed_hangup_tasks
    assert "unclaimed-1" in adapter._inbound_cleanup_pending
    assert adapter.get_inbound_call_meta("unclaimed-1") is not None

    allow_absence.set()
    for _ in range(30):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)

    assert acknowledged == ["unclaimed-1"]
    assert order == ["global_release_finalizer", "ack"]
    assert "unclaimed-1" not in adapter._inbound_cleanup_pending
    assert adapter.get_inbound_call_meta("unclaimed-1") is None


@pytest.mark.asyncio
async def test_missing_admission_callback_fails_closed():
    adapter = AsteriskAdapter()
    adapter._ari = AsyncMock(side_effect=AssertionError("ARI must not be used"))
    adapter._alloc_rtp_port = AsyncMock(side_effect=AssertionError("media must not be allocated"))
    adapter.hangup_many_confirmed = AsyncMock(return_value=True)

    await adapter._on_stasis_start("inbound-1", _event())
    for _ in range(20):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)

    adapter.hangup_many_confirmed.assert_awaited_once_with(
        ("inbound-1",),
        fence_root=False,
        reason_code=42,
    )


@pytest.mark.asyncio
async def test_missing_finalizer_denies_before_reserving():
    adapter = AsteriskAdapter()
    admit = AsyncMock(return_value=_allowed())
    adapter.set_inbound_admission_callback(admit)
    adapter._ari = AsyncMock(side_effect=AssertionError("ARI must not be used"))
    adapter.hangup_many_confirmed = AsyncMock(return_value=True)

    await adapter._on_stasis_start("inbound-1", _event())
    for _ in range(20):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)

    admit.assert_not_awaited()
    adapter.hangup_many_confirmed.assert_awaited_once_with(
        ("inbound-1",),
        fence_root=False,
        reason_code=42,
    )


@pytest.mark.asyncio
async def test_missing_answer_persistence_hook_denies_before_reserving():
    adapter = AsteriskAdapter()
    admit = AsyncMock(return_value=_allowed())
    adapter.set_inbound_admission_callback(admit)
    adapter.set_inbound_admission_finalizer(AsyncMock())
    adapter._ari = AsyncMock(side_effect=AssertionError("ARI must not be used"))
    adapter.hangup_many_confirmed = AsyncMock(return_value=True)

    await adapter._on_stasis_start("inbound-1", _event())
    for _ in range(20):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)

    admit.assert_not_awaited()
    adapter.hangup_many_confirmed.assert_awaited_once_with(
        ("inbound-1",),
        fence_root=False,
        reason_code=42,
    )


@pytest.mark.asyncio
async def test_duplicate_stasis_start_admits_only_once():
    adapter = AsteriskAdapter()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def admit(channel_id, metadata):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"allowed": False, "reason": "maintenance"}

    adapter.set_inbound_admission_callback(admit)
    adapter.set_inbound_admission_finalizer(AsyncMock())
    _enable_answer_persistence(adapter)
    adapter.hangup_many_confirmed = AsyncMock(return_value=True)

    first = asyncio.create_task(adapter._on_stasis_start("inbound-1", _event()))
    await entered.wait()
    await adapter._on_stasis_start("inbound-1", _event())
    release.set()
    await first
    for _ in range(20):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)

    assert calls == 1
    adapter.hangup_many_confirmed.assert_awaited_once_with(
        ("inbound-1",),
        fence_root=False,
        reason_code=21,
    )


@pytest.mark.asyncio
async def test_media_setup_failure_releases_admission_once():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    actions: list[str] = []

    async def finalize(*_args, **_kwargs):
        actions.append("finalize")

    finalizer = AsyncMock(side_effect=finalize)
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)

    async def ari(method, path, **kwargs):
        # Answer succeeds; cleanup DELETE is also harmless.
        actions.append(f"ari:{method}:{path}")
        if method == "DELETE" and path == "/channels/inbound-1" and kwargs.get("return_status"):
            return 404, {}
        return {}

    adapter._ari = ari
    adapter._alloc_rtp_port = AsyncMock(side_effect=RuntimeError("no RTP ports"))

    await adapter._on_stasis_start("inbound-1", _event())

    finalizer.assert_awaited_once()
    args, kwargs = finalizer.await_args
    assert args[0] == "inbound-1"
    assert args[1]["call_id"] == _allowed()["call_id"]
    assert kwargs == {
        "terminal_status": "failed",
        "duration_seconds": 1,
        "reason": "media_setup_failed",
        "release_only": False,
    }
    assert actions.index("ari:DELETE:/channels/inbound-1") < actions.index("finalize")
    assert adapter.get_inbound_admission("inbound-1") is None


@pytest.mark.asyncio
async def test_media_setup_failure_retries_unconfirmed_hangup_before_release():
    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    delete_attempts = 0

    async def ari(method, path, **kwargs):
        nonlocal delete_attempts
        if method == "DELETE" and path == "/channels/inbound-1":
            delete_attempts += 1
            if delete_attempts == 1:
                raise RuntimeError("ARI temporarily unavailable")
            if kwargs.get("return_status"):
                return 404, {}
        return {}

    adapter._ari = ari
    adapter._alloc_rtp_port = AsyncMock(side_effect=RuntimeError("no RTP ports"))

    await adapter._on_stasis_start("inbound-1", _event())

    # The first unconfirmed delete must retain both durable admission and quota.
    finalizer.assert_not_awaited()
    assert adapter.get_inbound_admission("inbound-1") is not None

    for _ in range(20):
        if finalizer.await_count and "inbound-1" not in adapter._preanswer_hangup_tasks:
            break
        await asyncio.sleep(0)

    assert delete_attempts >= 2
    finalizer.assert_awaited_once()
    assert adapter.get_inbound_admission("inbound-1") is None
    assert "inbound-1" not in adapter._preanswer_hangup_tasks


@pytest.mark.asyncio
async def test_media_setup_cleanup_proves_every_resource_before_release():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    actions: list[str] = []

    async def finalize(*_args, **_kwargs):
        actions.append("finalize")

    adapter.set_inbound_admission_finalizer(AsyncMock(side_effect=finalize))
    _enable_answer_persistence(adapter)

    async def ari(method, path, **kwargs):
        actions.append(f"ari:{method}:{path}")
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        if method == "DELETE" and path == "/channels/inbound-1" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def gateway(method, path, **kwargs):
        actions.append(f"gateway:{method}:{path}")
        if path == "/v1/sessions/start":
            raise RuntimeError("gateway start failed")
        return {}

    async def release_port(port):
        actions.append(f"release_port:{port}")

    adapter._ari = ari
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter._gateway = gateway
    adapter._release_rtp_port = AsyncMock(side_effect=release_port)

    await adapter._on_stasis_start("inbound-1", _event())

    expected_cleanup = [
        "gateway:POST:/v1/sessions/stop",
        "ari:DELETE:/channels/external-1",
        "ari:DELETE:/channels/inbound-1",
        "ari:DELETE:/bridges/bridge-1",
        "release_port:32000",
        "finalize",
    ]
    positions = [actions.index(item) for item in expected_cleanup]
    assert positions == sorted(positions)
    assert adapter.get_inbound_admission("inbound-1") is None
    assert "inbound-1" not in adapter._inbound_cleanup_pending


@pytest.mark.asyncio
async def test_cancelled_media_setup_still_cleans_before_release():
    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    actions: list[str] = []
    gateway_start_entered = asyncio.Event()
    never_finish_start = asyncio.Event()

    async def finalize(*_args, **_kwargs):
        actions.append("finalize")

    adapter.set_inbound_admission_finalizer(AsyncMock(side_effect=finalize))
    _enable_answer_persistence(adapter)

    async def ari(method, path, **kwargs):
        actions.append(f"ari:{method}:{path}")
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        if method == "DELETE" and path == "/channels/inbound-1" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def gateway(method, path, **kwargs):
        actions.append(f"gateway:{method}:{path}")
        if path == "/v1/sessions/start":
            gateway_start_entered.set()
            await never_finish_start.wait()
        return {}

    adapter._ari = ari
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter._gateway = gateway
    adapter._release_rtp_port = AsyncMock()

    setup = asyncio.create_task(adapter._on_stasis_start("inbound-1", _event()))
    await gateway_start_entered.wait()
    setup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await setup

    for _ in range(30):
        if "finalize" in actions and "inbound-1" not in adapter._preanswer_hangup_tasks:
            break
        await asyncio.sleep(0)

    expected_cleanup = [
        "gateway:POST:/v1/sessions/stop",
        "ari:DELETE:/channels/external-1",
        "ari:DELETE:/channels/inbound-1",
        "ari:DELETE:/bridges/bridge-1",
        "finalize",
    ]
    positions = [actions.index(item) for item in expected_cleanup]
    assert positions == sorted(positions)
    assert adapter.get_inbound_admission("inbound-1") is None


@pytest.mark.asyncio
async def test_active_call_terminal_is_owned_by_lifecycle_not_adapter_release():
    adapter = AsteriskAdapter()
    finalizer = AsyncMock()
    ended = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    adapter.set_call_end_callback(ended)
    adapter._inbound_admissions["inbound-1"] = _allowed()
    adapter._active_sessions["inbound-1"] = {
        "session_id": "gateway-1",
        "listen_port": 32000,
        "bridge_id": "bridge-1",
        "direction": "inbound",
    }
    adapter._gateway_sessions["inbound-1"] = "gateway-1"
    adapter._bridges["inbound-1"] = "bridge-1"
    adapter._ari = AsyncMock(return_value={})
    adapter._gateway = AsyncMock(return_value={})
    adapter._release_rtp_port = AsyncMock()
    adapter.hangup_confirmed = AsyncMock(return_value=True)

    await adapter._on_stasis_end("inbound-1", "StasisEnd")
    await asyncio.sleep(0)

    # Once media became active, adapter teardown only drops its cached copy;
    # the lifecycle callback owns measured-duration finalization.
    finalizer.assert_not_awaited()
    ended.assert_awaited_once_with("inbound-1")
    assert adapter.get_inbound_admission("inbound-1") is None


@pytest.mark.asyncio
async def test_bridge_timeout_after_create_cleans_requested_deterministic_id():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    requested_bridge_id = ""
    deleted_paths: list[str] = []

    async def ari(method, path, **kwargs):
        nonlocal requested_bridge_id
        if method == "POST" and path == "/bridges":
            requested_bridge_id = kwargs["params"]["bridgeId"]
            raise TimeoutError("response lost after Asterisk committed create")
        if method == "DELETE":
            deleted_paths.append(path)
            if path == "/channels/inbound-1" and kwargs.get("return_status"):
                return 404, {}
        return {}

    adapter._ari = ari
    adapter._gateway = AsyncMock(return_value={})
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._release_rtp_port = AsyncMock()

    await adapter._on_stasis_start("inbound-1", _event())

    assert requested_bridge_id.startswith("talky-inbound-bridge-")
    assert f"/bridges/{requested_bridge_id}" in deleted_paths
    assert "/channels/inbound-1" in deleted_paths
    adapter._release_rtp_port.assert_awaited_once_with(32000)
    finalizer.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_media_timeout_after_create_cleans_requested_channel_id():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    requested_bridge_id = ""
    requested_media_id = ""
    deleted_paths: list[str] = []

    async def ari(method, path, **kwargs):
        nonlocal requested_bridge_id, requested_media_id
        if method == "POST" and path == "/bridges":
            requested_bridge_id = kwargs["params"]["bridgeId"]
            return {"id": requested_bridge_id}
        if method == "POST" and path == "/channels/externalMedia":
            requested_media_id = kwargs["params"]["channelId"]
            raise TimeoutError("response lost after Asterisk committed create")
        if method == "DELETE":
            deleted_paths.append(path)
            if path == "/channels/inbound-1" and kwargs.get("return_status"):
                return 404, {}
        return {}

    adapter._ari = ari
    adapter._gateway = AsyncMock(return_value={})
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._release_rtp_port = AsyncMock()

    await adapter._on_stasis_start("inbound-1", _event())

    assert requested_media_id.startswith("talky-inbound-media-")
    assert f"/channels/{requested_media_id}" in deleted_paths
    assert f"/bridges/{requested_bridge_id}" in deleted_paths
    assert "/channels/inbound-1" in deleted_paths
    adapter._release_rtp_port.assert_awaited_once_with(32000)
    finalizer.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_stasis_start_is_fenced_while_cleanup_owns_admission():
    adapter = AsteriskAdapter()
    admit = AsyncMock(return_value=_allowed())
    adapter.set_inbound_admission_callback(admit)
    adapter.set_inbound_admission_finalizer(AsyncMock())
    _enable_answer_persistence(adapter)
    adapter._inbound_admissions["inbound-1"] = _allowed()
    adapter._inbound_cleanup_pending.add("inbound-1")
    adapter._ari = AsyncMock(side_effect=AssertionError("must not re-enter ARI"))

    await adapter._on_stasis_start("inbound-1", _event())
    adapter._schedule_inbound_start("inbound-1", _event())

    admit.assert_not_awaited()
    assert "inbound-1" not in adapter._inbound_setup_tasks


@pytest.mark.asyncio
async def test_stasis_end_during_cleanup_never_dispatches_lifecycle_finalizer():
    adapter = AsteriskAdapter()
    ended = AsyncMock()
    adapter.set_call_end_callback(ended)
    adapter._inbound_admissions["inbound-1"] = _allowed()
    adapter._inbound_cleanup_pending.add("inbound-1")
    adapter._active_sessions["inbound-1"] = {
        "session_id": "gateway-1",
        "listen_port": 32000,
        "bridge_id": "bridge-1",
        "direction": "inbound",
    }
    adapter._ari = AsyncMock(side_effect=AssertionError("cleanup task owns ARI"))
    adapter._gateway = AsyncMock(side_effect=AssertionError("cleanup task owns gateway"))

    await adapter._on_stasis_end("inbound-1", "StasisEnd")
    await asyncio.sleep(0)

    ended.assert_not_awaited()
    assert "inbound-1" in adapter._active_sessions
    assert adapter.get_inbound_admission("inbound-1") is not None


@pytest.mark.asyncio
async def test_bridge_delete_422_is_unconfirmed_and_retried_before_release():
    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    bridge_delete_attempts = 0

    async def ari(method, path, **kwargs):
        nonlocal bridge_delete_attempts
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        if method == "DELETE" and path == "/bridges/bridge-1":
            assert 422 not in kwargs["ok"]
            bridge_delete_attempts += 1
            if bridge_delete_attempts == 1:
                raise RuntimeError("ARI returned 422")
        if method == "DELETE" and path == "/channels/inbound-1" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def gateway(method, path, **kwargs):
        if path == "/v1/sessions/start":
            raise RuntimeError("force cleanup")
        return {}

    adapter._ari = ari
    adapter._gateway = gateway
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter._release_rtp_port = AsyncMock()

    await adapter._on_stasis_start("inbound-1", _event())

    finalizer.assert_not_awaited()
    adapter._release_rtp_port.assert_not_awaited()
    for _ in range(30):
        if finalizer.await_count and "inbound-1" not in adapter._preanswer_hangup_tasks:
            break
        await asyncio.sleep(0)

    assert bridge_delete_attempts == 2
    adapter._release_rtp_port.assert_awaited_once_with(32000)
    finalizer.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_drains_cancelled_setup_cleanup_before_closing_ari():
    adapter = AsteriskAdapter()
    adapter._connected_flag = True
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    actions: list[str] = []
    gateway_start_entered = asyncio.Event()
    never_finish_start = asyncio.Event()
    parent_delete_attempts = 0

    class Session:
        closed = False

        async def close(self):
            actions.append("session_close")
            self.closed = True

    session = Session()
    adapter._session = session

    async def finalize(*_args, **_kwargs):
        actions.append("finalize")

    adapter.set_inbound_admission_finalizer(AsyncMock(side_effect=finalize))
    _enable_answer_persistence(adapter)

    async def ari(method, path, **kwargs):
        nonlocal parent_delete_attempts
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        if method == "DELETE":
            actions.append(f"delete:{path}:session_closed={session.closed}")
        if method == "DELETE" and path == "/channels/inbound-1":
            parent_delete_attempts += 1
            if parent_delete_attempts == 1:
                raise RuntimeError("temporary ARI failure")
            if kwargs.get("return_status"):
                return 404, {}
        return {}

    async def gateway(method, path, **kwargs):
        if path == "/v1/sessions/start":
            gateway_start_entered.set()
            await never_finish_start.wait()
        elif path == "/v1/sessions/stop":
            actions.append(f"gateway_stop:session_closed={session.closed}")
        return {}

    async def release_port(port):
        actions.append(f"release_port:{port}")

    adapter._ari = ari
    adapter._gateway = gateway
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter._release_rtp_port = AsyncMock(side_effect=release_port)

    adapter._schedule_inbound_start("inbound-1", _event())
    await gateway_start_entered.wait()
    await adapter.disconnect()

    assert session.closed is True
    assert parent_delete_attempts == 2
    assert all("session_closed=False" in item for item in actions if "delete:" in item)
    assert actions.index("finalize") < actions.index("session_close")
    assert actions.index("release_port:32000") < actions.index("finalize")
    assert not adapter._inbound_setup_tasks
    assert not adapter._preanswer_hangup_tasks
    assert not adapter._inbound_cleanup_pending
    assert adapter.get_inbound_admission("inbound-1") is None


@pytest.mark.asyncio
async def test_terminal_during_setup_delegates_to_setup_cleanup_once():
    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    ended = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    adapter.set_call_end_callback(ended)
    gateway_start_entered = asyncio.Event()
    never_finish_start = asyncio.Event()
    deleted_paths: list[str] = []

    async def ari(method, path, **kwargs):
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        if method == "DELETE":
            deleted_paths.append(path)
            if path == "/channels/inbound-1" and kwargs.get("return_status"):
                return 404, {}
        return {}

    async def gateway(method, path, **kwargs):
        if path == "/v1/sessions/start":
            gateway_start_entered.set()
            await never_finish_start.wait()
        return {}

    adapter._ari = ari
    adapter._gateway = gateway
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter._release_rtp_port = AsyncMock()

    adapter._schedule_inbound_start("inbound-1", _event())
    await gateway_start_entered.wait()
    for event_type in ("StasisEnd", "ChannelHangupRequest", "ChannelDestroyed"):
        await adapter._handle_ari_event({"type": event_type, "channel": {"id": "inbound-1"}})

    for _ in range(50):
        if (
            finalizer.await_count
            and not adapter._terminal_cleanup_tasks
            and not adapter._preanswer_hangup_tasks
        ):
            break
        await asyncio.sleep(0)

    finalizer.assert_awaited_once()
    ended.assert_not_awaited()
    assert deleted_paths.count("/channels/external-1") == 1
    # ChannelDestroyed itself is the hard parent-absence proof, so cleanup
    # must not issue a redundant parent DELETE.
    assert deleted_paths.count("/channels/inbound-1") == 0
    assert deleted_paths.count("/bridges/bridge-1") == 1
    adapter._release_rtp_port.assert_awaited_once_with(32000)
    assert not adapter._inbound_setup_tasks
    assert not adapter._terminal_cleanup_tasks
    assert not adapter._inbound_cleanup_pending


@pytest.mark.asyncio
async def test_terminal_event_burst_claims_active_teardown_once():
    adapter = AsteriskAdapter()
    ended = AsyncMock()
    adapter.set_call_end_callback(ended)
    adapter._inbound_admissions["inbound-1"] = _allowed()
    adapter._active_sessions["inbound-1"] = {
        "session_id": "gateway-1",
        "listen_port": 32000,
        "bridge_id": "bridge-1",
        "direction": "inbound",
    }
    adapter._gateway_sessions["inbound-1"] = "gateway-1"
    adapter._ext_channels["inbound-1"] = "external-1"
    adapter._bridges["inbound-1"] = "bridge-1"
    gateway_stop_entered = asyncio.Event()
    release_gateway_stop = asyncio.Event()
    deleted_paths: list[str] = []

    async def gateway(method, path, **kwargs):
        if path == "/v1/sessions/stop":
            gateway_stop_entered.set()
            await release_gateway_stop.wait()
        return {}

    async def ari(method, path, **kwargs):
        if method == "DELETE":
            deleted_paths.append(path)
        return {}

    adapter._gateway = gateway
    adapter._ari = ari
    adapter._release_rtp_port = AsyncMock()

    for event_type in ("StasisEnd", "ChannelHangupRequest", "ChannelDestroyed"):
        await adapter._handle_ari_event({"type": event_type, "channel": {"id": "inbound-1"}})
    await gateway_stop_entered.wait()
    release_gateway_stop.set()

    for _ in range(30):
        if ended.await_count and not adapter._terminal_cleanup_tasks:
            break
        await asyncio.sleep(0)

    ended.assert_awaited_once_with("inbound-1")
    assert deleted_paths.count("/channels/external-1") == 1
    assert deleted_paths.count("/bridges/bridge-1") == 1
    assert deleted_paths.count("/channels/inbound-1") == 1
    adapter._release_rtp_port.assert_awaited_once_with(32000)
    assert not adapter._terminal_cleanup_tasks


@pytest.mark.asyncio
async def test_disconnect_waits_for_tracked_terminal_cleanup_before_session_close():
    adapter = AsteriskAdapter()
    adapter._connected_flag = True
    adapter._active_sessions["inbound-1"] = {
        "session_id": "gateway-1",
        "listen_port": 32000,
        "bridge_id": "bridge-1",
        "direction": "inbound",
    }
    adapter._gateway_sessions["inbound-1"] = "gateway-1"
    gateway_stop_entered = asyncio.Event()
    release_gateway_stop = asyncio.Event()

    class Session:
        closed = False

        async def close(self):
            self.closed = True

    session = Session()
    adapter._session = session

    async def gateway(method, path, **kwargs):
        if path == "/v1/sessions/stop":
            gateway_stop_entered.set()
            await release_gateway_stop.wait()
        return {}

    adapter._gateway = gateway
    adapter._ari = AsyncMock(return_value={})
    adapter._release_rtp_port = AsyncMock()

    await adapter._handle_ari_event({"type": "ChannelDestroyed", "channel": {"id": "inbound-1"}})
    await gateway_stop_entered.wait()
    disconnect_task = asyncio.create_task(adapter.disconnect())
    await asyncio.sleep(0)

    assert disconnect_task.done() is False
    assert session.closed is False
    release_gateway_stop.set()
    await disconnect_task

    assert session.closed is True
    assert not adapter._terminal_cleanup_tasks


@pytest.mark.asyncio
async def test_forced_disconnect_is_bounded_and_retains_unconfirmed_identity():
    adapter = AsteriskAdapter()
    adapter._connected_flag = True
    adapter._inbound_admissions["inbound-unconfirmed"] = _allowed()
    adapter._inbound_cleanup_pending.add("inbound-unconfirmed")
    never_finishes = asyncio.Event()

    async def retry_owner():
        await never_finishes.wait()

    retry_task = asyncio.create_task(retry_owner())
    adapter._preanswer_hangup_tasks["inbound-unconfirmed"] = retry_task

    class Session:
        closed = False

        async def close(self):
            self.closed = True

    session = Session()
    adapter._session = session

    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await adapter.disconnect(
        drain_timeout_s=0.05,
        force_handoff=True,
    )
    elapsed = loop.time() - started

    assert elapsed < 0.5
    assert result["status"] == "deferred"
    assert "inbound-unconfirmed" in result["deferred_call_ids"]
    assert "inbound-unconfirmed" in adapter._inbound_admissions
    assert "inbound-unconfirmed" in adapter._inbound_cleanup_pending
    assert retry_task.cancelled()
    assert session.closed is True


@pytest.mark.asyncio
async def test_disconnect_fence_blocks_all_new_stasis_start_routes():
    adapter = AsteriskAdapter()
    adapter._stop_event.set()
    inbound_scheduled: list[str] = []
    outbound_started = AsyncMock()
    adapter._schedule_inbound_start = lambda channel_id, event: inbound_scheduled.append(channel_id)
    adapter._on_outbound_stasis_start = outbound_started
    adapter.hangup = AsyncMock()

    events = [
        _event("inbound-1"),
        {
            "type": "StasisStart",
            "args": ["outbound"],
            "channel": {"id": "outbound-1", "name": "PJSIP/carrier-out"},
        },
        {
            "type": "StasisStart",
            "args": ["transfer_target", "inbound-1"],
            "channel": {"id": "transfer-1", "name": "PJSIP/carrier-xfer"},
        },
    ]
    for event in events:
        await adapter._handle_ari_event(event)
    await asyncio.sleep(0)

    assert inbound_scheduled == []
    outbound_started.assert_not_awaited()
    adapter.hangup.assert_not_awaited()
    assert not adapter._inbound_setup_tasks
    assert not adapter._transfers_by_target


@pytest.mark.asyncio
async def test_terminal_before_lifecycle_acceptance_uses_adapter_cleanup_only():
    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    finalizer = AsyncMock()
    ended = AsyncMock()
    adapter.set_inbound_admission_finalizer(finalizer)
    _enable_answer_persistence(adapter)
    adapter.set_call_end_callback(ended)
    handoff_started = asyncio.Event()
    handoff_cancelled = asyncio.Event()
    never_accept = asyncio.Event()

    async def pending_handoff(_channel_id, _admission):
        handoff_started.set()
        try:
            await never_accept.wait()
        except asyncio.CancelledError:
            handoff_cancelled.set()
            raise

    async def ari(method, path, **kwargs):
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        return {}

    adapter._on_new_call = pending_handoff
    adapter._ari = ari
    adapter._gateway = AsyncMock(return_value={})
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter._release_rtp_port = AsyncMock()

    await adapter._on_stasis_start("inbound-1", _event())
    await handoff_started.wait()
    assert "inbound-1" in adapter._active_sessions
    assert "inbound-1" not in adapter._inbound_handoff_accepted

    await adapter._handle_ari_event({"type": "StasisEnd", "channel": {"id": "inbound-1"}})
    await adapter._handle_ari_event({"type": "ChannelDestroyed", "channel": {"id": "inbound-1"}})
    for _ in range(50):
        if (
            finalizer.await_count
            and not adapter._terminal_cleanup_tasks
            and not adapter._preanswer_hangup_tasks
        ):
            break
        await asyncio.sleep(0)

    assert handoff_cancelled.is_set()
    finalizer.assert_awaited_once()
    ended.assert_not_awaited()
    assert "inbound-1" not in adapter._active_sessions
    assert "inbound-1" not in adapter._inbound_handoff_tasks
    assert "inbound-1" not in adapter._inbound_cleanup_pending
    assert adapter.get_inbound_admission("inbound-1") is None


@pytest.mark.asyncio
async def test_terminal_after_lifecycle_acceptance_uses_lifecycle_once():
    adapter = AsteriskAdapter()
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    accepted = asyncio.Event()
    finish_initialization = asyncio.Event()
    ended_calls: list[str] = []

    async def accepted_handoff(channel_id, _admission):
        # Production accepts only after every cancellable initialization await.
        await finish_initialization.wait()
        assert adapter.accept_inbound_handoff(channel_id)
        accepted.set()

    async def lifecycle_end(channel_id):
        ended_calls.append(channel_id)
        adapter.pop_inbound_admission(channel_id)

    async def ari(method, path, **kwargs):
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-1"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-1"}
        return {}

    adapter._on_new_call = accepted_handoff
    adapter.set_call_end_callback(lifecycle_end)
    adapter.set_inbound_admission_finalizer(AsyncMock())
    _enable_answer_persistence(adapter)
    adapter._ari = ari
    adapter._gateway = AsyncMock(return_value={})
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter._release_rtp_port = AsyncMock()

    await adapter._on_stasis_start("inbound-1", _event())
    finish_initialization.set()
    await accepted.wait()
    for _ in range(20):
        if "inbound-1" not in adapter._inbound_handoff_tasks:
            break
        await asyncio.sleep(0)
    assert "inbound-1" in adapter._inbound_handoff_accepted
    assert "inbound-1" not in adapter._inbound_handoff_tasks

    for event_type in ("StasisEnd", "ChannelDestroyed"):
        await adapter._handle_ari_event({"type": event_type, "channel": {"id": "inbound-1"}})
    for _ in range(30):
        if ended_calls and not adapter._terminal_cleanup_tasks:
            break
        await asyncio.sleep(0)

    assert ended_calls == ["inbound-1"]
    assert "inbound-1" not in adapter._inbound_handoff_tasks
    assert "inbound-1" not in adapter._inbound_handoff_accepted
    assert "inbound-1" not in adapter._active_sessions
    adapter._release_rtp_port.assert_awaited_once_with(32000)


@pytest.mark.asyncio
async def test_terminal_race_after_real_lifecycle_provisional_registration_unwinds_once(
    monkeypatch,
):
    """The adapter owns teardown until lifecycle's final acceptance boundary."""

    import app.core.container as container_module
    import app.domain.services.call_status as call_status
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module
    import app.domain.services.telephony.modes.caller_first as caller_first
    import app.domain.services.telephony.modes.user_first as user_first
    import app.services.scripts.knowledge.session_inject as knowledge_inject
    from app.domain.services.telephony import lifecycle
    from app.domain.services.voice_orchestrator import VoiceOrchestrator

    call_id = "inbound-provisional-race"
    durable_call_id = "11111111-1111-1111-1111-111111111111"
    admission = _allowed(call_id)
    admission["call_id"] = durable_call_id
    admission["opening_mode"] = "caller_first"
    admission["config_snapshot"]["route"] = {
        "max_call_duration_seconds": 60,
        "reservation_seconds": 60,
    }
    admission["config_snapshot"]["inbound_config"].update(
        {
            "opening_mode": "caller_first",
            "greeting": None,
            "transfer_policy": {},
            "qualification_config": {},
        }
    )

    never = asyncio.Event()
    pipeline_started = asyncio.Event()
    handoff_window_reached = asyncio.Event()
    audio_start_cancelled = asyncio.Event()
    heartbeat_started = asyncio.Event()
    deadline_started = asyncio.Event()
    guard_cancellations: list[str] = []
    cleanup_actions: list[str] = []
    durable_finalizations: list[object] = []
    global_releases: list[str] = []
    end_session_calls: list[object] = []

    class Provider:
        def __init__(self, name: str):
            self.name = name
            self.connect_calls = 0
            self.pre_connect_calls = 0
            self.cleanup_calls = 0

        async def connect_for_call(self, _call_id):
            self.connect_calls += 1

        async def pre_connect(self, _call_id):
            self.pre_connect_calls += 1

        async def cleanup(self):
            self.cleanup_calls += 1

    class Pipeline:
        def __init__(self):
            self.cancelled = 0
            self.cancel_active_turn_calls = 0

        async def start_pipeline(self, _call_session, _websocket):
            pipeline_started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise

        async def cancel_active_turn(self, _call_id):
            self.cancel_active_turn_calls += 1

    class MediaGateway:
        def __init__(self):
            self.started = 0
            self.ended = 0
            self.cleaned = 0

        async def on_call_started(self, _call_id, _metadata):
            self.started += 1

        async def on_audio_received(self, _call_id, _chunk):
            return None

        async def on_call_ended(self, _call_id, _reason):
            self.ended += 1

        async def cleanup(self):
            self.cleaned += 1

    class State:
        strict_ownership_active = False

        def __init__(self):
            self.sessions: dict[str, object] = {}
            self.gateway_sessions: dict[str, str] = {}

        @staticmethod
        def is_telephony_owner():
            return True

        def voice_session_count(self):
            return len(self.sessions)

        @staticmethod
        def has_ringing_warmup(_call_id):
            return False

        @staticmethod
        def get_ringing_event(_call_id):
            return None

        @staticmethod
        def pop_ringing_event(_call_id):
            return None

        @staticmethod
        async def register_cleanup_obligation(_call_id, **_kwargs):
            return None

        def set_voice_session(self, actual_call_id, session, **_kwargs):
            assert actual_call_id not in self.sessions
            self.sessions[actual_call_id] = session

        def get_voice_session(self, actual_call_id):
            return self.sessions.get(actual_call_id)

        def pop_voice_session(self, actual_call_id):
            return self.sessions.pop(actual_call_id, None)

        @staticmethod
        def get_first_speaker(_call_id):
            return None

        def set_call_id_for_gateway_session(self, session_id, actual_call_id):
            self.gateway_sessions[session_id] = actual_call_id

        def remove_gateway_sessions_for_call(self, actual_call_id):
            for session_id, owner in list(self.gateway_sessions.items()):
                if owner == actual_call_id:
                    self.gateway_sessions.pop(session_id, None)

        @staticmethod
        def drain_early_audio(_session_id):
            return []

    state = State()
    stt = Provider("stt")
    llm = Provider("llm")
    tts = Provider("tts")
    pipeline = Pipeline()
    media_gateway = MediaGateway()
    session_config = SimpleNamespace(session_type="telephony")
    voice_session = SimpleNamespace(
        call_id="voice-session-provisional",
        talklee_call_id="talklee-provisional",
        call_session=SimpleNamespace(call_id="voice-session-provisional"),
        stt_provider=stt,
        llm_provider=llm,
        tts_provider=tts,
        media_gateway=media_gateway,
        pipeline=pipeline,
        pipeline_task=None,
        realtime_bridge=None,
        realtime_session=None,
        event_repo=None,
        leg_id=None,
        config=session_config,
    )
    orchestrator = VoiceOrchestrator()

    async def create_voice_session(_config):
        orchestrator._active_sessions[voice_session.call_id] = voice_session
        return voice_session

    real_end_session = orchestrator.end_session

    async def end_session(session):
        end_session_calls.append(session)
        await real_end_session(session)

    orchestrator.create_voice_session = create_voice_session
    orchestrator.end_session = end_session

    async def guard(label, started):
        started.set()
        try:
            await never.wait()
        except asyncio.CancelledError:
            guard_cancellations.append(label)
            raise

    async def heartbeat_guard(_call_id, _admission):
        await guard("heartbeat", heartbeat_started)

    async def deadline_guard(_call_id, _duration, _answered_at):
        await guard("deadline", deadline_started)

    async def status_projection(*_args, **_kwargs):
        return None

    async def prepare_recording(_session):
        return True

    async def prewarm_llm(_session):
        return None

    async def durable_finalize(_self, request):
        durable_finalizations.append(request)
        cleanup_actions.append("durable_finalize")

    async def unexpected_release(*_args, **_kwargs):
        raise AssertionError("post-Answer cancellation must finalize, not release")

    async def release_global(_redis, *, call_id):
        global_releases.append(call_id)
        cleanup_actions.append("global_release")

    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0
    adapter.set_inbound_admission_callback(AsyncMock(return_value=admission))
    adapter.set_inbound_admission_finalizer(lifecycle._finalize_inbound_admission)
    _enable_answer_persistence(adapter)
    lifecycle_end = AsyncMock()
    adapter.set_call_end_callback(lifecycle_end)

    async def ari(method, path, **_kwargs):
        cleanup_actions.append(f"ari:{method}:{path}")
        if method == "POST" and path == "/bridges":
            return {"id": "bridge-provisional"}
        if method == "POST" and path == "/channels/externalMedia":
            return {"id": "external-provisional"}
        return {}

    async def gateway(method, path, **_kwargs):
        cleanup_actions.append(f"gateway:{method}:{path}")
        return {}

    async def blocked_audio_start(actual_call_id):
        assert actual_call_id == call_id
        await pipeline_started.wait()
        await heartbeat_started.wait()
        await deadline_started.wait()
        handoff_window_reached.set()
        try:
            await never.wait()
        except asyncio.CancelledError:
            audio_start_cancelled.set()
            raise

    adapter._ari = ari
    adapter._gateway = gateway
    adapter._alloc_rtp_port = AsyncMock(return_value=32000)
    adapter._release_rtp_port = AsyncMock()
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 41000))
    adapter.start_audio_stream = blocked_audio_start
    adapter._on_new_call = lifecycle._on_new_call

    container = SimpleNamespace(
        is_initialized=True,
        db_pool=object(),
        db_client=object(),
        redis=object(),
    )
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", status_projection)
    monkeypatch.setattr(global_concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "release", unexpected_release)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "finalize", durable_finalize)
    monkeypatch.setattr(caller_first, "prepare_inbound_recording", prepare_recording)
    monkeypatch.setattr(user_first, "prewarm_llm_pool", prewarm_llm)
    monkeypatch.setattr(knowledge_inject, "apply_pinned_campaign_knowledge", lambda *_a: None)
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda _call_id: None)
    monkeypatch.setattr(
        lifecycle,
        "_build_pinned_inbound_config",
        lambda *_args, **_kwargs: (
            session_config,
            admission["config_snapshot"]["campaign"],
        ),
    )
    monkeypatch.setattr(lifecycle, "_heartbeat_active_inbound_admission", heartbeat_guard)
    monkeypatch.setattr(lifecycle, "_enforce_inbound_deadline", deadline_guard)

    dedupe_key = ("asterisk", call_id)
    lifecycle._inbound_admissions_finalized.discard(dedupe_key)
    lifecycle._inbound_admissions_in_flight.pop(call_id, None)
    try:
        # Drive the real StasisStart -> adapter media -> lifecycle callback path.
        await adapter._handle_ari_event(_event(call_id))
        await asyncio.wait_for(handoff_window_reached.wait(), timeout=5)

        # This is the exact provisional window: all AI resources are live and
        # registered, while the adapter has not yet accepted lifecycle ownership.
        assert state.sessions[call_id] is voice_session
        assert len(state.gateway_sessions) == 1
        gateway_session_id = next(iter(state.gateway_sessions))
        assert gateway_session_id.startswith("asterisk-")
        assert state.gateway_sessions[gateway_session_id] == call_id
        assert orchestrator._active_sessions == {voice_session.call_id: voice_session}
        assert pipeline_started.is_set()
        assert voice_session.pipeline_task is not None
        assert not voice_session.pipeline_task.done()
        assert tts.connect_calls == 1
        assert stt.pre_connect_calls == 1
        assert call_id in lifecycle._inbound_heartbeat_tasks
        assert call_id in lifecycle._inbound_deadline_tasks
        assert call_id in adapter._active_sessions
        assert call_id in adapter._inbound_handoff_tasks
        assert call_id not in adapter._inbound_handoff_accepted

        # Asterisk commonly emits several terminal notifications for one leg.
        # The first must cancel provisional lifecycle; the second must be a no-op.
        for event_type in ("StasisEnd", "ChannelDestroyed"):
            await adapter._handle_ari_event({"type": event_type, "channel": {"id": call_id}})

        for _ in range(200):
            if (
                global_releases
                and not adapter._terminal_cleanup_tasks
                and not adapter._preanswer_hangup_tasks
                and not adapter._inbound_handoff_tasks
            ):
                break
            await asyncio.sleep(0)

        assert audio_start_cancelled.is_set()
        assert sorted(guard_cancellations) == ["deadline", "heartbeat"]
        assert call_id not in lifecycle._inbound_heartbeat_tasks
        assert call_id not in lifecycle._inbound_deadline_tasks
        assert call_id not in state.sessions
        assert state.gateway_sessions == {}
        assert orchestrator._active_sessions == {}
        assert end_session_calls == [voice_session]
        assert pipeline.cancelled == 1
        assert pipeline.cancel_active_turn_calls == 1
        assert media_gateway.started == 1
        assert media_gateway.ended == 1
        assert media_gateway.cleaned == 1
        assert [stt.cleanup_calls, llm.cleanup_calls, tts.cleanup_calls] == [1, 1, 1]

        assert len(durable_finalizations) == 1
        assert durable_finalizations[0].call_id == durable_call_id
        assert durable_finalizations[0].provider_call_id == call_id
        assert durable_finalizations[0].duration_seconds >= 1
        assert global_releases == [call_id]
        assert cleanup_actions.index("durable_finalize") < cleanup_actions.index("global_release")
        lifecycle_end.assert_not_awaited()
        adapter._release_rtp_port.assert_awaited_once_with(32000)
        assert call_id not in adapter._active_sessions
        assert call_id not in adapter._ext_channels
        assert call_id not in adapter._bridges
        assert call_id not in adapter._gateway_sessions
        assert call_id not in adapter._inbound_cleanup_pending
        assert adapter.get_inbound_admission(call_id) is None
    finally:
        # Cancelling the handoff can synchronously create the adapter-owned
        # cleanup task, so drain in passes rather than snapshotting only once.
        for _ in range(3):
            tasks = []
            for registry in (
                adapter._inbound_setup_tasks,
                adapter._inbound_handoff_tasks,
                adapter._preanswer_hangup_tasks,
                adapter._terminal_cleanup_tasks,
                lifecycle._inbound_heartbeat_tasks,
                lifecycle._inbound_deadline_tasks,
            ):
                task = registry.pop(call_id, None)
                if task is not None and not task.done():
                    task.cancel()
                    tasks.append(task)
            if voice_session.pipeline_task is not None and not voice_session.pipeline_task.done():
                voice_session.pipeline_task.cancel()
                tasks.append(voice_session.pipeline_task)
            if not tasks:
                break
            await asyncio.gather(*dict.fromkeys(tasks), return_exceptions=True)
            await asyncio.sleep(0)
        lifecycle._inbound_admissions_finalized.discard(dedupe_key)
        lifecycle._inbound_admissions_in_flight.pop(call_id, None)
