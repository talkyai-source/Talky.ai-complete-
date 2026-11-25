"""Realtime opening notice on a call that is ending (25 Nov 2026).

On the realtime path the opening notice's gate reset (`admit_opening`) emptied the
caller audio of a call whose hang-up was already requested (voicemail) or whose
teardown had begun: the connected call's recording was lost ("none:too_short").
The reset must be skipped there, the audio kept, and the notice settled as NOT
confirmed so nothing can later mark it delivered over audio from before it.

Real: TelephonyMediaGateway, RealtimeBridge, run_realtime_bridge, decide_opening,
admit_opening, the voicemail detector, the save and the spool writer. Fakes only at
the edges: the Asterisk adapter, the OpenAI socket, the DB container, the policy row.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_connected_call_recording_is_never_dropped_20261125 import (  # noqa: F401
    PBX, SID, TENANT, FakeAdapter, _caller_bytes, _decide_patch, _spooled, env,
)
from app.domain.services import recording_policy_service as policy
from app.domain.services.telephony import recording as tr
from app.domain.services.voice_orchestrator import Direction
from app.domain.services.voice_pipeline import voicemail_detector as vmd
from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway
from app.realtime import opening_notice as on
from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import RealtimeEvent

VOICEMAIL = "Hi, you have reached John. Please leave a message after the tone."


class RtAdapter(FakeAdapter):
    def __init__(self, release=None):
        super().__init__()
        self.release = release
        self.packets = 0

    async def begin_tts_utterance(self, pbx, uid):
        return None

    async def finish_tts_utterance(self, pbx, uid):
        return {"utterance_id": uid, "status": "transmitted", "evidence": "transmitted", "played_ms": 0}

    async def send_tts_audio(self, pbx, pcmu):
        await super().send_tts_audio(pbx, pcmu)
        self.packets += 1

    async def hangup_confirmed(self, pbx):
        self.hangups.append(pbx)
        if self.release is not None:
            await self.release.wait()
        self.gone = True
        return True


async def _call(adapter, seconds):
    gw = TelephonyMediaGateway()
    await gw.initialize({"sample_rate": 8000})
    await gw.on_call_started(SID, {"adapter": adapter, "pbx_call_id": PBX})
    for _ in range(int(seconds / 0.04)):
        await gw.on_audio_received(SID, b"\x55" * 320)
    return gw


def _build(gw, events_factory):
    agent_config = SimpleNamespace(agent_name="Sarah", company_name="Acme")
    cs = SimpleNamespace(call_id=SID, tenant_id=TENANT, agent_config=agent_config, _caller_opted_out=False,
                         captured_slots=None, contact_phone_region=None)
    config = SimpleNamespace(direction=Direction.OUTBOUND, agent_config=agent_config, callee_first_name="John",
                             tenant_id=TENANT, campaign_id=None, lead_id=None, realtime_opening_greeting=None)
    rt = SimpleNamespace(
        repair_unspoken_response=AsyncMock(), supports_response_metadata=False, request_response=AsyncMock(),
        truncate_response=AsyncMock(), close=AsyncMock(), closed=lambda: False, send_caller_audio=AsyncMock(),
        trigger_greeting=AsyncMock())
    bridge = RealtimeBridge(call_id=SID, realtime_session=rt, media_gateway=gw, internal_sample_rate=8000,
                            action_session=cs, contact_session=cs, greet_on_start=True)
    vs = SimpleNamespace(call_id=SID, call_session=cs, config=config, media_gateway=gw,
                         realtime_bridge=bridge, _dialer_tenant_id=TENANT)
    rt.events = lambda: events_factory(bridge)
    return vs, bridge


def _cand(bridge):
    return RealtimeEvent(kind="response_candidate", text=bridge._opening_gate.required_text,
                         audio=b"\xff" * 1600, raw={"response": {"id": "r1", "output": []}})


def _trace_gate(gw, trace):
    real = gw.set_recording_enabled

    def traced(cid, enabled):
        trace.append(("gate", enabled))
        return real(cid, enabled)
    gw.set_recording_enabled = traced


async def _voicemail_burst(bridge):
    yield _cand(bridge)
    yield RealtimeEvent(kind="caller_transcript", text=VOICEMAIL, is_final=True, raw=None)
    await asyncio.sleep(5)


async def _opening_only(bridge):
    yield _cand(bridge)
    await asyncio.sleep(0.3)


async def _run(vs, gw):
    trace = []
    _trace_gate(gw, trace)
    before = _caller_bytes(gw)
    await on.run_realtime_bridge(vs)
    after = _caller_bytes(gw)
    status = await tr._save_call_recording(vs, PBX)
    return trace, before, after, status


async def test_A_voicemail_after_proven_hangup_keeps_audio_and_is_not_confirmed(env, monkeypatch):
    _decide_patch(monkeypatch)
    adapter = RtAdapter()
    gw = await _call(adapter, 7.0)
    vs, bridge = _build(gw, _voicemail_burst)
    monkeypatch.setattr(vmd, "_locate_session", lambda call_id: (PBX, vs))
    trace, before, after, status = await _run(vs, gw)
    g = bridge._opening_gate
    assert trace == [] and after == before > 0
    assert status == "captured" and _spooled()
    assert (g.state, g.reason) == ("unconfirmed", "call_ending_hangup_requested")
    assert policy.get_disclosure_state(SID) == "failed"
    assert vs._recording_policy_decided is True
    assert adapter.packets == 0


async def test_A2_hangup_still_pending_waits_then_skips(env, monkeypatch):
    _decide_patch(monkeypatch)
    release = asyncio.Event()

    class Slow(RtAdapter):
        async def hangup_confirmed(self, pbx):
            asyncio.get_running_loop().call_later(0.05, release.set)
            return await super().hangup_confirmed(pbx)
    adapter = Slow(release=release)
    gw = await _call(adapter, 7.0)
    vs, bridge = _build(gw, _voicemail_burst)
    monkeypatch.setattr(vmd, "_locate_session", lambda call_id: (PBX, vs))
    trace, before, after, status = await _run(vs, gw)
    g = bridge._opening_gate
    assert trace == [] and after == before > 0 and status == "captured"
    assert g.state == "unconfirmed" and policy.get_disclosure_state(SID) == "failed"


async def test_B_teardown_begun_keeps_audio_and_is_not_confirmed(env, monkeypatch):
    _decide_patch(monkeypatch)
    adapter = RtAdapter()
    gw = await _call(adapter, 3.0)
    vs, bridge = _build(gw, _opening_only)
    vs._call_ending = True
    adapter.gone = True
    trace, before, after, status = await _run(vs, gw)
    g = bridge._opening_gate
    assert trace == [] and after == before > 0 and status == "captured"
    assert (g.state, g.reason) == ("unconfirmed", "call_ending_call_ended")
    assert policy.get_disclosure_state(SID) == "failed"


async def test_C_failed_hangup_still_gets_the_notice_and_the_reset(env, monkeypatch):
    _decide_patch(monkeypatch)

    class Refused(RtAdapter):
        async def hangup_confirmed(self, pbx):
            self.hangups.append(pbx)
            await asyncio.sleep(0.05)
            return False  # the person is still on the line
    adapter = Refused()
    gw = await _call(adapter, 3.0)

    async def opening_then_wait(bridge):
        yield _cand(bridge)
        await asyncio.sleep(1.0)
    vs, bridge = _build(gw, opening_then_wait)
    hup = asyncio.create_task(gw.hangup_call(SID, reason="agent_end_call"))
    await asyncio.sleep(0)
    assert gw.is_hangup_pending(SID) is True
    trace, before, after, status = await _run(vs, gw)
    assert await hup is False
    assert trace == [("gate", False), ("gate", True)]
    assert bridge._opening_gate.state == "delivered"
    assert policy.get_disclosure_state(SID) == "transmitted"


async def test_D_live_call_is_unchanged(env, monkeypatch):
    _decide_patch(monkeypatch)
    gw = await _call(RtAdapter(), 3.0)
    vs, bridge = _build(gw, _opening_only)
    trace, before, after, status = await _run(vs, gw)
    assert trace == [("gate", False), ("gate", True)]
    assert bridge._opening_gate.state == "delivered"
    assert policy.get_disclosure_state(SID) == "transmitted"
    assert after == 0 < before


@pytest.mark.parametrize("ending", [False, True])
async def test_E_disabled_account_stays_closed_and_skip_is_not_evaluated(env, monkeypatch, ending):
    _decide_patch(monkeypatch, should_record=False)
    gw = await _call(RtAdapter(), 3.0)
    vs, bridge = _build(gw, _opening_only)
    if ending:
        vs._call_ending = True
    probed = []
    from app.domain.services.telephony.modes import agent_first

    async def spy(*a, **k):
        probed.append(1)
        return "call_ended"
    monkeypatch.setattr(agent_first, "_notice_skip_reason", spy, raising=False)
    trace, before, after, status = await _run(vs, gw)
    assert probed == []
    assert all(t[1] is False for t in trace) and after == 0 and status == "refused"


async def test_F_after_a_skip_no_later_reply_can_mark_the_notice_delivered(env, monkeypatch):
    _decide_patch(monkeypatch)
    adapter = RtAdapter()
    gw = await _call(adapter, 3.0)

    async def twice(bridge):
        yield _cand(bridge)
        await asyncio.sleep(0.05)
        vs._call_ending = False  # even with the flag cleared, the gate stays settled
        ev = _cand(bridge)
        ev.raw = {"response": {"id": "r2", "output": []}}
        yield ev
        await asyncio.sleep(0.5)
    vs, bridge = _build(gw, twice)
    vs._call_ending = True
    trace, before, after, status = await _run(vs, gw)
    g = bridge._opening_gate
    assert adapter.packets > 0, "the second reply must actually have been transmitted"
    assert trace == [] and g.state == "unconfirmed"
    assert policy.get_disclosure_state(SID) == "failed"


async def test_G_notice_skip_reason_in_four_situations(env):
    from app.domain.services.telephony.modes.agent_first import _notice_skip_reason
    adapter = RtAdapter()

    async def fresh():
        gw = await _call(adapter, 0.04)
        return gw, SimpleNamespace(media_gateway=gw, _call_ending=False)

    gw, vs = await fresh()
    assert await _notice_skip_reason(vs, SID) is None

    vs._call_ending = True
    assert await _notice_skip_reason(vs, SID) == "call_ended"

    gw, vs = await fresh()
    assert await gw.hangup_call(SID, reason="agent_end_call") is True
    assert await _notice_skip_reason(vs, SID) == "hangup_requested"

    gw, vs = await fresh()
    gw.deactivate_session(SID)
    assert await _notice_skip_reason(vs, SID) == "session_inactive"


# ------------------------------------------ the wait covers the whole proof window --

async def test_the_notice_waits_for_the_phone_systems_whole_proof_window():
    # The phone system may take up to 5 s to prove a hang-up. A 2 s wait let the
    # notice start, and wipe the call, while a slow hang-up was on its way (review).
    import inspect

    from app.domain.services.telephony.modes import agent_first
    from app.infrastructure.telephony import asterisk_adapter

    assert agent_first._HANGUP_PROOF_WAIT_S > 5.0
    # The window is capped at 5.0 whatever the setting says: if this line of the
    # adapter changes, the wait above has to be looked at again.
    assert 'min(5.0, float(os.getenv("ASTERISK_HANGUP_CONFIRM_TIMEOUT_S", "5.0")))' in inspect.getsource(asterisk_adapter)

    asked = []

    class Gateway:
        def is_hangup_pending(self, call_id):
            return True

        async def wait_hangup_resolved(self, call_id, timeout):
            asked.append(timeout)

        def is_hangup_requested(self, call_id):
            return True

    vs = SimpleNamespace(media_gateway=Gateway(), _call_ending=False)
    assert await agent_first._notice_skip_reason(vs, SID) == "hangup_requested"
    assert asked == [agent_first._HANGUP_PROOF_WAIT_S]


async def test_the_wait_is_measured_by_the_clock():
    # Counting 10 ms sleeps made the same limit last about half as long again on
    # a system where such a sleep takes 15 ms; the limit is now by the clock.
    import time

    gw = TelephonyMediaGateway()
    await gw.initialize({"sample_rate": 16000})
    await gw.on_call_started(SID, {"adapter": SimpleNamespace(), "pbx_call_id": "talky-out-wait"})
    gw._sessions[SID].hangup_pending = True
    started = time.monotonic()
    await gw.wait_hangup_resolved(SID, 0.3)
    waited = time.monotonic() - started
    assert 0.29 <= waited < 0.45
    gw._sessions[SID].hangup_pending = False
    started = time.monotonic()
    await gw.wait_hangup_resolved(SID, 0.3)
    assert time.monotonic() - started < 0.05
