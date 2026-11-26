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
    backend = SimpleNamespace(owner=False, iter_voice_session_items=lambda: [])
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

async def test_a_non_owner_checkpoint_loop_leaves_a_foreign_folder_alone(env, monkeypatch):
    monkeypatch.setattr(cp, "_interval_s", lambda: 0.02)
    folder = _foreign_folder(env)
    calls = []
    real = cp.recover_orphans

    async def counted():
        calls.append(1)
        return await real()

    monkeypatch.setattr(cp, "recover_orphans", counted)
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert calls == [] and folder.exists()
    assert not spool.has_audio("live-1")


async def test_ownership_arriving_later_recovers_the_orphan_once(env, monkeypatch, caplog):
    monkeypatch.setattr(cp, "_interval_s", lambda: 0.02)
    folder = _foreign_folder(env)
    calls = []
    real = cp.recover_orphans

    async def counted():
        calls.append(1)
        return await real()

    monkeypatch.setattr(cp, "recover_orphans", counted)
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.15)
    assert calls == [] and folder.exists() and not spool.has_audio("live-1")
    env.backend.owner = True
    await asyncio.sleep(0.3)                                   # many owner passes
    stop.set()
    await asyncio.wait_for(task, 5)
    assert calls == [1], "recover_orphans must run exactly once"
    assert spool.has_audio("live-1") and not folder.exists()
    text = [r.getMessage() for r in caplog.records]
    assert text.count("recording_loops_idle reason=not_call_owner loop=recording_checkpoint_loop") == 1
    assert text.count("recording_loops_active loop=recording_checkpoint_loop") == 1


async def test_an_owner_at_start_up_recovers_immediately_as_today(env, monkeypatch):
    monkeypatch.setattr(cp, "_interval_s", lambda: 0.02)
    env.backend.owner = True
    folder = _foreign_folder(env)
    stop = asyncio.Event()
    task = asyncio.create_task(cp.recording_checkpoint_loop(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert spool.has_audio("live-1") and not folder.exists()


# ---- the pending loop

async def test_a_non_owner_spool_pass_processes_nothing_and_deletes_nothing(env):
    spool.write_entry(_entry("pend-1"), _wav(1.0))
    assert await tr._spool_pass() == 0
    assert env.processed == []
    assert spool.has_audio("pend-1")


async def test_the_spool_pass_runs_once_the_process_is_the_owner(env):
    spool.write_entry(_entry("pend-1"), _wav(1.0))
    assert await tr._spool_pass() == 0
    env.backend.owner = True
    assert await tr._spool_pass() == 1
    assert env.processed == ["pend-1"]


async def test_the_spool_loop_skips_the_whole_round_for_a_non_owner(env, monkeypatch, caplog):
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
    assert env.backend.owner is False
    assert await tr._save_call_recording_with_budget(vs, "pbx-own") == "captured"
    assert env.processed == ["own-call"]
