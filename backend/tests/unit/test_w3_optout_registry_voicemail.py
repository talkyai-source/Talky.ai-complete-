"""Packet W3: in-call opt-out and voicemail hang-up must find the LIVE session.

The telephony registry (``LocalOnlyStateBackend`` -> ``telephony_bridge.
_telephony_sessions``) is keyed by the PBX channel id (lifecycle ``set_voice_
session(call_id, ...)``), while the pipeline / realtime bridge / ``CallSession``
carry the pipeline UUID (``voice_orchestrator.create_voice_session``). Looking a
UUID up in that registry returns None on every Asterisk call. The older tests
stub ``_state()`` with a one-key lambda that answers for ANY id, which hid it.

These tests use the REAL ``LocalOnlyStateBackend`` keyed by a PBX id.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.dialer import opt_out
from app.domain.services.voice_pipeline import voicemail_detector

PBX_ID = "1790168826.2670"
CALL_UUID = "5c2f0a8e-6a39-4c1d-9d6e-0f0f0f0f0f01"
OTHER_PBX_ID = "1790168900.2671"
OTHER_UUID = "9d1e7b44-2b7c-4e0a-8a61-0a0a0a0a0a02"
TENANT = "20000000-0000-4000-8000-000000000031"
OTHER_TENANT = "20000000-0000-4000-8000-000000000032"
LEAD = "30000000-0000-4000-8000-000000000031"
DIALER_CALL = "10000000-0000-4000-8000-000000000031"
PHONE = "+15555550131"
GREETING = "Please leave a message after the tone."


class _Conn:
    """Connection double for the tenant-scoped job/lead cleanup writes."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return self

    async def execute(self, sql, *args):
        return "UPDATE 0" if "UPDATE dialer_jobs" in sql else "SET"

    async def fetchrow(self, sql, *args):
        return {"id": LEAD}


@pytest.fixture
def registry(monkeypatch):
    """The real LocalOnlyStateBackend over a fresh, empty session dict."""
    from app.api.v1.endpoints import telephony_bridge
    from app.domain.services.telephony import lifecycle
    from app.domain.services.telephony.state_backend import LocalOnlyStateBackend

    monkeypatch.setattr(telephony_bridge, "_telephony_sessions", {})
    backend = LocalOnlyStateBackend()
    monkeypatch.setattr(lifecycle, "_state", lambda: backend)
    return backend


@pytest.fixture
def dnc(monkeypatch):
    """Synthetic DB ports; records the order of DNC writes."""
    pool = SimpleNamespace(acquire=lambda: _Conn())
    write = AsyncMock(return_value=SimpleNamespace(id="synthetic-dnc"))
    monkeypatch.setattr(opt_out.DNCService, "add_caller_opt_out", write)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=pool, db_client=object()),
    )
    return write


def _live_session(*, call_id=CALL_UUID, tenant=TENANT, direction="outbound",
                  gateway=None, with_call_id=True):
    """A VoiceSession-shaped double: pipeline UUID inside, PBX id outside."""
    call_session = SimpleNamespace(call_id=call_id, _call_direction=direction)
    fields = dict(
        call_session=call_session,
        media_gateway=gateway,
        _dialer_tenant_id=tenant,
        _dialer_lead_id=LEAD,
        _dialer_phone=PHONE,
        _dialer_call_id=DIALER_CALL,
    )
    if with_call_id:
        fields["call_id"] = call_id
    return SimpleNamespace(**fields), call_session


# --- Item 1: in-call opt-out resolves the live session -----------------------

def test_the_real_registry_cannot_be_looked_up_by_the_pipeline_uuid(registry):
    """Documents the root cause with the real backend (not a one-key stub)."""
    vs, _ = _live_session()
    registry.set_voice_session(PBX_ID, vs)
    assert registry.get_voice_session(CALL_UUID) is None
    assert registry.get_voice_session(PBX_ID) is vs


@pytest.mark.asyncio
async def test_opt_out_finds_session_keyed_by_pbx_id_and_writes_dnc(registry, dnc):
    vs, call_session = _live_session()
    registry.set_voice_session(PBX_ID, vs)

    assert await opt_out.purge_opt_out_before_farewell(call_session) is True

    dnc.assert_awaited_once()
    assert dnc.await_args.kwargs["tenant_id"] == TENANT
    assert dnc.await_args.kwargs["e164"] == PHONE
    assert vs._opt_out_dnc_written is True
    assert vs._opt_out_purged is True
    assert call_session._opt_out_recorded is True


