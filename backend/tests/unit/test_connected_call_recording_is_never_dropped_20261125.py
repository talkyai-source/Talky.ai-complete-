"""A call that connected must end with its recording saved, or kept on disk.

Two outgoing calls of 24 Nov 2026 lost their audio (production evidence in
tmp/handoff-20261124/inspect_lost_outbound_recordings). Real TelephonyMediaGateway,
real VoicePipelineService._shutdown_session_for_end_action, real
_speak_recording_disclosure, real save and real spool writer. Fakes only at the
edges: the Asterisk adapter, the DB container, the policy row, the TTS call and
process_spool_entry.
"""
import asyncio
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_policy_service as policy
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import recording as tr
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.telephony.browser_media_gateway import SessionGoneError
from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway

SID = "dc858e25-9de0-4000-8000-000000000001"
PBX = "talky-out-5b-test"
TENANT = "20000000-0000-4000-8000-000000000004"


class FakeAdapter:
    def __init__(self):
        self.gone = False
        self.hangups = []

    async def send_tts_audio(self, pbx, pcmu):
        if self.gone:
            from app.infrastructure.telephony.asterisk_adapter import TtsSessionGoneError
            raise TtsSessionGoneError("no gateway session")

    async def hangup_confirmed(self, pbx):
        self.hangups.append(pbx)
        self.gone = True
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    container = SimpleNamespace(is_initialized=True, db_pool=object(), db_client=None)
    monkeypatch.setattr("app.core.container.get_container", lambda: container)

    async def fake_process(entry, *, wav_bytes=None, voice_session=None):
        return "retry"  # keep the spool entry on disk so the test can see it

    monkeypatch.setattr(tr, "process_spool_entry", fake_process)
    tr._PROCESSING.clear()
    cp._DONE.clear()
    cp._STATE.clear()
    policy.clear_disclosure_state(SID, PBX)
    yield SimpleNamespace(tmp=tmp_path)
    policy.clear_disclosure_state(SID, PBX)
    cp._DONE.clear()
    cp._STATE.clear()


async def _live_call(seconds=3.0, agent=True):
    adapter = FakeAdapter()
    gw = TelephonyMediaGateway()
    await gw.initialize({"sample_rate": 16000})
    await gw.on_call_started(SID, {"adapter": adapter, "pbx_call_id": PBX})
    for _ in range(int(seconds / 0.04)):
        await gw.on_audio_received(SID, b"\x55" * 320)
    if agent:
        await gw.send_audio(SID, b"\x10\x00" * 1600)
    return adapter, gw


def _voice_session(gw, **extra):
    cs = SimpleNamespace(
        call_id=SID, campaign_id="camp-1", current_user_input="", llm_active=True,
        tts_active=True, state=None, _caller_opted_out=False,
        conversation_history=[], turn_id=0, talklee_call_id=None,
    )
    return SimpleNamespace(
        call_id=SID, media_gateway=gw, _dialer_tenant_id=TENANT, _dialer_phone=None,
        config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
        call_session=cs, **extra,
    )


def _spooled():
    return spool.has_audio(SID)


def _live_dir():
    return os.path.join(spool.spool_dir(), "live", SID)


def _caller_bytes(gw):
    return sum(len(c) for c in (gw.get_recording_buffer(SID) or []))


def _pipeline(gw):
    return SimpleNamespace(
        media_gateway=gw, _is_ask_ai_session=VoicePipelineService._is_ask_ai_session,
    )


async def _end_action(gw, vs):
    await VoicePipelineService._shutdown_session_for_end_action(
        _pipeline(gw), vs.call_session, None, "agent_end_call", "",
    )


async def _carrier_hangup_then_failed_packet(gw, adapter):
    adapter.gone = True
    with pytest.raises(SessionGoneError):
        await gw.send_audio(SID, b"\x10\x00" * 1600)
    assert SID in gw._sessions and gw.is_session_active(SID) is False


def _decide_patch(monkeypatch, should_record=True):
    async def decide(self, **kw):
        return policy.RecordingDecision(
            should_record=should_record, announcement_required=should_record,
            announcement_text=None, opt_out_dtmf_digit=None, retention_days=90,
            reason="tenant_policy_two_party_announce",
        )

    monkeypatch.setattr(policy.RecordingPolicyService, "decide", decide)


