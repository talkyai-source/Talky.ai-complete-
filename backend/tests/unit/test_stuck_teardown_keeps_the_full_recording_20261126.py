"""A call whose teardown is stuck must still end with the FULL recording.

If the teardown's steps before the recording save stall for minutes, the
checkpoint pass used to recover the (older, shorter) checkpoint after 120 s and
the pending loop stored it; the teardown's full capture was then "already
saved" (26 Nov 2026 inspection, Q2). The teardown now registers itself
(``checkpoint_teardown_started``, keyed by the session's call_id) and the pass
leaves that call's checkpoint alone while it runs.
"""
import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_service as rs
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import recording as tr

TENANT = "20000000-0000-4000-8000-000000000004"
SID = "sess-stuck-1"
PBX = "talky-out-stuck"
RATE = 8000


class Gw:
    def __init__(self):
        self.caller, self.agent, self._sample_rate = [], [], RATE

    def get_recording_buffer(self, _cid):
        return self.caller

    def get_tts_recording_buffer(self, _cid):
        return self.agent

    def is_recording_enabled(self, _cid):
        return True

    def clear_recording_buffer(self, _cid):
        self.caller.clear()
        self.agent.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    live = {}
    monkeypatch.setattr(lifecycle, "_state",
                        lambda: SimpleNamespace(iter_voice_session_items=lambda: list(live.items())))
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(is_initialized=False))
    stored = []

    async def store_first_wins(entry, *, wav_bytes=None, voice_session=None):
        """The real store: the first save of a call wins, later ones are 'already saved'."""
        wav = wav_bytes if wav_bytes is not None else spool.read_wav(entry.key)
        if any(k == entry.key for k, _ in stored):
            await tr._finish(entry, "already_saved")
            return "saved"
        stored.append((entry.key, wav))
        await tr._finish(entry, "saved")
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", store_first_wins)
    clock = [1000.0]
    monkeypatch.setattr(cp, "_now", lambda: clock[0], raising=False)
    for d in (cp._STATE, cp._DONE, cp._TEARDOWN, tr._PROCESSING):
        d.clear()
    yield SimpleNamespace(live=live, spool=tmp_path / "spool", clock=clock, stored=stored)
    for d in (cp._STATE, cp._DONE, cp._TEARDOWN):
        d.clear()


async def _call_that_ends(env, sid=SID):
    gw = Gw()
    vs = SimpleNamespace(call_id=sid, media_gateway=gw, _dialer_tenant_id=TENANT,
                         config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
                         call_session=None, _recording_policy_decided=True)
    env.live[PBX] = vs
    gw.caller += [bytes([1, 0]) * 800 for _ in range(40)]
    gw.agent += [(0, b"\x10\x00" * 800)]
    assert await cp.checkpoint_pass() == 1
    gw.caller += [bytes([2, 0]) * 800 for _ in range(10)]      # the farewell, after the last tick
    gw.agent += [(36000, b"\x20\x00" * 800)]
    full = rs.mix_stereo_recording(caller_chunks=list(gw.caller), agent_chunks=list(gw.agent), sample_rate=RATE)
    env.live.clear()                                           # lifecycle: pop_voice_session
    return vs, full


def _recovered(caplog):
    return [r for r in caplog.records if "recording_checkpoint_recovered" in r.getMessage()]


async def _passes_until(env, seconds, caplog=None, step=10.0):
    end = env.clock[0] + seconds
    while env.clock[0] < end:
        env.clock[0] += step
        await cp.checkpoint_pass()
        await tr._spool_pass()


# (a) ---------------------------------------------------------------------
async def test_a_stuck_teardown_keeps_its_checkpoint_and_the_full_capture_is_stored(env, caplog):
    vs, full = await _call_that_ends(env)
    cp.checkpoint_teardown_started(vs.call_id)                 # what lifecycle does after the pop
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()
    await _passes_until(env, 300)                              # 300 s of passes: teardown stuck
    assert _recovered(caplog) == []
    assert not spool.has_audio(SID)
    assert env.stored == []
    assert (env.spool / "live" / SID / "caller.pcm").exists()  # the safety copy is still there
    # the teardown wakes up and saves
    assert await tr._save_call_recording(vs, PBX) == "captured"
    assert len(env.stored) == 1 and env.stored[0][1] == full   # the FULL capture, 5 s
    await _passes_until(env, 30)
    assert _recovered(caplog) == []
    for _ in range(100):
        if not (env.spool / "live" / SID).exists():
            break
        await asyncio.sleep(0.02)
    assert not (env.spool / "live" / SID).exists()
    assert cp._STATE == {} and cp._TEARDOWN == {}


# (b) THE KEY TEST -------------------------------------------------------
class _Sentinel(BaseException):
    """Escapes ``except Exception``: stops the real teardown right after the key is registered."""


