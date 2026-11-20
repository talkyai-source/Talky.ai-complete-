"""Incoming calls follow the same recording rule as outgoing calls (owner
decision 20 November 2026, extending the one of 18 November): no saved policy
is recorded with the spoken notice, a missing notice receipt keeps the audio
marked unconfirmed, a saved `disabled` policy is never recorded, and
RECORDING_REQUIRE_NOTICE_PROOF=true restores the strict rule.

Also pins: no notice on a call that is not recorded (F4), an unreadable live
switch never destroys audio (F5), crash protection for inbound (F6)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_policy_service as rps
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import recording as tr
from app.domain.services.telephony.modes import caller_first
from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure
from app.domain.services.telephony.modes.caller_first import prepare_inbound_recording
from tests.unit import test_recording_disclosure as base
from tests.unit.test_recording_checkpoint import FakeGw, chunk, env, folder, make_session  # noqa: F401
from tests.unit.test_recording_never_lost import (  # noqa: F401
    PBX, SESSION, TENANT, _entry, _write, h,
)

DISABLED = rps.RecordingDecision(False, False, None, None, 90, "tenant_policy_disabled")
_CF = "app.domain.services.telephony.modes.caller_first."


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("RECORDING_REQUIRE_NOTICE_PROOF", raising=False)
    rps.clear_disclosure_state(base.CALL_UUID)
    yield
    rps.clear_disclosure_state(base.CALL_UUID)


async def _gate(decision, *, raises=False, live=True, campaign=True, platform=True):
    """Run the inbound gate; returns (allowed, voice_session, gateway, log, consent)."""
    log = []
    vs, _ = base._make_voice_session(log, raises=raises)
    gateway = base._GatedInboundMediaGateway(log)
    vs.media_gateway = gateway
    snap = vs._inbound_admission["config_snapshot"]
    snap["inbound_config"]["recording_enabled"] = campaign
    snap["controls"]["recording_enabled"] = platform
    consent = AsyncMock(return_value=None)
    p_container, p_policy = base._patched_policy(decision)
    with p_container, p_policy, patch(
        _CF + "_live_inbound_recording_state", new=AsyncMock(return_value=live)
    ), patch(_CF + "_set_inbound_consent_status", new=consent):
        allowed = await prepare_inbound_recording(vs)
    return allowed, vs, gateway, log, consent


def _last_status(consent):
    return consent.await_args_list[-1].args[2]


def _spoke(log):
    return any(kind == "tts" for kind, _ in log)


# ------------------------------------------------------------ the gate (1)

@pytest.mark.asyncio
async def test_no_saved_policy_is_recorded_with_notice_and_receipt():
    allowed, vs, gateway, log, consent = await _gate(rps._ABSENT_POLICY_DEFAULT)
    assert allowed is True and gateway.enabled is True and vs._recording_allowed is True
    assert _spoke(log)
    assert _last_status(consent) == "granted"


@pytest.mark.asyncio
async def test_missing_receipt_keeps_gate_open_with_consent_unknown():
    allowed, vs, gateway, log, consent = await _gate(base._ANNOUNCE, raises=True)
    assert allowed is True and gateway.enabled is True
    assert _last_status(consent) == "unknown"


@pytest.mark.asyncio
async def test_strict_setting_missing_receipt_closes_gate_declined(monkeypatch):
    monkeypatch.setenv("RECORDING_REQUIRE_NOTICE_PROOF", "true")
    allowed, vs, gateway, log, consent = await _gate(base._ANNOUNCE, raises=True)
    assert allowed is False and gateway.enabled is False
    assert _last_status(consent) == "declined"


@pytest.mark.asyncio
async def test_strict_setting_refuses_a_tenant_without_saved_policy(monkeypatch):
    monkeypatch.setenv("RECORDING_REQUIRE_NOTICE_PROOF", "true")
    allowed, vs, gateway, log, consent = await _gate(rps._ABSENT_POLICY_DEFAULT)
    assert allowed is False and gateway.enabled is False
    assert not _spoke(log)
    assert _last_status(consent) == "not_required"


@pytest.mark.asyncio
async def test_saved_disabled_policy_is_not_recorded_and_no_notice():
    allowed, vs, gateway, log, consent = await _gate(DISABLED)
    assert allowed is False and gateway.enabled is False
    assert not _spoke(log)
    assert _last_status(consent) == "not_required"


# ------------------------------------------------------- no notice (2, F4)

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        {"campaign": False},
        {"platform": False},
        {"live": False},
        {"decision": DISABLED},
    ],
    ids=["campaign_off", "platform_off", "live_off", "policy_refuses"],
)
async def test_no_notice_flag_set_when_call_is_not_recorded(kw):
    decision = kw.pop("decision", base._ANNOUNCE)
    allowed, vs, gateway, log, consent = await _gate(decision, **kw)
    assert allowed is False
    assert vs._recording_disclosure_attempted is True
    p_container, p_policy = base._patched_policy(decision)
    with p_container, p_policy:
        await _speak_recording_disclosure(vs)
    assert not _spoke(log), "a call that is not recorded never gets the notice later"


@pytest.mark.asyncio
async def test_a_gate_that_fails_closed_never_lets_the_notice_be_spoken_later():
    """An error before the notice leaves the call unrecorded; no later step may say it is recorded."""
    log = []
    vs, _ = base._make_voice_session(log)
    vs.media_gateway = base._GatedInboundMediaGateway(log)
    p_container, _unused = base._patched_policy(base._ANNOUNCE)
    with p_container, patch.object(
        rps.RecordingPolicyService, "decide", new=AsyncMock(side_effect=RuntimeError("policy read failed"))
    ), patch(_CF + "_live_inbound_recording_state", new=AsyncMock(return_value=True)), patch(
        _CF + "_set_inbound_consent_status", new=AsyncMock(return_value=None)
    ):
        assert await prepare_inbound_recording(vs) is False
    assert vs._recording_allowed is False
    assert vs._recording_disclosure_attempted is True
    p_container, p_policy = base._patched_policy(base._ANNOUNCE)
    with p_container, p_policy:
        await _speak_recording_disclosure(vs)
    assert not _spoke(log)


@pytest.mark.asyncio
async def test_no_database_connection_at_save_time_is_cannot_tell_not_off(h):  # noqa: F811
    vs = h.voice(_inbound_admission={"x": 1}, _recording_allowed=True)
    assert await tr._inbound_recording_persist_allowed(vs, None) is None
    closed = h.voice(_inbound_admission={"x": 1}, _recording_allowed=False)
    assert await tr._inbound_recording_persist_allowed(closed, None) is False


# -------------------------------------------- unreadable live switch (3, F5)

class _BrokenPool:
    def acquire(self):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_state_is_none_when_the_switch_cannot_be_read():
    assert await caller_first._live_inbound_recording_state(_BrokenPool()) is None
    assert await caller_first._live_inbound_recording_enabled(_BrokenPool()) is False


@pytest.mark.asyncio
async def test_setup_records_when_the_switch_read_fails():
    log = []
    vs, _ = base._make_voice_session(log)
    gateway = base._GatedInboundMediaGateway(log)
    vs.media_gateway = gateway
    container = SimpleNamespace(is_initialized=True, db_pool=_BrokenPool())
    with patch("app.core.container.get_container", return_value=container), patch.object(
        rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=base._ANNOUNCE)
    ), patch(_CF + "_set_inbound_consent_status", new=AsyncMock(return_value=None)):
        assert await prepare_inbound_recording(vs) is True
    assert gateway.enabled is True


async def _run_heartbeat(monkeypatch, state):
    disable = Mock(return_value=1)
    monkeypatch.setattr(lifecycle, "disable_live_inbound_recordings", disable)
    monkeypatch.setattr(lifecycle, "_INBOUND_HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(db_pool=object(), redis=None),
    )
    monkeypatch.setattr(
        "app.domain.services.telephony.inbound_admission.heartbeat_inbound_call",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("app.domain.services.global_concurrency.refresh_lease", AsyncMock())
    monkeypatch.setattr(_CF + "_live_inbound_recording_state", AsyncMock(return_value=state))
    task = asyncio.create_task(
        lifecycle._heartbeat_active_inbound_admission("pbx-hb", {"call_id": "durable-1"})
    )
    await asyncio.sleep(0.08)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return disable


@pytest.mark.asyncio
async def test_heartbeat_does_not_clear_buffers_when_the_switch_is_unreadable(monkeypatch):
    disable = await _run_heartbeat(monkeypatch, None)
    disable.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_clears_buffers_when_the_switch_is_definitely_off(monkeypatch):
    disable = await _run_heartbeat(monkeypatch, False)
    disable.assert_called_with("pbx-hb")


@pytest.mark.asyncio
async def test_save_with_unreadable_switch_retries_and_keeps_the_audio(h):  # noqa: F811
    entry = _write(_entry(inbound=True))
    with patch(_CF + "_live_inbound_recording_state", new=AsyncMock(return_value=None)):
        outcome = await tr.process_spool_entry(entry)
    assert outcome == "retry"
    [kept_entry] = spool.list_entries()
    assert kept_entry.last_error == "inbound_switch_unreadable"
    assert spool.has_audio(kept_entry.key)
    assert h.saves == []


@pytest.mark.asyncio
async def test_save_with_definitely_off_switch_still_discards(h):  # noqa: F811
    entry = _write(_entry(inbound=True))
    with patch(_CF + "_live_inbound_recording_state", new=AsyncMock(return_value=False)):
        assert await tr.process_spool_entry(entry) == "refused"
    assert spool.list_entries() == []


@pytest.mark.asyncio
async def test_first_save_check_continues_to_pending_when_switch_unreadable(h):  # noqa: F811
    vs = h.voice(_inbound_admission={"x": 1}, _recording_allowed=True)
    with patch(_CF + "_live_inbound_recording_state", new=AsyncMock(return_value=None)):
        await tr._save_call_recording(vs, PBX)
    [entry] = spool.list_entries()
    assert entry.inbound is True
    assert spool.has_audio(entry.key)
    assert vs._recording_allowed is True


# ------------------------------------------------------ crash protection (4)

@pytest.mark.asyncio
async def test_inbound_session_with_open_gate_is_checkpointed(env):  # noqa: F811
    gw = FakeGw()
    gw.caller += [chunk(100, 1)]
    env.live["pbx1"] = make_session(
        "sess-in", gw, _inbound_admission={"x": 1},
        _recording_allowed=True, _recording_policy_decided=False,
    )
    assert await cp.checkpoint_pass() == 1
    assert (folder(env, "sess-in") / "meta.json").exists()


@pytest.mark.asyncio
async def test_inbound_session_with_closed_gate_is_not_checkpointed(env):  # noqa: F811
    gw = FakeGw()
    gw.caller += [chunk(100, 1)]
    env.live["pbx1"] = make_session(
        "sess-in", gw, _inbound_admission={"x": 1}, _recording_allowed=False,
    )
    assert await cp.checkpoint_pass() == 0
    assert not (env.spool / "live").exists()


@pytest.mark.asyncio
async def test_outbound_rule_is_unchanged(env):  # noqa: F811
    gw = FakeGw()
    gw.caller += [chunk(100, 1)]
    env.live["pbx-undecided"] = make_session("out-1", gw, _recording_policy_decided=False)
    assert await cp.checkpoint_pass() == 0
    gw2 = FakeGw()
    gw2.caller += [chunk(100, 1)]
    env.live["pbx-decided"] = make_session("out-2", gw2)
    assert await cp.checkpoint_pass() == 1


@pytest.mark.asyncio
async def test_recovered_inbound_orphan_keeps_inbound_flag_and_tenant(env):  # noqa: F811
    gw = FakeGw()
    gw.caller += [chunk(20000, 1)]
    env.live["pbx1"] = make_session(
        "sess-in", gw, _inbound_admission={"x": 1}, _recording_allowed=True,
    )
    await cp.checkpoint_pass()
    env.live.clear()
    cp._STATE.clear()
    assert await cp.recover_orphans() == 1
    [entry] = spool.list_entries()
    assert entry.inbound is True and entry.tenant_id == TENANT


# ------------------------------------------------------- the saved entry (5)

def test_inbound_spool_entry_strict_only_under_the_strict_setting(h, monkeypatch):  # noqa: F811
    vs = h.voice(_inbound_admission={"x": 1})
    kw = dict(sample_rate=16000, duration=1.0, awaiting_transfer=False,
              transfer_recording_name=None)
    entry = tr._build_spool_entry(vs, PBX, **kw)
    assert entry.inbound is True and entry.strict_notice is False
    monkeypatch.setenv("RECORDING_REQUIRE_NOTICE_PROOF", "true")
    assert tr._build_spool_entry(vs, PBX, **kw).strict_notice is True
    out = tr._build_spool_entry(h.voice(), PBX, **kw)
    assert out.inbound is False and out.strict_notice is False