@pytest.mark.asyncio
async def test_dnc_is_written_before_the_farewell_when_registry_is_pbx_keyed(
    registry, dnc
):
    """The farewell path must find the session, write, THEN speak the claim."""
    from tests.unit.test_voice_pipeline_service import _make_session
    from app.domain.services.voice_pipeline_service import VoicePipelineService

    order: list[str] = []

    async def write_spy(**kwargs):
        order.append("dnc_write")
        return SimpleNamespace(id="synthetic-dnc")

    dnc.side_effect = write_spy

    session = _make_session()
    session.call_id = CALL_UUID
    session._caller_opted_out = True
    vs, _ = _live_session()
    vs.call_session = session
    registry.set_voice_session(PBX_ID, vs)

    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(), mute_during_tts=False,
    )

    async def speak_spy(sess, text, websocket, **kw):
        order.append(f"speak:{text}")
        return False

    service.synthesize_and_send_audio = speak_spy

    farewell = "You have been removed from our list. Goodbye."
    await service._shutdown_session_for_end_action(
        session, None, "caller_opt_out", farewell,
    )

    assert order[0] == "dnc_write", order
    assert order[1] == f"speak:{farewell}", order  # the claim is now true


@pytest.mark.asyncio
async def test_opt_out_finds_session_by_call_session_identity(registry, dnc):
    """A registry entry without a matching call_id is still found by identity
    (the lead_slot_capture pattern)."""
    vs, call_session = _live_session(with_call_id=False)
    registry.set_voice_session(PBX_ID, vs)

    assert await opt_out.purge_opt_out_before_farewell(call_session) is True
    dnc.assert_awaited_once()