@pytest.mark.parametrize("inbound", [False, True], ids=["outbound", "inbound"])
async def test_the_key_the_teardown_registers_is_the_key_the_pass_looks_up(env, monkeypatch, inbound):
    sid = "sess-key-in" if inbound else "sess-key-out"
    pbx = "talky-in-key" if inbound else "talky-out-key"
    gw = Gw()
    gw.caller += [bytes([1, 0]) * 800 for _ in range(40)]
    vs = SimpleNamespace(call_id=sid, media_gateway=gw, _dialer_tenant_id=TENANT,
                         config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
                         call_session=None, _recording_policy_decided=True,
                         _recording_allowed=True,
                         _inbound_admission={"allowed": True} if inbound else {})
    if inbound:
        monkeypatch.setattr(tr, "_is_true_inbound_session", lambda v: True)
    env.live[pbx] = vs
    # the pass names the session by str(vs.call_id): that is the checkpoint state's key
    await cp.checkpoint_pass()
    assert [st.session_call_id for st in cp._STATE.values()] == [sid]

    registered = []
    real_start = cp.checkpoint_teardown_started

    def spy(key):
        real_start(key)
        registered.append(key)
        raise _Sentinel()

    monkeypatch.setattr(cp, "checkpoint_teardown_started", spy)
    registry = SimpleNamespace(
        get_voice_session=lambda c: env.live.get(c),
        clear_first_speaker=lambda c: None,
        pop_voice_session=lambda c: env.live.pop(c, None),
        iter_voice_session_items=lambda: list(env.live.items()),
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: registry)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda c: None)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda c: None)
    try:
        with pytest.raises(_Sentinel):
            await lifecycle._on_call_ended(pbx, terminal_at_monotonic=1.0)
    finally:
        lifecycle._ended_calls_in_flight.discard(pbx)
        lifecycle._ended_call_marker_tokens.pop(pbx, None)
    assert registered == [sid]                                 # the real teardown registered it
    assert list(cp._TEARDOWN) == [sid]
    # and the real pass honours exactly that key: 300 s later still nothing recovered
    env.clock[0] += 300
    await cp.checkpoint_pass()
    env.clock[0] += 300
    await cp.checkpoint_pass()
    assert not spool.has_audio(sid)
    assert (env.spool / "live" / sid / "caller.pcm").exists()


# (c) ---------------------------------------------------------------------
async def test_a_teardown_that_never_finishes_does_not_hide_the_copy_for_ever(env, caplog):
    vs, _full = await _call_that_ends(env)
    cp.checkpoint_teardown_started(vs.call_id)
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()
    env.clock[0] += cp._TEARDOWN_MAX_S - 1
    await cp.checkpoint_pass()
    assert _recovered(caplog) == [] and not spool.has_audio(SID)
    env.clock[0] += 2                                          # past the limit: key dropped, clock starts
    await cp.checkpoint_pass()
    assert cp._TEARDOWN == {}
    assert _recovered(caplog) == []
    env.clock[0] += cp._VANISHED_GRACE_S - 1
    await cp.checkpoint_pass()
    assert _recovered(caplog) == []
    env.clock[0] += 2
    await cp.checkpoint_pass()
    [line] = _recovered(caplog)
    assert "source=pass" in line.getMessage()
    assert spool.has_audio(SID)
    assert cp._TEARDOWN == {} and cp._STATE == {}


# (d) ---------------------------------------------------------------------
async def test_after_the_teardown_finished_the_normal_grace_runs_from_that_moment(env, caplog):
    vs, _full = await _call_that_ends(env)
    cp.checkpoint_teardown_started(vs.call_id)
    await cp.checkpoint_pass()
    env.clock[0] += 500                                        # a long, still running teardown
    await cp.checkpoint_pass()
    cp.checkpoint_teardown_finished(vs.call_id)                # the save returned "retry": no done
    assert cp._TEARDOWN == {}
    caplog.set_level(logging.INFO)
    env.clock[0] += cp._VANISHED_GRACE_S - 1
    await cp.checkpoint_pass()
    assert _recovered(caplog) == [] and not spool.has_audio(SID)
    env.clock[0] += 2
    await cp.checkpoint_pass()
    assert len(_recovered(caplog)) == 1 and spool.has_audio(SID)


# (e) ---------------------------------------------------------------------
async def test_a_vanished_session_with_no_teardown_registered_is_recovered_after_120_s(env, caplog):
    await _call_that_ends(env)
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()
    env.clock[0] += cp._VANISHED_GRACE_S - 1
    await cp.checkpoint_pass()
    assert _recovered(caplog) == []
    env.clock[0] += 2
    await cp.checkpoint_pass()
    assert len(_recovered(caplog)) == 1 and spool.has_audio(SID)


