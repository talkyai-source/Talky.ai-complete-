"""The two recording loops do nothing unless this process is the call owner.

A second telephony-enabled process must not treat the owner's LIVE checkpoint
folders as orphans (it would store a partial copy; the owner's full recording
would then be "already saved"). The loops still start everywhere; each pass is
gated by the telephony state backend's ``is_telephony_owner()``, failing OPEN.
"""
import asyncio
import io
import json
import logging
import wave
from types import SimpleNamespace

import pytest

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import recording as tr

TENANT = "20000000-0000-4000-8000-000000000004"
RATE = 8000


def _wav(seconds):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x01\x00\x02\x00" * int(seconds * RATE))
    return out.getvalue()


def _entry(key):
    return spool.SpoolEntry(
        key=key, pbx_call_id="pbx-" + key, session_call_id=key, tenant_id=TENANT, campaign_id=None,
        phone_hint=None, sample_rate=RATE, duration_seconds=1.0, disclosure_state="transmitted",
        callee_country="PK", callee_country_known=True, inbound=False, strict_notice=False,
        awaiting=None, transfer_recording_name=None, transfer_part=None, created_at=spool.utc_now_iso(),
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("RECORDING_CHECKPOINT_INTERVAL_S", "5")
    monkeypatch.setattr(tr, "_LOOP_FIRST_DELAY_S", 0.01)
    monkeypatch.setenv("RECORDING_SPOOL_RETRY_INTERVAL_S", "10")
    backend = SimpleNamespace(owner=True, live=[])  # the real backend's flag starts True
    backend.iter_voice_session_items = lambda: list(backend.live)
    backend.is_telephony_owner = lambda: backend.owner
    monkeypatch.setattr(lifecycle, "_state", lambda: backend)
    cp._LOOP_IDLE.clear()
    for d in (cp._STATE, cp._DONE, cp._TEARDOWN, tr._PROCESSING):
        d.clear()
    processed = []

    async def process(entry, **kw):
        processed.append(entry.key)
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", process)
    monkeypatch.setattr(tr, "_age_seconds", lambda iso, now: tr._ORPHAN_AGE_S + 1)
    yield SimpleNamespace(backend=backend, spool=tmp_path / "spool", processed=processed)
    cp._LOOP_IDLE.clear()


def _foreign_folder(env, key="live-1"):
    d = env.spool / "live" / key
    d.mkdir(parents=True)
    (d / "caller.pcm").write_bytes(b"\x01\x00" * RATE)       # 1 s
    (d / "agent.bin").write_bytes(b"")
    (d / "meta.json").write_text(json.dumps(spool.asdict(_entry(key))))
    return d


# ---- the gate itself

def test_only_an_explicit_false_means_not_the_owner(env, monkeypatch):
    env.backend.owner = False
    assert cp._is_call_owner() is False
    env.backend.owner = True
    assert cp._is_call_owner() is True
    env.backend.is_telephony_owner = lambda: None            # not an explicit False
    assert cp._is_call_owner() is True


def test_fail_open_when_the_backend_raises_or_has_no_method(env, monkeypatch):
    def boom():
        raise RuntimeError("redis down")

    env.backend.is_telephony_owner = boom
    assert cp._is_call_owner() is True
    monkeypatch.setattr(lifecycle, "_state", lambda: SimpleNamespace())
    assert cp._is_call_owner() is True

    def no_backend():
        raise RuntimeError("no backend")

    monkeypatch.setattr(lifecycle, "_state", no_backend)
    assert cp._is_call_owner() is True


def test_the_real_in_memory_backend_is_the_owner(monkeypatch):
    from app.domain.services.telephony.state_backend import LocalOnlyStateBackend

    monkeypatch.setattr(lifecycle, "_state", lambda: LocalOnlyStateBackend())
    assert cp._is_call_owner() is True


# ---- the checkpoint loop

def _clock(monkeypatch, start=1000.0):
    clock = [start]
    monkeypatch.setattr(cp, "_now", lambda: clock[0])
    return clock


def _fast_rounds(monkeypatch):
    monkeypatch.setattr(cp, "_interval_s", lambda: 0.02)


def _count_recoveries(monkeypatch):
    calls = []
    real = cp.recover_orphans

    async def counted():
        calls.append(1)
        return await real()

    monkeypatch.setattr(cp, "recover_orphans", counted)
    return calls


async def _stop(task, stop):
    stop.set()
    await asyncio.wait_for(task, 5)


async def test_a_boot_order_flag_true_then_false_never_recovers_a_foreign_folder(env, monkeypatch):
    """The real Redis flag starts True and the awaited acquire turns it False."""
    _fast_rounds(monkeypatch)
    clock = _clock(monkeypatch)
    folder = _foreign_folder(env)
    calls = _count_recoveries(monkeypatch)
    assert env.backend.is_telephony_owner() is True            # loop start: the default
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.05)                                  # first rounds run on the default True
    env.backend.owner = False                                  # the acquire answered False
    for t in (1005.0, 1011.0, 1100.0, 5000.0):
        clock[0] = t
        await asyncio.sleep(0.1)
    await _stop(task, stop)
    assert calls == [] and folder.exists() and not spool.has_audio("live-1")