def _notice_session(gw):
    async def synth(session, text, websocket=None, **kw):
        session._tts_delivery_status = "pending"
        try:
            await gw.send_audio(SID, b"\x10\x00" * 1600)
        except SessionGoneError:
            session._tts_delivery_status = "failed"
        return True

    return _voice_session(gw, pipeline=SimpleNamespace(synthesize_and_send_audio=synth))


# ------------------------------------------------ change 1: lost call 1 ----

async def test_call1_agent_end_after_carrier_hangup_keeps_recording(env):
    adapter, gw = await _live_call()
    vs = _voice_session(gw)
    await _carrier_hangup_then_failed_packet(gw, adapter)
    await _end_action(gw, vs)
    assert SID in gw._sessions, "end action removed the session and its buffers"
    assert _caller_bytes(gw) > 0
    status = await tr._save_call_recording(vs, PBX)
    assert _spooled(), "call 1 order: nothing written to the pending folder"
    assert status == "captured"


async def test_end_action_still_tears_down_a_session_that_is_not_registered():
    gw = TelephonyMediaGateway()
    gw.on_call_ended = AsyncMock()
    vs = _voice_session(gw)
    await _end_action(gw, vs)
    gw.on_call_ended.assert_awaited_once_with(SID, "agent_end_call")


async def test_gateway_without_deactivate_session_still_gets_on_call_ended():
    class NoDeactivateGw:
        def __init__(self):
            self.on_call_ended = AsyncMock()

        async def hangup_call(self, call_id, reason):
            return False

        def is_session_active(self, call_id):
            return False

    gw = NoDeactivateGw()
    vs = _voice_session(gw)
    await _end_action(gw, vs)
    gw.on_call_ended.assert_awaited_once_with(SID, "agent_end_call")


async def test_control_normal_agent_end_keeps_recording(env):
    adapter, gw = await _live_call()
    vs = _voice_session(gw)
    await _end_action(gw, vs)
    assert adapter.hangups == [PBX] and SID in gw._sessions
    await tr._save_call_recording(vs, PBX)
    assert _spooled()


async def test_control_deactivate_keeps_the_buffers(env):
    adapter, gw = await _live_call()
    vs = _voice_session(gw)
    await _carrier_hangup_then_failed_packet(gw, adapter)
    gw.deactivate_session(SID)
    await tr._save_call_recording(vs, PBX)
    assert _spooled()


# ------------------------------------------------ change 2: lost call 2 ----

async def test_call2_notice_after_voicemail_hangup_keeps_recording(env, monkeypatch, caplog):
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch)
    adapter, gw = await _live_call(seconds=7.0)
    vs = _notice_session(gw)
    assert await gw.hangup_call(SID, reason="voicemail_detected") is True
    assert gw.is_hangup_requested(SID) is True
    before = _caller_bytes(gw)
    assert before > 0
    caplog.set_level(logging.INFO)
    await agent_first._speak_recording_disclosure(vs)
    assert _caller_bytes(gw) == before, "the notice reset the gate of an ending call"
    assert "recording_disclosure_speaking" not in caplog.text
    assert f"recording_notice_skipped_call_ending call_id={SID[:12]} reason=hangup_requested" in caplog.text
    # Not delivered, not refused: kept and marked "notice not confirmed".
    assert policy.get_disclosure_state(SID, PBX) == policy.DISCLOSURE_FAILED
    assert not getattr(vs, "_recording_refused_by_policy", False)
    status = await tr._save_call_recording(vs, PBX)
    assert status == "captured" and _spooled()


async def test_notice_skipped_when_session_already_inactive(env, monkeypatch, caplog):
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch)
    adapter, gw = await _live_call(seconds=3.0)
    vs = _notice_session(gw)
    await _carrier_hangup_then_failed_packet(gw, adapter)
    before = _caller_bytes(gw)
    caplog.set_level(logging.INFO)
    await agent_first._speak_recording_disclosure(vs)
    assert _caller_bytes(gw) == before
    assert "reason=session_inactive" in caplog.text


