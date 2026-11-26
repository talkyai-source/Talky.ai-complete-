"""Live checkpoint: the audio of a call in progress is copied to disk every few
seconds, and what a dead process left behind becomes a pending recording
(owner decision 2026-11-18: every recording is recorded at any cost)."""
import asyncio
import json
import logging
import os
import struct
from types import SimpleNamespace

import pytest

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_service as rs
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import recording as tr

TENANT = "20000000-0000-4000-8000-000000000004"
RATE = 8000
HDR = struct.Struct("<qI")


def chunk(n, value):
    return bytes([value]) * n


class FakeGw:
    def __init__(self):
        self.caller = []
        self.agent = []
        self.enabled = True
        self._sample_rate = RATE
        self.cleared = []

    def get_recording_buffer(self, _cid):
        return self.caller

    def get_tts_recording_buffer(self, _cid):
        return self.agent

    def is_recording_enabled(self, _cid):
        return self.enabled

    def set_recording_enabled(self, _cid, enabled):
        self.enabled = enabled
        if not enabled:
            self.caller.clear()
            self.agent.clear()

    def clear_recording_buffer(self, cid):
        self.cleared.append(cid)


def make_session(sid, gw=None, **extra):
    return SimpleNamespace(
        call_id=sid, media_gateway=gw or FakeGw(), _dialer_tenant_id=TENANT,
        config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
        call_session=None, **{"_recording_policy_decided": True, **extra},
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.delenv("RECORDING_CHECKPOINT_INTERVAL_S", raising=False)
    live = {}
    monkeypatch.setattr(
        lifecycle, "_state",
        lambda: SimpleNamespace(iter_voice_session_items=lambda: list(live.items())),
    )
    saved_state, saved_done = dict(cp._STATE), dict(cp._DONE)
    cp._STATE.clear()
    cp._DONE.clear()
    yield SimpleNamespace(live=live, spool=tmp_path / "spool")
    cp._STATE.clear()
    cp._STATE.update(saved_state)
    cp._DONE.clear()
    cp._DONE.update(saved_done)


def folder(env, key):
    return env.spool / "live" / key


def read_agent(path):
    blob = path.read_bytes()
    out, pos = [], 0
    while pos < len(blob):
        off, ln = HDR.unpack_from(blob, pos)
        out.append((off, blob[pos + HDR.size:pos + HDR.size + ln]))
        pos += HDR.size + ln
    return out


# ------------------------------------------------------------------ passes

async def test_two_passes_append_only_new_chunks(env):
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(100, 1), chunk(100, 2)]
    gw.agent += [(0, chunk(50, 3))]
    assert await cp.checkpoint_pass() == 1
    gw.caller += [chunk(100, 4)]
    gw.agent += [(400, chunk(60, 5))]
    assert await cp.checkpoint_pass() == 1
    d = folder(env, "sess-1")
    assert (d / "caller.pcm").read_bytes() == b"".join(gw.caller)
    assert read_agent(d / "agent.bin") == gw.agent
    meta = json.loads((d / "meta.json").read_text())
    assert meta["tenant_id"] == TENANT and meta["sample_rate"] == RATE
    assert meta["duration_seconds"] == 0.0


async def test_reset_between_passes_restarts_the_files(env):
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(100, 1), chunk(100, 2)]
    gw.agent += [(0, chunk(50, 3))]
    await cp.checkpoint_pass()
    # the live path clears both lists at the notice and refills them
    gw.caller.clear()
    gw.agent.clear()
    gw.caller += [chunk(100, 9), chunk(100, 8), chunk(100, 7)]
    gw.agent += [(10, chunk(20, 6))]
    await cp.checkpoint_pass()
    d = folder(env, "sess-1")
    assert (d / "caller.pcm").read_bytes() == b"".join(gw.caller)
    assert chunk(100, 1) not in (d / "caller.pcm").read_bytes()
    assert read_agent(d / "agent.bin") == [(10, chunk(20, 6))]


async def test_recording_turned_off_removes_the_folder(env):
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(100, 1)]
    await cp.checkpoint_pass()
    assert folder(env, "sess-1").exists()
    gw.set_recording_enabled("sess-1", False)
    await cp.checkpoint_pass()
    assert not folder(env, "sess-1").exists()
    assert cp._STATE == {}


async def test_inbound_session_with_closed_gate_is_never_written(env):
    gw = FakeGw()
    gw.caller += [chunk(100, 1)]
    env.live["pbx1"] = make_session("sess-in", gw, _inbound_admission={"x": 1})
    assert await cp.checkpoint_pass() == 0
    assert not (env.spool / "live").exists()