@pytest.mark.asyncio
async def test_opt_out_falls_back_to_the_session_back_reference(registry, dnc):
    vs, call_session = _live_session(with_call_id=False)
    call_session._voice_session_ref = vs  # set by create_voice_session
    # NOT registered: nothing in the registry at all.

    assert await opt_out.purge_opt_out_before_farewell(call_session) is True
    dnc.assert_awaited_once()
    assert dnc.await_args.kwargs["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_opt_out_never_writes_for_a_different_calls_session(registry, dnc):
    """Two live calls: the opt-out lands on the caller's own tenant only."""
    other, _ = _live_session(call_id=OTHER_UUID, tenant=OTHER_TENANT)
    mine, my_call_session = _live_session()
    registry.set_voice_session(OTHER_PBX_ID, other)
    registry.set_voice_session(PBX_ID, mine)

    assert await opt_out.purge_opt_out_before_farewell(my_call_session) is True

    dnc.assert_awaited_once()
    assert dnc.await_args.kwargs["tenant_id"] == TENANT
    assert not getattr(other, "_opt_out_dnc_written", False)


@pytest.mark.asyncio
async def test_opt_out_without_any_live_session_is_not_acknowledged(registry, dnc):
    session = SimpleNamespace(call_id=CALL_UUID)
    assert await opt_out.purge_opt_out_before_farewell(session) is False
    dnc.assert_not_awaited()


def test_locate_live_voice_session_returns_the_pbx_channel_key(registry):
    vs, call_session = _live_session()
    registry.set_voice_session(PBX_ID, vs)
    assert opt_out.locate_live_voice_session(CALL_UUID) == (PBX_ID, vs)
    assert opt_out.locate_live_voice_session("", call_session) == (PBX_ID, vs)
    assert opt_out.resolve_live_voice_session(PBX_ID) is vs
    assert opt_out.locate_live_voice_session("no-such-call") == (None, None)


# --- Item 2: teardown opt-out write is bounded-retried -----------------------

OK = {"dnc_added": True, "purge_complete": True, "jobs_cancelled": 0, "lead_marked": True}
FAILED = {"dnc_added": False, "purge_complete": False, "jobs_cancelled": 0, "lead_marked": False}


def _retry_kwargs(**over):
    base = dict(
        db_pool=object(), db_client=object(), tenant_id=TENANT, lead_id=LEAD,
        phone_number=PHONE, call_id=DIALER_CALL,
        attempts=3, timeout_s=0.05, backoff_s=(0.0, 0.0),
    )
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_teardown_purge_retries_after_a_busy_pool_timeout(monkeypatch):
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(5)  # pool saturated: exceeds the timeout
        if calls["n"] == 2:
            return dict(FAILED)  # DNC add swallowed an error
        return dict(OK)

    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", flaky)
    vs = SimpleNamespace()

    result = await opt_out.purge_lead_on_opt_out_with_retry(
        voice_session=vs, **_retry_kwargs())

    assert calls["n"] == 3
    assert result["dnc_added"] is True
    assert vs._opt_out_dnc_written is True and vs._opt_out_purged is True


@pytest.mark.asyncio
async def test_teardown_purge_does_not_retry_once_confirmed(monkeypatch):
    purge = AsyncMock(return_value=dict(OK))
    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", purge)

    result = await opt_out.purge_lead_on_opt_out_with_retry(**_retry_kwargs())

    assert purge.await_count == 1
    assert result["purge_complete"] is True


@pytest.mark.asyncio
async def test_teardown_purge_stops_at_an_acknowledged_dnc_even_if_cleanup_is_partial(
    monkeypatch, caplog
):
    """The DNC row is the promise; partial lead/job cleanup is reported, not
    re-run (and is not an unconfirmed-DNC ERROR)."""
    partial = {**OK, "purge_complete": False, "lead_marked": False}
    purge = AsyncMock(return_value=partial)
    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", purge)

    with caplog.at_level(logging.WARNING, logger=opt_out.logger.name):
        result = await opt_out.purge_lead_on_opt_out_with_retry(**_retry_kwargs())

    assert purge.await_count == 1
    assert result["dnc_added"] is True and result["purge_complete"] is False
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_unconfirmed_opt_out_logs_error_with_masked_phone(monkeypatch, caplog):
    purge = AsyncMock(return_value=dict(FAILED))
    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", purge)
    vs = SimpleNamespace()

    with caplog.at_level(logging.WARNING, logger=opt_out.logger.name):
        result = await opt_out.purge_lead_on_opt_out_with_retry(
            voice_session=vs, call_ref="1790168826.26", **_retry_kwargs())

    assert purge.await_count == 3  # bounded: never more
    assert result["dnc_added"] is False
    assert not getattr(vs, "_opt_out_dnc_written", False)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    # The app-wide log filter (log_redact) rewrites long digit runs in the
    # record itself (these all-digit test UUIDs lose their tails), so match
    # on the leading group that survives.
    args = errors[0].args
    assert "1790168826.26" in args
    assert any(str(a).startswith(TENANT[:8]) for a in args)
    assert any(str(a).startswith(DIALER_CALL[:8]) for a in args)
    assert PHONE not in args  # never passed raw
    text = errors[0].getMessage()
    assert "5550131" not in text  # masked
    assert "***0131" in text  # last four digits still identify the number


@pytest.mark.asyncio
async def test_teardown_retry_propagates_cancellation(monkeypatch):
    started = asyncio.Event()

    async def hang(**kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", hang)
    task = asyncio.create_task(opt_out.purge_lead_on_opt_out_with_retry(
        **_retry_kwargs(timeout_s=30)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_teardown_retry_skips_retries_when_nothing_can_be_written(monkeypatch):
    """No tenant/phone: another attempt cannot help, so do not stall teardown."""
    purge = AsyncMock(return_value=dict(FAILED))
    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", purge)

    await opt_out.purge_lead_on_opt_out_with_retry(
        **_retry_kwargs(tenant_id=None))

    assert purge.await_count == 1


def test_lifecycle_teardown_uses_the_bounded_retry_not_a_single_shot():
    """Guard (source text, not behaviour): the teardown block must go through
    the retry helper. Behaviour is covered by the helper tests above."""
    import inspect
    from app.domain.services.telephony import lifecycle

    src = inspect.getsource(lifecycle._on_call_ended)
    assert "purge_lead_on_opt_out_with_retry" in src
    assert "asyncio.wait_for(purge_lead_on_opt_out(" not in src.replace("\n", "")


# --- Item 3: realtime voicemail hang-up uses the live session ----------------

def _adapter(monkeypatch, adapter):
    import app.domain.services.telephony.adapter_registry as adapter_registry

    monkeypatch.setattr(adapter_registry, "get_adapter", lambda: adapter)
    return adapter


@pytest.mark.asyncio
async def test_voicemail_hangup_goes_through_the_media_gateway_with_the_uuid(
    registry, monkeypatch
):
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=True))
    adapter = _adapter(monkeypatch, SimpleNamespace(hangup=AsyncMock()))
    vs, call_session = _live_session(gateway=gateway)
    registry.set_voice_session(PBX_ID, vs)

    assert await voicemail_detector.detect_and_hang_up_voicemail(
        CALL_UUID, GREETING, 0) is True

    gateway.hangup_call.assert_awaited_once_with(CALL_UUID, reason="voicemail_detected")
    adapter.hangup.assert_not_awaited()  # never DELETE /channels/<uuid>
    assert vs._amd_voicemail is True
    assert call_session._amd_voicemail is True


@pytest.mark.asyncio
async def test_unconfirmed_gateway_hangup_logs_error_and_tries_the_pbx_channel(
    registry, monkeypatch, caplog
):
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=False))
    adapter = _adapter(monkeypatch, SimpleNamespace(hangup=AsyncMock()))
    vs, _ = _live_session(gateway=gateway)
    registry.set_voice_session(PBX_ID, vs)

    with caplog.at_level(logging.WARNING, logger=voicemail_detector.logger.name):
        assert await voicemail_detector.detect_and_hang_up_voicemail(
            CALL_UUID, GREETING, 0) is True

    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    adapter.hangup.assert_awaited_once_with(PBX_ID)  # the channel id, not the UUID
    assert vs._amd_voicemail is True