async def test_control_voicemail_hangup_without_notice_keeps_recording(env):
    adapter, gw = await _live_call(seconds=7.0)
    vs = _voice_session(gw)
    assert await gw.hangup_call(SID, reason="voicemail_detected") is True
    await tr._save_call_recording(vs, PBX)
    assert _spooled()


async def test_notice_without_hangup_still_resets_the_gate(env, monkeypatch):
    """Privacy design (6e0e221b): what was said before the notice is dropped."""
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch)
    adapter, gw = await _live_call(seconds=3.0)
    vs = _notice_session(gw)
    assert _caller_bytes(gw) > 0
    await agent_first._speak_recording_disclosure(vs)
    assert _caller_bytes(gw) == 0


async def test_recording_disabled_tenant_still_has_buffers_closed_when_hangup_requested(env, monkeypatch):
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch, should_record=False)
    adapter, gw = await _live_call(seconds=3.0)
    vs = _notice_session(gw)
    assert await gw.hangup_call(SID, reason="voicemail_detected") is True
    await agent_first._speak_recording_disclosure(vs)
    assert gw.is_recording_enabled(SID) is False
    assert _caller_bytes(gw) == 0
    assert vs._recording_refused_by_policy is True


# ------------------------------------------------ change 3: silent save ----

async def _checkpoint_copy(gw, vs):
    vs._recording_policy_decided = True
    assert await cp._checkpoint_one(PBX, vs, SID) is True
    assert os.path.isfile(os.path.join(_live_dir(), "caller.pcm"))


async def test_save_with_no_gateway_logs_warning_and_keeps_nothing_silently(env, caplog):
    vs = _voice_session(None)
    caplog.set_level(logging.WARNING)
    status = await tr._save_call_recording(vs, PBX)
    assert status == "none:no_gateway"
    assert f"recording_not_captured call={PBX[:12]} session={SID[:12]} reason=no_gateway checkpoint=none" in caplog.text
    assert [r.levelno for r in caplog.records if "recording_not_captured" in r.getMessage()] == [logging.WARNING]


async def test_save_with_gateway_session_gone_moves_checkpoint_to_pending(env, caplog):
    adapter, gw = await _live_call(seconds=8.0)
    vs = _voice_session(gw)
    await _checkpoint_copy(gw, vs)
    await gw.on_call_ended(SID, "premature")
    caplog.set_level(logging.WARNING)
    status = await tr._save_call_recording(vs, PBX)
    assert status == "recovered_checkpoint"
    assert _spooled(), "the checkpoint copy was not moved to the pending folder"
    assert "reason=gateway_session_gone checkpoint=recovered" in caplog.text
    await asyncio.sleep(0.2)
    assert not os.path.isdir(_live_dir())


async def test_save_with_empty_buffers_says_empty(env, caplog):
    adapter, gw = await _live_call(seconds=0, agent=False)
    vs = _voice_session(gw)
    caplog.set_level(logging.INFO)
    status = await tr._save_call_recording(vs, PBX)
    assert status == "none:empty"
    assert "reason=empty checkpoint=none" in caplog.text
    # An ordinary call (about 70 a day): INFO, not an alarm.
    assert [r.levelno for r in caplog.records if "recording_not_captured" in r.getMessage()] == [logging.INFO]
    assert not _spooled()


async def test_save_too_short_says_too_short_and_recovers_the_longer_checkpoint(env, caplog):
    adapter, gw = await _live_call(seconds=8.0)
    vs = _voice_session(gw)
    await _checkpoint_copy(gw, vs)
    gw.clear_recording_buffer(SID)
    gw._sessions[SID].tts_recording_buffer.clear()
    await gw.on_audio_received(SID, b"\x55" * 320)  # 40 ms: under 0.5 s
    caplog.set_level(logging.WARNING)
    status = await tr._save_call_recording(vs, PBX)
    assert status == "recovered_checkpoint" and _spooled()
    assert "reason=too_short checkpoint=recovered" in caplog.text