async def test_inbound_session_with_open_gate_is_written(env):
    gw = FakeGw()
    gw.caller += [chunk(100, 1)]
    env.live["pbx1"] = make_session(
        "sess-in", gw, _inbound_admission={"x": 1},
        _recording_allowed=True, _recording_policy_decided=False,
    )
    assert await cp.checkpoint_pass() == 1
    assert (folder(env, "sess-in") / "meta.json").exists()


@pytest.mark.parametrize("flag", ["_transfer_connected", "_recording_saved_at_transfer"])
async def test_transferred_session_is_not_written(env, flag):
    gw = FakeGw()
    gw.caller += [chunk(100, 1)]
    env.live["pbx1"] = make_session("sess-t", gw, **{flag: True})
    assert await cp.checkpoint_pass() == 0
    assert not (env.spool / "live").exists()


async def test_checkpoint_done_removes_folder_and_is_not_recreated(env):
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(100, 1)]
    await cp.checkpoint_pass()
    cp.checkpoint_done("sess-1")
    # The files are removed off the event loop, so the end of a call never
    # waits on the disk: give that a moment.
    for _ in range(100):
        if not folder(env, "sess-1").exists():
            break
        await asyncio.sleep(0.02)
    assert not folder(env, "sess-1").exists()
    gw.caller += [chunk(100, 2)]
    assert await cp.checkpoint_pass() == 0
    assert not folder(env, "sess-1").exists()
    assert list(spool.list_entries()) == []


async def test_vanished_session_is_recovered_to_pending(env, monkeypatch):
    # Contract changed 2026-11-25: a session gone from the registry belongs to
    # its teardown for cp._VANISHED_GRACE_S; it is recovered only when that
    # grace passes without checkpoint_done (was: recovered on the very next pass).
    clock = [5000.0]
    monkeypatch.setattr(cp, "_now", lambda: clock[0])
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(6000, 1), chunk(6000, 2)]
    gw.agent += [(0, chunk(400, 3)), (3000, chunk(400, 4))]
    await cp.checkpoint_pass()
    env.live.clear()  # process-internal teardown without checkpoint_done
    await cp.checkpoint_pass()  # first seen gone: left to its teardown
    assert spool.list_entries() == []
    clock[0] += cp._VANISHED_GRACE_S + 1
    await cp.checkpoint_pass()
    [entry] = spool.list_entries()
    assert entry.tenant_id == TENANT
    assert entry.session_call_id == "sess-1" and entry.pbx_call_id == "pbx1"
    assert entry.sample_rate == RATE and entry.key == "sess-1"
    assert entry.duration_seconds == pytest.approx(12000 / (RATE * 2))
    assert spool.read_wav("sess-1") == rs.mix_stereo_recording(
        caller_chunks=list(gw.caller), agent_chunks=list(gw.agent), sample_rate=RATE,
    )
    assert not folder(env, "sess-1").exists()
    assert cp._STATE == {}


# ---------------------------------------------------------------- recovery

def make_orphan(env, key, caller=b"", agent=b"", meta=True):
    d = folder(env, key)
    d.mkdir(parents=True)
    (d / "caller.pcm").write_bytes(caller)
    (d / "agent.bin").write_bytes(agent)
    if meta:
        entry = tr._build_spool_entry(
            make_session(key), "pbx-old", sample_rate=RATE, duration=0.0,
            awaiting_transfer=False, transfer_recording_name=None,
        )
        entry.key = key
        (d / "meta.json").write_text(json.dumps(spool.asdict(entry)))
    return d


async def test_startup_recovery_ignores_cut_off_last_record(env):
    caller = chunk(9000, 1) + chunk(9000, 2)
    good = HDR.pack(0, 300) + chunk(300, 5)
    cut = HDR.pack(500, 300) + chunk(100, 6)  # process died mid-record
    make_orphan(env, "old-call", caller, good + cut)
    assert await cp.recover_orphans() == 1
    [entry] = spool.list_entries()
    assert entry.key == "old-call" and entry.pbx_call_id == "pbx-old"
    assert spool.read_wav("old-call") == rs.mix_stereo_recording(
        caller_chunks=[caller], agent_chunks=[(0, chunk(300, 5))], sample_rate=RATE,
    )
    assert not folder(env, "old-call").exists()


