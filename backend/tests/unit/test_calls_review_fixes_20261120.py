"""Review fixes of 20 November 2026 (items 1-4)."""
from unittest.mock import AsyncMock, patch

import pytest

from tests.unit import test_recording_disclosure as base
from tests.unit.test_inbound_recording_keep_and_mark import (
    _CF, _clean, _last_status, _spoke,  # noqa: F401
)
from app.domain.services.telephony.modes.caller_first import prepare_inbound_recording


# ---------------------------------------------------------------- item 1
@pytest.mark.asyncio
async def test_realtime_session_without_pipeline_is_not_recorded(caplog):
    log = []
    vs, _ = base._make_voice_session(log)
    vs.pipeline = None
    gateway = base._GatedInboundMediaGateway(log)
    vs.media_gateway = gateway
    consent = AsyncMock(return_value=None)
    p_container, p_policy = base._patched_policy(base._ANNOUNCE)
    with caplog.at_level("INFO"), p_container, p_policy, patch(
        _CF + "_live_inbound_recording_state", new=AsyncMock(return_value=True)
    ), patch(_CF + "_set_inbound_consent_status", new=consent):
        allowed = await prepare_inbound_recording(vs)
    assert allowed is False
    assert gateway.enabled is False and vs._recording_allowed is False
    assert _last_status(consent) == "declined"
    assert vs._recording_disclosure_attempted is True
    assert "notice_not_spoken" in caplog.text


# ---------------------------------------------------------------- item 3
from app.domain.services.telephony import transfer_recording as tr  # noqa: E402
from tests.unit.test_transfer_limits_and_part_20261120 import _SeqAdapter, _no_waiting  # noqa: E402,F401
from tests.unit.test_transfer_recording import make_entry, make_wav, spool  # noqa: E402,F401


def _open_asterisk_wav(wav: bytes, riff=True, data=True) -> bytes:
    b = bytearray(wav)
    if riff:
        b[4:8] = b"\x00\x00\x00\x00"
    if data:
        b[40:44] = b"\x00\x00\x00\x00"
    return bytes(b)


@pytest.mark.asyncio
@pytest.mark.parametrize("riff,data", [(True, True), (False, True), (True, False)])
async def test_unfinished_asterisk_header_is_refetched(spool, riff, data):  # noqa: F811
    good = make_wav(8000, 1, 1.0)
    adapter = _SeqAdapter([_open_asterisk_wav(good, riff, data), good], order=spool.order)
    entry = make_entry()
    assert await tr.finalize_transfer_entry(entry, adapter=adapter, call_ended=True) == "saved"
    assert entry.transfer_part == "included"
    assert abs(entry.duration_seconds - 3.0) < 0.02


@pytest.mark.asyncio
async def test_garbage_part_is_unreadable_at_once(spool):  # noqa: F811
    adapter = _SeqAdapter([b"NOPE" + b"\x01" * 200], order=spool.order)
    entry = make_entry()
    await tr.finalize_transfer_entry(entry, adapter=adapter, call_ended=True)
    assert [o for o in adapter.order if o[0] == "fetch"].__len__() == 1


@pytest.mark.asyncio
async def test_never_ready_part_is_retried_and_never_deleted(spool):  # noqa: F811
    good = make_wav(8000, 1, 1.0)
    adapter = _SeqAdapter([_open_asterisk_wav(good)], order=spool.order)
    entry = make_entry()
    assert await tr.finalize_transfer_entry(entry, adapter=adapter, call_ended=True) == "retry"
    assert entry.awaiting == "transfer_part"
    assert not any(o[0] == "delete" for o in adapter.order)


# ---------------------------------------------------------------- item 4
from app.domain.services import recording_checkpoint as cp  # noqa: E402
from tests.unit.test_recording_checkpoint import FakeGw, chunk, env, folder, make_session  # noqa: E402,F401


@pytest.mark.asyncio
async def test_closing_the_inbound_gate_deletes_the_crash_copy(env):  # noqa: F811
    gw = FakeGw()
    gw.caller += [chunk(100, 1)]
    vs = make_session("sess-in", gw, _inbound_admission={"x": 1},
                      _recording_allowed=True, _recording_policy_decided=False)
    env.live["pbx1"] = vs
    assert await cp.checkpoint_pass() == 1
    assert folder(env, "sess-in").exists()
    vs._recording_allowed = False
    await cp.checkpoint_pass()
    assert not folder(env, "sess-in").exists()
    assert id(vs) not in cp._STATE