async def test_save_too_short_without_checkpoint(env, caplog):
    adapter, gw = await _live_call(seconds=0.2, agent=False)
    vs = _voice_session(gw)
    caplog.set_level(logging.INFO)
    assert await tr._save_call_recording(vs, PBX) == "none:too_short"
    assert "reason=too_short checkpoint=none" in caplog.text
    assert [r.levelno for r in caplog.records if "recording_not_captured" in r.getMessage()] == [logging.INFO]


async def test_failed_checkpoint_recovery_leaves_the_copy_and_logs_error(env, monkeypatch, caplog):
    adapter, gw = await _live_call(seconds=8.0)
    vs = _voice_session(gw)
    await _checkpoint_copy(gw, vs)
    await gw.on_call_ended(SID, "premature")

    def boom(key):
        raise OSError("disk full")

    monkeypatch.setattr(cp, "_recover_sync", boom)
    caplog.set_level(logging.WARNING)
    status = await tr._save_call_recording(vs, PBX)
    await asyncio.sleep(0.2)
    assert status == "none:gateway_session_gone"
    assert os.path.isdir(_live_dir()), "a copy that was not saved was deleted"
    rec = [r for r in caplog.records if "recording_not_captured" in r.getMessage()]
    assert rec and rec[0].levelno == logging.ERROR and "checkpoint=failed" in rec[0].getMessage()
    assert SID not in cp._DONE


async def test_deliberate_refusal_status(env, monkeypatch):
    adapter, gw = await _live_call()
    vs = _voice_session(gw)

    async def refuse(*a, **k):
        return False

    monkeypatch.setattr(tr, "_inbound_recording_persist_allowed", refuse)
    assert await tr._save_call_recording(vs, PBX) == "refused"


@pytest.mark.parametrize("with_gateway", [True, False])
async def test_an_account_that_chose_not_to_record_is_refused_not_missing(env, caplog, with_gateway):
    # Recording disabled by the account's own setting empties the buffers at the
    # notice step. That must never be reported as a recording that went missing
    # (lead's review: every call of such an account would have raised the alarm).
    adapter, gw = await _live_call()
    gw.set_recording_enabled(SID, False)
    vs = _voice_session(gw if with_gateway else None, _recording_refused_by_policy=True)
    caplog.set_level(logging.DEBUG)
    status = await tr._save_call_recording(vs, PBX)
    assert status == "refused"
    assert "recording_not_captured" not in caplog.text
    lifecycle._log_recording_missing_after_answer(PBX, vs, status, 60)
    assert "recording_missing_after_answer" not in caplog.text
    assert not _spooled()


def _spool_write_fails(monkeypatch):
    def broken(entry, wav_bytes):
        raise OSError("disk full")

    monkeypatch.setattr(spool, "write_entry", broken)


def _process_returns(monkeypatch, outcome):
    async def fake_process(entry, *, wav_bytes=None, voice_session=None):
        return outcome

    monkeypatch.setattr(tr, "process_spool_entry", fake_process)


async def test_status_is_never_captured_when_the_audio_reached_neither_disk_nor_storage(env, monkeypatch):
    # The pending folder cannot be written AND the direct save says "try again":
    # the audio is only in memory and the teardown ends the session next. The
    # status must say so, so the teardown's ERROR line fires (lead's review).
    adapter, gw = await _live_call()
    vs = _voice_session(gw)
    _spool_write_fails(monkeypatch)
    _process_returns(monkeypatch, "retry")
    assert await tr._save_call_recording(vs, PBX) == "none:spool_write_failed"
    assert not _spooled()


@pytest.mark.parametrize("spool_ok,outcome,expected", [
    (True, "retry", "captured"),     # on disk in the pending folder: the retry loop saves it
    (True, "saved", "captured"),
    (False, "saved", "captured"),    # the pending folder failed, the direct save worked
    (True, "refused", "refused"),    # a deliberate "do not keep" decision
    (False, "refused", "refused"),
])
async def test_status_after_the_audio_is_mixed(env, monkeypatch, spool_ok, outcome, expected):
    adapter, gw = await _live_call()
    vs = _voice_session(gw)
    if not spool_ok:
        _spool_write_fails(monkeypatch)
    _process_returns(monkeypatch, outcome)
    assert await tr._save_call_recording(vs, PBX) == expected