async def test_startup_recovery_drops_audio_under_half_a_second(env):
    make_orphan(env, "tiny", chunk(2000, 1))
    assert await cp.recover_orphans() == 0
    assert spool.list_entries() == []
    assert not folder(env, "tiny").exists()


async def test_unreadable_meta_is_left_alone_and_logged(env, caplog):
    make_orphan(env, "broken", chunk(9000, 1), meta=False)
    folder(env, "broken").joinpath("meta.json").write_text("{not json")
    make_orphan(env, "fine", chunk(9000, 1))
    with caplog.at_level(logging.WARNING):
        assert await cp.recover_orphans() == 1
    assert folder(env, "broken").exists()
    assert "recording_checkpoint_unreadable" in caplog.text
    assert [e.key for e in spool.list_entries()] == ["fine"]


async def test_existing_pending_entry_is_not_overwritten(env):
    make_orphan(env, "dup", chunk(9000, 1))
    entry = tr._build_spool_entry(
        make_session("dup"), "pbx", sample_rate=RATE, duration=5.0,
        awaiting_transfer=False, transfer_recording_name=None,
    )
    # 2026-11-26: "has audio" means a wav larger than a bare 44-byte header, so the
    # pending copy here is made realistic (was the 8-byte b"RIFFkeep", a fake that now counts as empty).
    KEEP = b"RIFFkeep" + bytes(200)
    spool.write_entry(entry, KEEP)
    assert await cp.recover_orphans() == 0
    assert spool.read_wav("dup") == KEEP
    assert not folder(env, "dup").exists()


async def test_orphan_with_bad_name_is_never_touched(env):
    bad = env.spool / "live" / "bad name!"
    bad.mkdir(parents=True)
    (bad / "meta.json").write_text("{}")
    assert await cp.recover_orphans() == 0
    assert bad.exists()


def test_bad_key_never_becomes_a_path(env):
    for key in ("../x", "a/b", "", "a" * 81, "x y", None):
        with pytest.raises(ValueError):
            cp._folder(key)
    cp.checkpoint_done("../../etc")  # must not raise or remove anything outside
    assert env.spool.parent.exists()


# ------------------------------------------------------------ resilience

async def test_error_on_one_call_does_not_stop_another(env, monkeypatch, caplog):
    g1, g2 = FakeGw(), FakeGw()
    g1.caller += [chunk(100, 1)]
    g2.caller += [chunk(100, 2)]
    env.live["p1"] = make_session("sess-bad", g1)
    env.live["p2"] = make_session("sess-ok", g2)
    real = cp._write_files

    def flaky(key, *a):
        if key == "sess-bad":
            raise OSError("disk")
        return real(key, *a)

    monkeypatch.setattr(cp, "_write_files", flaky)
    with caplog.at_level(logging.ERROR):
        assert await cp.checkpoint_pass() == 1
    assert "recording_checkpoint_error call=sess-bad err=OSError" in caplog.text
    assert (folder(env, "sess-ok") / "caller.pcm").read_bytes() == chunk(100, 2)
    # the failed call starts over from zero on the next pass
    monkeypatch.setattr(cp, "_write_files", real)
    await cp.checkpoint_pass()
    assert (folder(env, "sess-bad") / "caller.pcm").read_bytes() == chunk(100, 1)


# -------------------------------------------------------------- the loop

@pytest.mark.parametrize("raw,expected", [
    (None, 10.0), ("", 10.0), ("1", 5.0), ("7", 7.0), ("0", None), ("-3", None),
])
def test_interval_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("RECORDING_CHECKPOINT_INTERVAL_S", raising=False)
    else:
        monkeypatch.setenv("RECORDING_CHECKPOINT_INTERVAL_S", raw)
    assert cp._interval_s() == expected


async def test_loop_disabled_recovers_once_then_returns(env, monkeypatch):
    monkeypatch.setenv("RECORDING_CHECKPOINT_INTERVAL_S", "0")
    make_orphan(env, "old-call", chunk(9000, 1))
    passes = []
    monkeypatch.setattr(cp, "checkpoint_pass", lambda: passes.append(1))
    await asyncio.wait_for(cp.recording_checkpoint_loop(asyncio.Event()), 5)
    assert passes == []
    assert [e.key for e in spool.list_entries()] == ["old-call"]