async def test_checkpoint_done_forgets_a_registered_teardown(env):
    cp.checkpoint_teardown_started(SID)
    cp.checkpoint_done(SID)
    assert cp._TEARDOWN == {}


# (f) ---------------------------------------------------------------------
async def test_save_call_recording_finishes_the_teardown_when_the_save_raises(env, monkeypatch):
    vs, _full = await _call_that_ends(env)
    cp.checkpoint_teardown_started(vs.call_id)

    async def boom(*a, **kw):
        raise RuntimeError("save blew up")

    monkeypatch.setattr(tr, "_save_call_recording_with_budget", boom)
    with pytest.raises(RuntimeError):
        await tr._save_call_recording(vs, PBX)
    assert cp._TEARDOWN == {}


async def test_save_call_recording_finishes_the_teardown_on_success(env):
    vs, _full = await _call_that_ends(env)
    cp.checkpoint_teardown_started(vs.call_id)
    assert await tr._save_call_recording(vs, PBX) == "captured"
    assert cp._TEARDOWN == {}


# (g) item 2 --------------------------------------------------------------
async def _run_on_ringing(monkeypatch, llm):
    gate = asyncio.Event()
    sess = SimpleNamespace(
        call_id="sess-ring", llm_provider=llm, tts_provider=SimpleNamespace(),
        stt_provider=SimpleNamespace(), call_session=SimpleNamespace(call_id="x"), config=None,
    )
    store = {}
    state = SimpleNamespace(
        has_ringing_warmup=lambda c: False, get_ringing_event=lambda c: store.get("evt"),
        get_voice_session=lambda c: None, voice_session_count=lambda: 0,
        ringing_warmup_count=lambda: 0,
        set_ringing_event=lambda c, e: store.__setitem__("evt", e),
        set_ringing_warmup=lambda c, s, t: store.__setitem__("task", t),
        set_ringing_started_at=lambda c, t: None,
        get_ringing_warmup=lambda c: None,
    )

    async def create_voice_session(_cfg):
        return sess

    orch = SimpleNamespace(create_voice_session=create_voice_session)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: SimpleNamespace(name="asterisk"))
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_outbound_terminal_seen", lambda a, c: False)
    monkeypatch.setattr(lifecycle, "_requires_selected_outbound_session", lambda s, a, c: False)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: orch)
    monkeypatch.setattr(lifecycle, "_build_telephony_session_config", lambda **kw: object())
    monkeypatch.setattr(lifecycle, "_build_outbound_greeting", lambda cs: "hi")
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(is_initialized=False))
    await lifecycle._on_ringing("talky-out-ring")
    store["task"].cancel()
    return gate


async def test_the_llm_warm_up_task_is_referenced_until_done(monkeypatch):
    lifecycle._background_tasks.clear()
    release = asyncio.Event()
    seen = {}

    class Llm:
        async def warm_up(self):
            seen["tracked"] = len(lifecycle._background_tasks)
            await release.wait()

    await _run_on_ringing(monkeypatch, Llm())
    await asyncio.sleep(0.05)
    assert seen["tracked"] == 1                                # held by _background_tasks while running
    assert len(lifecycle._background_tasks) == 1
    release.set()
    await asyncio.sleep(0.05)
    assert lifecycle._background_tasks == set()


async def test_a_failing_llm_warm_up_writes_no_error_line(monkeypatch, caplog):
    lifecycle._background_tasks.clear()

    class Llm:
        async def warm_up(self):
            raise RuntimeError("OpenAI client is not initialized")

    caplog.set_level(logging.DEBUG)
    await _run_on_ringing(monkeypatch, Llm())
    await asyncio.sleep(0.05)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert any("llm_warm_up_failed" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records)
    assert lifecycle._background_tasks == set()


def test_a_teardown_that_never_reaches_the_save_does_not_stay_registered_for_ever(monkeypatch):
    """A call whose recording was already saved at transfer never calls the save in
    its teardown, and it has no checkpoint state for the pass to expire: its entry
    is dropped when a later teardown starts, so the table cannot grow for ever."""
    clock = [1000.0]
    monkeypatch.setattr(cp, "_now", lambda: clock[0], raising=False)
    monkeypatch.setattr(cp, "_TEARDOWN", {}, raising=False)
    cp.checkpoint_teardown_started("old-call")
    clock[0] += cp._TEARDOWN_MAX_S - 1
    cp.checkpoint_teardown_started("recent-call")
    assert set(cp._TEARDOWN) == {"old-call", "recent-call"}     # not yet too old
    clock[0] += 2
    cp.checkpoint_teardown_started("new-call")
    assert set(cp._TEARDOWN) == {"recent-call", "new-call"}     # the old one is gone