async def test_notice_runs_as_before_for_a_session_the_gateway_does_not_hold(env, monkeypatch):
    # "Not active" alone is not "ending": a session this gateway has never
    # registered (another key, another gateway type) must not lose its notice.
    from app.domain.services.telephony.modes import agent_first

    class OtherGateway:  # no is_session_registered, no is_hangup_requested
        def __init__(self):
            self.gate = []

        def is_session_active(self, call_id):
            return False

        def set_recording_enabled(self, call_id, enabled):
            self.gate.append((call_id, enabled))

    _decide_patch(monkeypatch)
    other = OtherGateway()

    async def synth(session, text, websocket=None, **kw):
        session._tts_delivery_status = "pending"
        return True

    vs = _voice_session(other, pipeline=SimpleNamespace(synthesize_and_send_audio=synth))
    await agent_first._speak_recording_disclosure(vs)
    # The gate reset that precedes the notice ran: the notice was not skipped.
    assert other.gate[:2] == [(SID, False), (SID, True)]


def test_teardown_line_fires_for_an_incoming_call_too(caplog):
    # An incoming call's length is in another variable than an outgoing one's;
    # the teardown passes the larger of the two.
    caplog.set_level(logging.ERROR)
    lifecycle._log_recording_missing_after_answer(
        PBX, SimpleNamespace(call_id=SID), "none:gateway_session_gone", max(0, 21))
    assert "recording_missing_after_answer" in caplog.text and "duration_s=21" in caplog.text
    import inspect
    source = inspect.getsource(lifecycle._on_call_ended)
    assert "max(outbound_duration_s, inbound_duration_s)" in source


# ------------------------------------------------ change 5: checkpoint pass --

async def test_checkpoint_pass_leaves_copy_when_gateway_session_is_gone(env):
    adapter, gw = await _live_call(seconds=8.0)
    vs = _voice_session(gw)
    await _checkpoint_copy(gw, vs)
    await gw.on_call_ended(SID, "agent_end_call")
    await cp._checkpoint_one(PBX, vs, SID)
    assert os.path.isdir(_live_dir()), "checkpoint pass deleted the copy of a call with no saved audio"


async def test_checkpoint_pass_still_deletes_copy_when_recording_disabled(env):
    adapter, gw = await _live_call(seconds=8.0)
    vs = _voice_session(gw)
    await _checkpoint_copy(gw, vs)
    gw.set_recording_enabled(SID, False)
    await cp._checkpoint_one(PBX, vs, SID)
    assert not os.path.isdir(_live_dir())


# ------------------------------------------------ change 4: teardown line ---

@pytest.mark.parametrize("reason", ["gateway_session_gone", "no_gateway", "spool_write_failed"])
def test_teardown_logs_error_for_answered_call_with_no_recording(caplog, reason):
    vs = SimpleNamespace(call_id=SID)
    caplog.set_level(logging.WARNING)
    lifecycle._log_recording_missing_after_answer(PBX, vs, f"none:{reason}", 13)
    recs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert recs == [(
        logging.ERROR,
        f"recording_missing_after_answer call={PBX[:12]} session={SID[:12]} duration_s=13 reason={reason}",
    )]


@pytest.mark.parametrize("reason", ["empty", "too_short"])
def test_teardown_warns_when_the_emptiness_can_be_the_privacy_rule(caplog, reason):
    # A person who hangs up as the notice begins leaves nothing, by design.
    caplog.set_level(logging.WARNING)
    lifecycle._log_recording_missing_after_answer(PBX, SimpleNamespace(call_id=SID), f"none:{reason}", 13)
    recs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert recs == [(
        logging.WARNING,
        f"recording_missing_after_answer call={PBX[:12]} session={SID[:12]} duration_s=13 reason={reason}",
    )]


@pytest.mark.parametrize("status,duration", [("none:empty", 1), ("captured", 13), ("recovered_checkpoint", 13), (None, 13)])
def test_teardown_is_quiet_otherwise(caplog, status, duration):
    caplog.set_level(logging.WARNING)
    lifecycle._log_recording_missing_after_answer(PBX, SimpleNamespace(call_id=SID), status, duration)
    assert not [r for r in caplog.records if "recording_missing_after_answer" in r.getMessage()]