async def test_b_the_real_owner_recovers_once_at_the_first_round_after_the_delay(env, monkeypatch):
    _fast_rounds(monkeypatch)
    clock = _clock(monkeypatch)
    folder = _foreign_folder(env)
    calls = _count_recoveries(monkeypatch)
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.15)
    assert calls == [] and folder.exists()                     # t = 0: not before 10 s
    clock[0] = 1000.0 + cp._ORPHAN_RECOVERY_DELAY_S - 0.1
    await asyncio.sleep(0.15)
    assert calls == [] and folder.exists()
    clock[0] = 1000.0 + cp._ORPHAN_RECOVERY_DELAY_S
    await asyncio.sleep(0.4)                                   # many rounds
    clock[0] += 500
    await asyncio.sleep(0.2)
    await _stop(task, stop)
    assert calls == [1]
    assert spool.has_audio("live-1") and not folder.exists()


async def test_c_lost_at_boot_won_later_recovers_once_after_that(env, monkeypatch, caplog):
    _fast_rounds(monkeypatch)
    clock = _clock(monkeypatch)
    folder = _foreign_folder(env)
    calls = _count_recoveries(monkeypatch)
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.05)
    env.backend.owner = False
    clock[0] = 1030.0
    await asyncio.sleep(0.15)
    assert calls == [] and folder.exists()
    clock[0] = 1060.0
    env.backend.owner = True                                   # won by the stale-lock retry
    await asyncio.sleep(0.4)
    await _stop(task, stop)
    assert calls == [1] and spool.has_audio("live-1") and not folder.exists()
    text = [r.getMessage() for r in caplog.records]
    assert text.count("recording_loops_idle reason=not_call_owner loop=recording_checkpoint_loop") == 1
    assert text.count("recording_loops_active loop=recording_checkpoint_loop") == 1


class _Gw:
    def __init__(self):
        self.caller = [b"\x01\x00" * 800 for _ in range(10)]
        self.agent = []
        self._sample_rate = RATE

    def get_recording_buffer(self, _c):
        return self.caller

    def get_tts_recording_buffer(self, _c):
        return self.agent

    def is_recording_enabled(self, _c):
        return True


def _own_session(sid="own-1"):
    return SimpleNamespace(
        call_id=sid, media_gateway=_Gw(), _dialer_tenant_id=TENANT,
        config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
        call_session=None, _recording_policy_decided=True,
    )


async def test_d_the_pass_is_not_gated_a_non_owner_keeps_copies_of_its_own_calls(env, monkeypatch):
    _fast_rounds(monkeypatch)
    _clock(monkeypatch)
    env.backend.owner = False
    vs = _own_session()
    env.backend.live = [("pbx-own", vs)]
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    pcm = env.spool / "live" / "own-1" / "caller.pcm"
    await asyncio.sleep(0.2)
    first = pcm.stat().st_size
    vs.media_gateway.caller += [b"\x02\x00" * 800 for _ in range(10)]
    await asyncio.sleep(0.2)
    await _stop(task, stop)
    assert first > 0 and pcm.stat().st_size > first


async def test_e_a_live_call_of_this_process_is_not_an_orphan_but_a_foreign_folder_is(env, monkeypatch):
    _fast_rounds(monkeypatch)
    clock = _clock(monkeypatch)
    foreign = _foreign_folder(env)
    vs = _own_session()
    env.backend.live = [("pbx-own", vs)]
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.2)                                   # the pass checkpoints the own call
    own = env.spool / "live" / "own-1"
    assert own.exists() and [st.key for st in cp._STATE.values()] == ["own-1"]
    clock[0] = 1000.0 + cp._ORPHAN_RECOVERY_DELAY_S
    await asyncio.sleep(0.3)
    await _stop(task, stop)
    assert spool.has_audio("live-1") and not foreign.exists()   # the foreign one is recovered
    assert own.exists() and not spool.has_audio("own-1")        # the own live call is not