@pytest.mark.asyncio
async def test_hangup_failure_on_every_path_is_surfaced_as_error(
    registry, monkeypatch, caplog
):
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=False))
    _adapter(monkeypatch, None)
    vs, _ = _live_session(gateway=gateway)
    registry.set_voice_session(PBX_ID, vs)

    with caplog.at_level(logging.WARNING, logger=voicemail_detector.logger.name):
        assert await voicemail_detector.detect_and_hang_up_voicemail(
            CALL_UUID, GREETING, 0) is True

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("NOT actually hung up" in m for m in messages), messages


@pytest.mark.asyncio
async def test_true_inbound_call_is_resolved_from_the_session_not_the_uuid_key(
    registry, monkeypatch
):
    """Before: the UUID lookup missed, the call read as outbound and a real
    inbound caller saying 'leave a message' was hung up on."""
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=True))
    adapter = _adapter(monkeypatch, SimpleNamespace(hangup=AsyncMock()))
    vs, call_session = _live_session(direction="inbound", gateway=gateway)
    registry.set_voice_session(PBX_ID, vs)

    assert await voicemail_detector.detect_and_hang_up_voicemail(
        CALL_UUID, GREETING, 0) is False

    gateway.hangup_call.assert_not_awaited()
    adapter.hangup.assert_not_awaited()
    assert not hasattr(vs, "_amd_voicemail")
    assert not hasattr(call_session, "_amd_voicemail")


@pytest.mark.asyncio
async def test_flag_session_voicemail_reaches_the_pbx_keyed_session(registry):
    vs, call_session = _live_session()
    registry.set_voice_session(PBX_ID, vs)

    voicemail_detector._flag_session_voicemail(CALL_UUID)

    assert vs._amd_voicemail is True
    assert call_session._amd_voicemail is True