# ------------------------------------------------ 25 Nov review holes -------

class _SlowConfirmAdapter(FakeAdapter):
    """hangup_confirmed waits on an event, then answers `result`."""

    def __init__(self, release, result=True, raises=False):
        super().__init__()
        self.release, self.result, self.raises = release, result, raises

    async def hangup_confirmed(self, pbx):
        self.hangups.append(pbx)
        await self.release.wait()
        if self.raises:
            raise RuntimeError("ari down")
        if self.result:
            self.gone = True
        return self.result


async def _call_with(adapter, seconds):
    gw = TelephonyMediaGateway()
    await gw.initialize({"sample_rate": 16000})
    await gw.on_call_started(SID, {"adapter": adapter, "pbx_call_id": PBX})
    for _ in range(int(seconds / 0.04)):
        await gw.on_audio_received(SID, b"\x55" * 320)
    return gw


async def test_carrier_hangs_up_just_before_the_notice(env, monkeypatch, caplog):
    """The other side hung up; teardown began (_call_ending) but the gateway
    session is still active: the notice must not start and wipe the call."""
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch)
    adapter = FakeAdapter()
    gw = await _call_with(adapter, 6.0)
    vs = _notice_session(gw)
    vs._call_ending = True  # what _on_call_ended now sets before its first await
    before = _caller_bytes(gw)
    caplog.set_level(logging.INFO)
    await agent_first._speak_recording_disclosure(vs)
    assert _caller_bytes(gw) == before > 0
    assert f"recording_notice_skipped_call_ending call_id={SID[:12]} reason=call_ended" in caplog.text
    assert policy.get_disclosure_state(SID, PBX) == policy.DISCLOSURE_FAILED
    assert await tr._save_call_recording(vs, PBX) == "captured"


async def test_on_call_ended_marks_the_session_before_its_first_await(monkeypatch):
    vs = SimpleNamespace()
    state = SimpleNamespace(get_voice_session=lambda cid: vs if cid == "c-1" else None)
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_ended_calls_in_flight", set())
    seen = {}

    def stop_here(*a, **k):  # the first call after the marker block
        seen["flag"] = getattr(vs, "_call_ending", False)
        raise KeyError("stop")

    monkeypatch.setattr(lifecycle, "get_adapter", stop_here)
    with pytest.raises(KeyError):
        await lifecycle._on_call_ended("c-1")
    assert seen["flag"] is True


async def test_on_call_ended_marker_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("state gone")

    monkeypatch.setattr(lifecycle, "_state", broken)
    monkeypatch.setattr(lifecycle, "_ended_calls_in_flight", set())

    def stop_here(*a, **k):
        raise KeyError("stop")

    monkeypatch.setattr(lifecycle, "get_adapter", stop_here)
    with pytest.raises(KeyError):  # got past the marker block; no RuntimeError escaped it
        await lifecycle._on_call_ended("c-2")


async def test_notice_starts_while_the_voicemail_hangup_is_being_confirmed(env, monkeypatch, caplog):
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch)
    release = asyncio.Event()
    adapter = _SlowConfirmAdapter(release)
    gw = await _call_with(adapter, 7.0)
    vs = _notice_session(gw)
    before = _caller_bytes(gw)
    hup = asyncio.create_task(gw.hangup_call(SID, reason="voicemail_detected"))
    await asyncio.sleep(0)
    assert gw.is_hangup_pending(SID) is True and gw.is_hangup_requested(SID) is False
    caplog.set_level(logging.INFO)
    notice = asyncio.create_task(agent_first._speak_recording_disclosure(vs))
    await asyncio.sleep(0.05)
    assert not notice.done(), "the notice did not wait for the hang-up proof"
    release.set()
    assert await hup is True
    await notice
    assert "reason=hangup_requested" in caplog.text
    assert _caller_bytes(gw) == before
    assert await tr._save_call_recording(vs, PBX) == "captured"