async def test_f_interval_disabled_owner_recovers_once_after_the_delay_then_returns(env, monkeypatch):
    monkeypatch.setattr(cp, "_interval_s", lambda: None)
    monkeypatch.setattr(cp, "_ORPHAN_RECOVERY_DELAY_S", 0.05)
    monkeypatch.setattr(cp, "_ORPHAN_RECOVERY_WINDOW_S", 0.5)
    folder = _foreign_folder(env)
    calls = _count_recoveries(monkeypatch)
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.01)
    assert calls == [] and folder.exists()                     # not before the delay
    await asyncio.wait_for(task, 3)                            # returns by itself
    assert calls == [1] and spool.has_audio("live-1")


async def test_f_interval_disabled_non_owner_never_recovers_and_returns_within_the_window(env, monkeypatch):
    monkeypatch.setattr(cp, "_interval_s", lambda: None)
    monkeypatch.setattr(cp, "_ORPHAN_RECOVERY_DELAY_S", 0.05)
    monkeypatch.setattr(cp, "_ORPHAN_RECOVERY_WINDOW_S", 0.3)
    env.backend.owner = False
    folder = _foreign_folder(env)
    calls = _count_recoveries(monkeypatch)
    await asyncio.wait_for(cp.recording_checkpoint_loop(asyncio.Event()), 3)
    assert calls == [] and folder.exists() and not spool.has_audio("live-1")


# ---- the pending loop

async def test_a_non_owner_spool_pass_processes_nothing_and_deletes_nothing(env):
    env.backend.owner = False
    spool.write_entry(_entry("pend-1"), _wav(1.0))
    assert await tr._spool_pass() == 0
    assert env.processed == []
    assert spool.has_audio("pend-1")


async def test_the_spool_pass_runs_once_the_process_is_the_owner(env):
    env.backend.owner = False
    spool.write_entry(_entry("pend-1"), _wav(1.0))
    assert await tr._spool_pass() == 0
    env.backend.owner = True
    assert await tr._spool_pass() == 1
    assert env.processed == ["pend-1"]


async def test_the_spool_loop_skips_the_whole_round_for_a_non_owner(env, monkeypatch, caplog):
    env.backend.owner = False
    spool.write_entry(_entry("pend-1"), _wav(1.0))
    extras = []
    import app.domain.services.recording_spool as rsp
    monkeypatch.setattr(rsp, "sweep_leftovers", lambda *a: extras.append("sweep") or 0)
    monkeypatch.setattr(tr, "_env_float", lambda n, d, minimum=0.0: 0.02 if "INTERVAL" in n else d)
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    task = asyncio.create_task(tr.recording_spool_loop(stop))
    await asyncio.sleep(0.15)
    assert env.processed == [] and extras == []
    env.backend.owner = True
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert env.processed and set(env.processed) == {"pend-1"}   # the fake never removes it
    text = [r.getMessage() for r in caplog.records]
    assert text.count("recording_loops_idle reason=not_call_owner loop=recording_spool_loop") == 1


async def test_a_failing_backend_does_not_stop_the_spool_pass(env):
    def boom():
        raise RuntimeError("redis down")

    env.backend.is_telephony_owner = boom
    spool.write_entry(_entry("pend-1"), _wav(1.0))
    assert await tr._spool_pass() == 1


async def test_the_teardowns_own_save_is_not_gated(env, monkeypatch):
    """A process tearing down its own call always saves it, owner or not."""
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(is_initialized=False))
    gw = SimpleNamespace(
        _sample_rate=RATE,
        get_recording_buffer=lambda c: [b"\x01\x00" * 800 for _ in range(20)],
        get_tts_recording_buffer=lambda c: [],
        clear_recording_buffer=lambda c: None,
    )
    vs = SimpleNamespace(
        call_id="own-call", media_gateway=gw, _dialer_tenant_id=TENANT,
        config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
        call_session=None, _recording_policy_decided=True,
    )
    env.backend.owner = False
    assert await tr._save_call_recording_with_budget(vs, "pbx-own") == "captured"
    assert env.processed == ["own-call"]