@pytest.mark.asyncio
async def test_teardown_block_retries_a_lost_opt_out_write(monkeypatch):
    """Drive the real ``_on_call_ended`` teardown: the first DNC write is lost
    (busy pool), the retry lands, and the session records the acknowledgement.
    Unrelated media/recording/billing ports are stubbed (as in the integration
    test); the opt-out block itself is the production code."""
    from app.domain.services.telephony import lifecycle

    attempts = {"n": 0}

    async def lossy_purge(**kwargs):
        attempts["n"] += 1
        return dict(FAILED) if attempts["n"] == 1 else dict(OK)

    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", lossy_purge)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object(), db_client=object()),
    )

    voice = SimpleNamespace(
        _caller_opted_out=True, _dialer_tenant_id=TENANT, _dialer_lead_id=LEAD,
        _dialer_phone=PHONE, _dialer_call_id=DIALER_CALL,
        pipeline=SimpleNamespace(cancel_active_turn=AsyncMock()),
    )
    ended = AsyncMock()
    state = SimpleNamespace(
        clear_first_speaker=lambda _: None, clear_ringing_started_at=lambda _: None,
        pop_ringing_warmup=lambda _: None, pop_voice_session=lambda _: voice,
        remove_gateway_sessions_for_call=lambda _: None,
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "_save_call_recording", AsyncMock())
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: SimpleNamespace(end_session=ended))
    monkeypatch.setattr("app.domain.services.global_concurrency.release_lease", AsyncMock())
    monkeypatch.setattr("app.domain.services.call_status.record_call_state_by_provider_id", AsyncMock())

    pbx_call = "1790168826.2670-w3-teardown"
    try:
        await lifecycle._on_call_ended(pbx_call, terminal_at_monotonic=100, acknowledge_ledger=False)
        assert attempts["n"] == 2  # lost once, retried once, then stopped
        assert voice._opt_out_dnc_written is True and voice._opt_out_purged is True
        ended.assert_awaited_once_with(voice)
    finally:
        lifecycle._clear_ended_marker(pbx_call, lifecycle._ended_call_marker_tokens.get(pbx_call))


@pytest.mark.asyncio
async def test_gateway_hangup_raising_falls_back_to_the_confirmed_pbx_hangup(
    registry, monkeypatch
):
    gateway = SimpleNamespace(hangup_call=AsyncMock(side_effect=RuntimeError("boom")))
    adapter = _adapter(monkeypatch, SimpleNamespace(hangup_confirmed=AsyncMock(return_value=True)))
    vs, _ = _live_session(gateway=gateway)
    registry.set_voice_session(PBX_ID, vs)

    assert await voicemail_detector.detect_and_hang_up_voicemail(
        CALL_UUID, GREETING, 0) is True

    adapter.hangup_confirmed.assert_awaited_once_with(PBX_ID)


@pytest.mark.asyncio
async def test_unproven_pbx_hangup_is_not_reported_as_success(
    registry, monkeypatch, caplog
):
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=False))
    _adapter(monkeypatch, SimpleNamespace(hangup_confirmed=AsyncMock(return_value=False)))
    vs, _ = _live_session(gateway=gateway)
    registry.set_voice_session(PBX_ID, vs)

    with caplog.at_level(logging.WARNING, logger=voicemail_detector.logger.name):
        assert await voicemail_detector.detect_and_hang_up_voicemail(
            CALL_UUID, GREETING, 0) is True

    assert any("NOT actually hung up" in r.getMessage()
               for r in caplog.records if r.levelno >= logging.ERROR)


@pytest.mark.asyncio
async def test_unresolved_session_never_hangs_up_the_pipeline_uuid_on_the_bridge(
    registry, monkeypatch, caplog
):
    """No live session: the UUID is not a PBX channel, so DELETE
    /channels/<uuid> (a 404 that reads as 'gone') must not be sent or counted."""
    adapter = _adapter(monkeypatch, SimpleNamespace(
        hangup=AsyncMock(), hangup_confirmed=AsyncMock(return_value=True)))

    with caplog.at_level(logging.WARNING, logger=voicemail_detector.logger.name):
        assert await voicemail_detector.detect_and_hang_up_voicemail(
            CALL_UUID, GREETING, 0) is True

    adapter.hangup.assert_not_awaited()
    adapter.hangup_confirmed.assert_not_awaited()
    assert any("NOT actually hung up" in r.getMessage()
               for r in caplog.records if r.levelno >= logging.ERROR)


@pytest.mark.asyncio
async def test_earlier_in_call_dnc_proof_is_not_reported_as_unconfirmed(monkeypatch, caplog):
    # The in-call purge already acknowledged the DNC row; only cleanup was
    # partial. Teardown attempts that time out must not raise the
    # "DNC row was NOT confirmed written" ERROR ops is meant to act on.
    async def slow(**_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out", slow)
    vs = SimpleNamespace(_opt_out_dnc_written=True)

    with caplog.at_level(logging.WARNING, logger=opt_out.logger.name):
        await opt_out.purge_lead_on_opt_out_with_retry(voice_session=vs, **_retry_kwargs())

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert vs._opt_out_dnc_written is True