async def test_a_hangup_that_fails_ends_in_the_normal_notice(env, monkeypatch, caplog):
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch)
    release = asyncio.Event()
    adapter = _SlowConfirmAdapter(release, result=False)
    gw = await _call_with(adapter, 3.0)
    vs = _notice_session(gw)
    hup = asyncio.create_task(gw.hangup_call(SID, reason="voicemail_detected"))
    await asyncio.sleep(0)
    caplog.set_level(logging.INFO)
    notice = asyncio.create_task(agent_first._speak_recording_disclosure(vs))
    await asyncio.sleep(0.05)
    assert not notice.done()
    release.set()
    assert await hup is False
    await notice
    assert gw.is_hangup_requested(SID) is False and gw.is_hangup_pending(SID) is False
    # The person is still on the line: the privacy design holds (gate reset) and
    # the speech was attempted.
    assert "recording_notice_skipped_call_ending" not in caplog.text
    assert "recording_disclosure_speaking" in caplog.text
    assert _caller_bytes(gw) == 0, "the gate was not reset: the notice was skipped after a failed hang-up"


async def test_a_legacy_adapter_hangup_proves_nothing(env, monkeypatch, caplog):
    from app.domain.services.telephony.modes import agent_first

    _decide_patch(monkeypatch)

    class Legacy:
        def __init__(self):
            self.calls = []

        async def send_tts_audio(self, pbx, pcmu):
            return None

        async def hangup(self, pbx):
            self.calls.append(pbx)

    adapter = Legacy()
    gw = await _call_with(adapter, 3.0)
    vs = _notice_session(gw)
    assert await gw.hangup_call(SID) is True and adapter.calls == [PBX]
    assert gw.is_hangup_requested(SID) is False
    caplog.set_level(logging.INFO)
    await agent_first._speak_recording_disclosure(vs)
    assert "recording_disclosure_speaking" in caplog.text
    assert _caller_bytes(gw) == 0  # the normal notice ran: gate reset


@pytest.mark.parametrize("mode", ["success", "failure", "exception"])
async def test_hangup_pending_is_false_again_on_every_way_out(env, mode):
    release = asyncio.Event()
    adapter = _SlowConfirmAdapter(release, result=(mode == "success"), raises=(mode == "exception"))
    gw = await _call_with(adapter, 0.2)
    assert gw.is_hangup_pending("unknown-session") is False
    hup = asyncio.create_task(gw.hangup_call(SID))
    await asyncio.sleep(0)
    assert gw.is_hangup_pending(SID) is True
    release.set()
    if mode == "exception":
        with pytest.raises(RuntimeError):
            await hup
    else:
        assert await hup is (mode == "success")
    assert gw.is_hangup_pending(SID) is False
    assert gw.is_hangup_requested(SID) is (mode == "success")


async def test_a_true_inbound_call_whose_gate_never_opened_is_refused_not_missing(env, caplog):
    """Real inbound session: prepare_inbound_recording closes the gate at answer and
    sets _recording_allowed only AFTER the notice. A caller who hangs up during the
    notice leaves it unset: the save must say 'refused' (the deliberate path)."""
    adapter = FakeAdapter()
    gw = TelephonyMediaGateway()
    await gw.initialize({"sample_rate": 16000})
    await gw.on_call_started(SID, {"adapter": adapter, "pbx_call_id": PBX, "recording_enabled": False})
    for _ in range(150):
        await gw.on_audio_received(SID, b"\x55" * 320)
    vs = _voice_session(gw, _inbound_admission={"allowed": True})  # _recording_allowed never set
    caplog.set_level(logging.INFO)
    status = await tr._save_call_recording(vs, PBX)
    assert status == "refused"
    assert "recording_not_captured" not in caplog.text
    lifecycle._log_recording_missing_after_answer(PBX, vs, status, 6)
    assert "recording_missing_after_answer" not in caplog.text


async def test_the_wait_for_a_pending_hangup_gives_up_after_its_limit(env):
    release = asyncio.Event()
    gw = await _call_with(_SlowConfirmAdapter(release), 0.2)
    hup = asyncio.create_task(gw.hangup_call(SID))
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await gw.wait_hangup_resolved(SID, 0.1)
    assert 0.08 <= loop.time() - start < 1.0 and gw.is_hangup_pending(SID) is True
    release.set()
    assert await hup is True
    await gw.wait_hangup_resolved(SID, 0.1)  # nothing pending: returns at once