async def test_loop_runs_passes_until_stopped(env, monkeypatch, caplog):
    monkeypatch.setenv("RECORDING_CHECKPOINT_INTERVAL_S", "5")
    monkeypatch.setattr(cp, "_interval_s", lambda: 0.01)
    stop = asyncio.Event()
    count = []

    async def fake_pass():
        count.append(1)
        if len(count) == 1:
            raise RuntimeError("boom")  # logged, loop continues
        if len(count) >= 3:
            stop.set()
        return 0

    monkeypatch.setattr(cp, "checkpoint_pass", fake_pass)
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(cp.recording_checkpoint_loop(stop), 5)
    assert len(count) >= 3
    assert "recording_checkpoint_loop_started interval_s=0.01" in caplog.text
    assert "recording_checkpoint_error err=RuntimeError" in caplog.text
    assert "recording_checkpoint_loop_stopped" in caplog.text


# ------------------------------------------------- hooks in the save path

@pytest.fixture
def hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=False, db_pool=None),
    )
    calls = []
    monkeypatch.setattr(cp, "checkpoint_done", lambda sid: calls.append(sid))
    state = SimpleNamespace(calls=calls, outcome="saved")

    async def fake_process(entry, *, wav_bytes=None, voice_session=None, **kw):
        return state.outcome

    monkeypatch.setattr(tr, "process_spool_entry", fake_process)
    return state


async def test_hook_when_inbound_gate_discards(hooks):
    gw = FakeGw()
    gw.caller += [chunk(20000, 1)]
    await tr._save_call_recording_with_budget(
        make_session("sess-1", gw, _inbound_admission={"x": 1}), "pbx1",
    )
    assert hooks.calls == ["sess-1"]


async def test_hook_when_no_recording_data(hooks):
    await tr._save_call_recording_with_budget(make_session("sess-1"), "pbx1")
    assert hooks.calls == ["sess-1"]


async def test_hook_when_recording_under_half_a_second(hooks):
    gw = FakeGw()
    gw.caller += [chunk(2000, 1)]
    await tr._save_call_recording_with_budget(make_session("sess-1", gw), "pbx1")
    assert hooks.calls == ["sess-1"]


async def test_hook_after_pending_write_succeeds(hooks):
    gw = FakeGw()
    gw.caller += [chunk(20000, 1)]
    await tr._save_call_recording_with_budget(make_session("sess-1", gw), "pbx1")
    assert hooks.calls == ["sess-1"]
    assert len(spool.list_entries()) == 1


def _break_spool(monkeypatch):
    def boom(entry, wav):
        raise OSError("disk full")

    monkeypatch.setattr(spool, "write_entry", boom)


@pytest.mark.parametrize("outcome", ["saved", "refused"])
async def test_hook_when_pending_write_failed_and_direct_save_is_final(hooks, monkeypatch, outcome):
    _break_spool(monkeypatch)
    hooks.outcome = outcome
    gw = FakeGw()
    gw.caller += [chunk(20000, 1)]
    await tr._save_call_recording_with_budget(make_session("sess-1", gw), "pbx1")
    assert hooks.calls == ["sess-1"]


async def test_no_hook_when_pending_write_failed_and_outcome_is_retry(hooks, monkeypatch):
    _break_spool(monkeypatch)
    hooks.outcome = "retry"
    gw = FakeGw()
    gw.caller += [chunk(20000, 1)]
    await tr._save_call_recording_with_budget(make_session("sess-1", gw), "pbx1")
    assert hooks.calls == []


async def test_hook_failure_never_breaks_the_save_path(hooks, monkeypatch):
    def boom(_sid):
        raise RuntimeError("nope")

    monkeypatch.setattr(cp, "checkpoint_done", boom)
    gw = FakeGw()
    gw.caller += [chunk(20000, 1)]
    await tr._save_call_recording_with_budget(make_session("sess-1", gw), "pbx1")
    assert len(spool.list_entries()) == 1


@pytest.mark.asyncio
async def test_nothing_is_copied_before_the_call_has_taken_its_recording_decision(env):
    """Until the notice decision exists, the audio may belong to a tenant who
    switched recording off, or be what was said before the spoken notice."""
    gw = FakeGw()
    gw.caller.extend([b"\x01\x02" * 160, b"\x03\x04" * 160])
    vs = make_session("11111111-1111-4111-8111-00000000aaaa", gw, _recording_policy_decided=False)
    env.live["pbx-undecided"] = vs
    assert await cp.checkpoint_pass() == 0
    assert not (env.spool / "live").exists() or not any((env.spool / "live").iterdir())

    vs._recording_policy_decided = True
    assert await cp.checkpoint_pass() == 1
    assert (env.spool / "live" / vs.call_id / "caller.pcm").exists()
