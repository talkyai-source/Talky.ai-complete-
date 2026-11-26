"""Start-up recovery waits for a DECIDED ownership answer, not for a clock.

The Redis backend's owner flag starts True and only the ownership acquire
assigns the real answer. A slow acquire that answers False must never let a
non-owner process recover the owner's live folders.
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
from app.domain.services.telephony.state_backend import (
    LocalOnlyStateBackend,
    RedisBackedStateBackend,
)

TENANT = "20000000-0000-4000-8000-000000000004"
RATE = 8000


def _entry(key):
    return spool.SpoolEntry(
        key=key, pbx_call_id="pbx-" + key, session_call_id=key, tenant_id=TENANT, campaign_id=None,
        phone_hint=None, sample_rate=RATE, duration_seconds=1.0, disclosure_state="transmitted",
        callee_country="PK", callee_country_known=True, inbound=False, strict_notice=False,
        awaiting=None, transfer_recording_name=None, transfer_part=None, created_at=spool.utc_now_iso(),
    )


def _wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x01\x00\x02\x00" * RATE)
    return out.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("RECORDING_CHECKPOINT_INTERVAL_S", "5")
    monkeypatch.setattr(cp, "_interval_s", lambda: 0.02)
    clock = [1000.0]
    monkeypatch.setattr(cp, "_now", lambda: clock[0])
    # behaves like the real Redis backend: flag True, undecided until the acquire answers
    backend = SimpleNamespace(owner=True, decided=False, live=[])
    backend.iter_voice_session_items = lambda: list(backend.live)
    backend.is_telephony_owner = lambda: backend.owner
    backend.telephony_ownership_decided = lambda: backend.decided
    monkeypatch.setattr(lifecycle, "_state", lambda: backend)
    cp._LOOP_IDLE.clear()
    cp._UNDECIDED_SINCE.clear()
    cp._LOOP_STARTED.clear()
    cp._UNDECIDED_WARNED.clear()
    for d in (cp._STATE, cp._DONE, cp._TEARDOWN, tr._PROCESSING):
        d.clear()
    processed = []

    async def process(entry, **kw):
        processed.append(entry.key)
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", process)
    monkeypatch.setattr(tr, "_age_seconds", lambda iso, now: tr._ORPHAN_AGE_S + 1)
    calls = []
    real = cp.recover_orphans

    async def counted():
        calls.append(1)
        return await real()

    monkeypatch.setattr(cp, "recover_orphans", counted)
    yield SimpleNamespace(backend=backend, clock=clock, spool=tmp_path / "spool", calls=calls, processed=processed)
    cp._LOOP_IDLE.clear()
    cp._UNDECIDED_SINCE.clear()
    cp._LOOP_STARTED.clear()
    cp._UNDECIDED_WARNED.clear()


def _foreign_folder(env, key="live-1"):
    d = env.spool / "live" / key
    d.mkdir(parents=True)
    (d / "caller.pcm").write_bytes(b"\x01\x00" * RATE)
    (d / "agent.bin").write_bytes(b"")
    (d / "meta.json").write_text(json.dumps(spool.asdict(_entry(key))))
    return d


async def _run(env, steps):
    """Run the loop; steps = [(clock value, optional callable run before waiting)]."""
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.05)
    for t, action in steps:
        env.clock[0] = t
        if action:
            action()
        await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, 5)


# ---- (a) slow acquire answering False

async def test_a_slow_acquire_that_answers_false_never_recovers_the_foreign_folder(env):
    folder = _foreign_folder(env)

    def answer_false():
        env.backend.owner, env.backend.decided = False, True

    await _run(env, [(1010.0, None), (1011.0, answer_false), (1100.0, None), (5000.0, None)])
    assert env.calls == [] and folder.exists() and not spool.has_audio("live-1")


# ---- (b) slow acquire answering True

async def test_b_slow_acquire_that_answers_true_recovers_once_after_the_answer(env):
    folder = _foreign_folder(env)
    seen = {}

    def answer_true():
        seen["before"] = list(env.calls)
        env.backend.owner, env.backend.decided = True, True

    await _run(env, [(1010.0, None), (1011.0, answer_true), (1500.0, None)])
    assert seen["before"] == []                      # not at 10 s: no answer was there yet
    assert env.calls == [1] and spool.has_audio("live-1") and not folder.exists()


# ---- (c) undecided for ever

async def test_c_undecided_longer_than_the_maximum_fails_open_with_one_warning(env, caplog):
    folder = _foreign_folder(env)
    caplog.set_level(logging.DEBUG)
    await _run(env, [(1059.0, None), (1061.0, None), (1200.0, None)])
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert [w for w in warnings if w.startswith("recording_ownership_undecided_timeout")] == [
        "recording_ownership_undecided_timeout loop=recording_checkpoint_loop"
    ]
    assert env.calls == [1] and spool.has_audio("live-1") and not folder.exists()
    assert not [r for r in caplog.records if "recording_loops_idle" in r.getMessage()]


async def test_c_undecided_does_not_recover_before_the_maximum(env):
    folder = _foreign_folder(env)
    await _run(env, [(1059.0, None)])
    assert env.calls == [] and folder.exists()


# ---- (d) fail open

async def test_d_a_backend_without_the_method_or_raising_behaves_as_before(env):
    folder = _foreign_folder(env)
    del env.backend.telephony_ownership_decided
    assert cp._ownership_decided() is True

    def boom():
        raise RuntimeError("redis down")

    env.backend.telephony_ownership_decided = boom
    assert cp._ownership_decided() is True
    env.backend.telephony_ownership_decided = lambda: "yes"           # not a boolean
    assert cp._ownership_decided() is True
    env.backend.telephony_ownership_decided = lambda: None
    assert cp._ownership_decided() is True
    await _run(env, [(1010.0, None), (1500.0, None)])
    assert env.calls == [1] and spool.has_audio("live-1") and not folder.exists()


# ---- (e) the real backends

class _Registry:
    pod_id = "pod-x"

    def __init__(self, answer):
        self.answer = answer

    async def try_acquire_ari_ownership_strict(self, ttl):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def try_acquire_ari_ownership(self, ttl):
        return await self.try_acquire_ari_ownership_strict(ttl)


async def _acquire(backend, strict):
    if strict:
        return await backend.acquire_telephony_ownership_strict()
    return await backend.acquire_telephony_ownership()


@pytest.mark.parametrize("answer", [True, False])
@pytest.mark.parametrize("strict", [True, False])
async def test_e_real_redis_backend_is_decided_after_either_acquire(answer, strict):
    backend = RedisBackedStateBackend(_Registry(answer))
    assert backend.telephony_ownership_decided() is False
    assert backend.is_telephony_owner() is True               # the default, not an answer
    assert await _acquire(backend, strict) is answer
    assert backend.telephony_ownership_decided() is True
    assert backend.is_telephony_owner() is answer


@pytest.mark.parametrize("strict", [True, False])
async def test_e_real_redis_backend_stays_undecided_when_the_acquire_raises(strict):
    backend = RedisBackedStateBackend(_Registry(RuntimeError("redis down")))
    with pytest.raises(RuntimeError):
        await _acquire(backend, strict)
    assert backend.telephony_ownership_decided() is False


def test_e_real_in_memory_backend_is_decided():
    assert LocalOnlyStateBackend().telephony_ownership_decided() is True


async def test_e_real_redis_backend_through_the_gate(env, monkeypatch):
    backend = RedisBackedStateBackend(_Registry(False))
    monkeypatch.setattr(lifecycle, "_state", lambda: backend)
    assert cp.loop_may_run("recording_checkpoint_loop") is False        # undecided: waits
    await backend.acquire_telephony_ownership_strict()
    assert cp.loop_may_run("recording_checkpoint_loop") is False        # decided False: idle


# ---- (f) the pending pass

async def test_f_the_pending_pass_waits_while_undecided_and_runs_once_decided_as_owner(env):
    spool.write_entry(_entry("pend-1"), _wav())
    assert await tr._spool_pass() == 0 and env.processed == []
    env.backend.decided = True                                  # owner (flag True)
    assert await tr._spool_pass() == 1 and env.processed == ["pend-1"]


async def test_f_the_pending_pass_stays_idle_when_decided_not_owner(env):
    spool.write_entry(_entry("pend-1"), _wav())
    env.backend.owner, env.backend.decided = False, True
    assert await tr._spool_pass() == 0 and env.processed == []


# ---- (g) the lines

async def test_g_lines_one_per_change_with_the_new_names(env, caplog):
    caplog.set_level(logging.DEBUG)
    for _ in range(3):
        assert cp.loop_may_run("recording_checkpoint_loop") is False   # undecided
    env.backend.owner, env.backend.decided = False, True
    for _ in range(3):
        assert cp.loop_may_run("recording_checkpoint_loop") is False
        assert cp.loop_may_run("recording_spool_loop") is False
    env.backend.owner = True
    for _ in range(3):
        assert cp.loop_may_run("recording_checkpoint_loop") is True
        assert cp.loop_may_run("recording_spool_loop") is True
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert info.count("recording_orphan_recovery_waiting reason=not_call_owner") == 1
    assert info.count("recording_orphan_recovery_allowed") == 1
    assert info.count("recording_loops_idle reason=not_call_owner loop=recording_spool_loop") == 1
    assert info.count("recording_loops_active loop=recording_spool_loop") == 1
    assert not [m for m in info if "loop=recording_checkpoint_loop" in m]
    assert not [m for m in info if "undecided" in m]          # undecided is silent at INFO