# ---------------------------------------------------------------- item 2
import asyncio  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.domain.services.telephony import lifecycle  # noqa: E402
from app.domain.services.voice_pipeline import action_execution as ax  # noqa: E402
from tests.unit.test_transfer_call_limits import make_vs  # noqa: E402,F401
from tests.unit.test_transfer_tool_wait_20261120 import _FakeExecutor, rig  # noqa: E402,F401

MARK = "_transfer_ringing_since"


def _overlong(vs):
    return lifecycle._collect_expired_sessions(
        [("c", vs)], inactivity_timeout_s=300, max_duration_s=600, soft_cap_s=300
    )


@pytest.mark.parametrize("duration", [400, 700])
def test_fresh_marker_protects_from_soft_and_hard_cap(duration):
    vs = make_vs(duration=duration, stale=False)
    setattr(vs, MARK, time.monotonic() - 10)
    assert _overlong(vs) == ([], [])


def test_fresh_marker_protects_from_stale_reap():
    vs = make_vs(duration=100, stale=True)
    setattr(vs, MARK, time.monotonic() - 10)
    assert _overlong(vs) == ([], [])


@pytest.mark.parametrize("duration", [400, 700])
def test_old_marker_and_no_marker_are_collected(duration):
    vs = make_vs(duration=duration, stale=False)
    assert _overlong(vs) == ([], ["c"])
    setattr(vs, MARK, time.monotonic() - 200)
    assert _overlong(vs) == ([], ["c"])


def _both(rig):
    vs = SimpleNamespace()
    rig.session._voice_session_ref = vs
    return vs


async def _run_transfer(rig, finish, check_set=True):
    vs = _both(rig)
    turn = asyncio.ensure_future(
        ax.execute_connected_voice_action(rig.session, "transfer_call", {}, "yes please"))
    await rig.state.started.wait()
    if check_set:
        assert getattr(rig.session, MARK, None) is not None and getattr(vs, MARK, None) is not None
    await finish()
    try:
        await turn
    except Exception:
        pass
    await asyncio.sleep(0)
    return vs


@pytest.mark.asyncio
async def test_marker_cleared_after_connect(rig):  # noqa: F811
    async def finish():
        rig.state.release.set()
    vs = await _run_transfer(rig, finish)
    assert getattr(rig.session, MARK, None) is None and getattr(vs, MARK, None) is None


@pytest.mark.asyncio
async def test_marker_cleared_after_no_answer(rig, monkeypatch):  # noqa: F811
    from app.domain.services.telephony import adapter_registry

    async def no_answer(*a, **kw):
        assert getattr(rig.session, MARK, None) is not None  # set while it ran
        rig.state.started.set()
        return {"status": "no_answer", "error": "no_answer"}
    monkeypatch.setattr(adapter_registry, "execute_transfer", no_answer)

    async def finish():
        pass
    vs = await _run_transfer(rig, finish, check_set=False)
    assert getattr(rig.session, MARK, None) is None and getattr(vs, MARK, None) is None


@pytest.mark.asyncio
async def test_marker_cleared_after_exception(rig, monkeypatch):  # noqa: F811
    from app.domain.services.telephony import adapter_registry

    async def boom(*a, **kw):
        assert getattr(rig.session, MARK, None) is not None  # set while it ran
        rig.state.started.set()
        raise RuntimeError("x")
    monkeypatch.setattr(adapter_registry, "execute_transfer", boom)
    monkeypatch.setattr(_FakeExecutor, "execute", _raising_execute, raising=True)

    async def finish():
        pass
    vs = await _run_transfer(rig, finish, check_set=False)
    assert getattr(rig.session, MARK, None) is None and getattr(vs, MARK, None) is None


async def _raising_execute(self, *, executor, **kw):
    return await executor()


# silence monitor
from tests.unit.test_audio_ingest_caller_first_silence import (  # noqa: E402
    _make_pipeline, _make_session, _run_until_silence_tick,
)


@pytest.mark.asyncio
async def test_silence_monitor_does_not_hang_up_while_transfer_rings():
    session, pipeline = _make_session("user"), _make_pipeline()
    setattr(session, MARK, time.monotonic())
    await _run_until_silence_tick(session, pipeline, hangup_s=0.03)
    pipeline._shutdown_session_for_end_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_silence_monitor_hangs_up_when_marker_is_old():
    session, pipeline = _make_session("user"), _make_pipeline()
    setattr(session, MARK, time.monotonic() - 200)
    await _run_until_silence_tick(session, pipeline, hangup_s=0.03)
    pipeline._shutdown_session_for_end_action.assert_awaited_once()
