"""A checkpoint pass must leave a call whose teardown is still running alone.

lifecycle._on_call_ended pops the session from the registry about two seconds
before _save_call_recording calls checkpoint_done. The pass used to read
"left the registry, not done" as "ended without the normal path" and wrote the
OLDER checkpoint into the pending folder at the same moment the teardown wrote
the full capture under the same key (25 Nov 2026: recording_spool_write_failed
FileNotFoundError; the pending safety copy could silently hold the shorter audio).
"""
import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_service as rs
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import recording as tr

TENANT = "20000000-0000-4000-8000-000000000004"
SID = "sess-teardown-1"
PBX = "talky-out-test"
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

    async def direct_save_failed(entry, *, wav_bytes=None, voice_session=None):
        return await tr._mark_retry(entry, "db_down")  # the pending copy is the only copy left

    monkeypatch.setattr(tr, "process_spool_entry", direct_save_failed)
    clock = [1000.0]
    monkeypatch.setattr(cp, "_now", lambda: clock[0], raising=False)
    cp._STATE.clear()
    cp._DONE.clear()
    tr._PROCESSING.clear()
    yield SimpleNamespace(live=live, spool=tmp_path / "spool", clock=clock)
    cp._STATE.clear()
    cp._DONE.clear()


async def _call_that_ends(env):
    gw = Gw()
    vs = SimpleNamespace(call_id=SID, media_gateway=gw, _dialer_tenant_id=TENANT,
                         config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
                         call_session=None, _recording_policy_decided=True)
    env.live[PBX] = vs
    gw.caller += [bytes([1, 0]) * 800 for _ in range(40)]      # 4 s at the last tick
    gw.agent += [(0, b"\x10\x00" * 800)]
    assert await cp.checkpoint_pass() == 1
    gw.caller += [bytes([2, 0]) * 800 for _ in range(10)]      # 1 s more before the hang-up
    gw.agent += [(36000, b"\x20\x00" * 800)]
    full = rs.mix_stereo_recording(caller_chunks=list(gw.caller), agent_chunks=list(gw.agent), sample_rate=RATE)
    env.live.clear()                                           # lifecycle.py: pop_voice_session
    return vs, full


def _recovered_lines(caplog):
    return [r for r in caplog.records if "recording_checkpoint_recovered" in r.getMessage()]


# ------------------------------------------------- the inspector's two tests

async def test_a_pass_during_teardown_leaves_the_copy_to_the_teardown(env, caplog):
    vs, full = await _call_that_ends(env)
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()                                 # the 10 s tick lands in the window
    assert not spool.has_audio(SID), "the pass wrote the older copy to pending during teardown"
    assert "recording_checkpoint_recovered" not in caplog.text
    assert await tr._save_call_recording_with_budget(vs, PBX) == "captured"
    assert spool.read_wav(SID) == full


async def test_a_recovery_finishing_after_the_teardown_spooled_never_replaces_it(env, monkeypatch):
    vs, full = await _call_that_ends(env)
    teardown_spooled = threading.Event()
    real_mix, real_write = rs.mix_stereo_recording, spool.write_entry

    def mix(*a, **kw):
        import sys
        if sys._getframe(1).f_code.co_name == "_recover_sync":
            teardown_spooled.wait(10)                          # the recovery's mix ends last
        return real_mix(*a, **kw)

    def write(entry, wav):
        real_write(entry, wav)
        if len(wav) == len(full):
            teardown_spooled.set()

    monkeypatch.setattr(rs, "mix_stereo_recording", mix)
    monkeypatch.setattr(spool, "write_entry", write)
    cpass = asyncio.create_task(cp.checkpoint_pass())
    await asyncio.sleep(0.05)                                  # the pass has read the folder
    assert await tr._save_call_recording_with_budget(vs, PBX) == "captured"
    await cpass
    assert spool.read_wav(SID) == full, "the 4 s checkpoint replaced the 5 s capture in pending"
    assert json.loads((env.spool / f"{SID}.json").read_text())["duration_seconds"] == pytest.approx(5.0)


async def test_a_recovery_that_misuses_the_pass_still_cannot_replace_the_teardown_copy(env, monkeypatch):
    """Fix B on its own: a recovery started directly (no grace) whose mix ends
    after the teardown has spooled must find the entry and leave it alone."""
    vs, full = await _call_that_ends(env)
    teardown_spooled = threading.Event()
    real_mix, real_write = rs.mix_stereo_recording, spool.write_entry

    def mix(*a, **kw):
        import sys
        if sys._getframe(1).f_code.co_name == "_recover_sync":
            teardown_spooled.wait(10)
        return real_mix(*a, **kw)

    def write(entry, wav):
        real_write(entry, wav)
        if len(wav) == len(full):
            teardown_spooled.set()

    monkeypatch.setattr(rs, "mix_stereo_recording", mix)
    monkeypatch.setattr(spool, "write_entry", write)
    recovery = asyncio.create_task(cp._recover_key(SID))
    await asyncio.sleep(0.05)
    assert await tr._save_call_recording_with_budget(vs, PBX) == "captured"
    assert await recovery is False
    assert spool.read_wav(SID) == full
    assert json.loads((env.spool / f"{SID}.json").read_text())["duration_seconds"] == pytest.approx(5.0)


# ------------------------------------------------------------ the grace rule

async def test_a_gone_session_is_recovered_only_after_the_grace_and_only_once(env, caplog):
    await _call_that_ends(env)
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()                                 # first seen gone: noted, skipped
    assert not spool.has_audio(SID)
    env.clock[0] += cp._VANISHED_GRACE_S - 1                   # 119 s
    await cp.checkpoint_pass()
    assert not spool.has_audio(SID)
    assert _recovered_lines(caplog) == []
    env.clock[0] += 2                                          # 121 s
    await cp.checkpoint_pass()
    assert spool.has_audio(SID)
    assert json.loads((env.spool / f"{SID}.json").read_text())["duration_seconds"] == pytest.approx(4.0)
    [line] = _recovered_lines(caplog)
    assert line.levelno == logging.WARNING and "source=pass" in line.getMessage()
    env.clock[0] += 30
    await cp.checkpoint_pass()
    assert len(_recovered_lines(caplog)) == 1
    assert cp._STATE == {}


def test_the_grace_is_120_seconds():
    assert cp._VANISHED_GRACE_S == 120


async def test_checkpoint_done_inside_the_grace_means_no_recovery_and_no_state(env, caplog):
    await _call_that_ends(env)
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()                                 # noted
    cp.checkpoint_done(SID)
    assert cp._STATE == {}
    for _ in range(100):
        if not (env.spool / "live" / SID).exists():
            break
        await asyncio.sleep(0.02)
    env.clock[0] += 10_000
    await cp.checkpoint_pass()
    assert not spool.has_audio(SID)
    assert _recovered_lines(caplog) == []
    assert cp._STATE == {}


async def test_a_session_that_comes_back_is_not_recovered_and_vanished_at_is_cleared(env, caplog):
    """The SAME session is in the registry again: its clock is cleared (not id re-use: see the id_reuse test)."""
    vs, _full = await _call_that_ends(env)
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()
    [st] = cp._STATE.values()
    assert st.vanished_at == 1000.0
    env.live[PBX] = vs                                         # the same session is back
    env.clock[0] += 500
    await cp.checkpoint_pass()
    [st] = cp._STATE.values()
    assert st.vanished_at is None
    assert _recovered_lines(caplog) == [] and not spool.has_audio(SID)
    env.live.clear()                                           # gone again: the clock starts afresh
    await cp.checkpoint_pass()
    env.clock[0] += cp._VANISHED_GRACE_S - 1
    await cp.checkpoint_pass()
    assert not spool.has_audio(SID)


async def test_startup_recovery_is_unchanged_and_says_source_startup(env, caplog):
    d = env.spool / "live" / "old-call"
    d.mkdir(parents=True)
    (d / "caller.pcm").write_bytes(b"\x01\x00" * 8000)
    (d / "agent.bin").write_bytes(b"")
    entry = tr._build_spool_entry(
        SimpleNamespace(call_id="old-call", media_gateway=Gw(), _dialer_tenant_id=TENANT,
                        config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
                        call_session=None),
        "pbx-old", sample_rate=RATE, duration=0.0, awaiting_transfer=False, transfer_recording_name=None,
    )
    entry.key = "old-call"
    (d / "meta.json").write_text(json.dumps(spool.asdict(entry)))
    caplog.set_level(logging.INFO)
    assert await cp.recover_orphans() == 1
    [line] = _recovered_lines(caplog)
    assert "source=startup" in line.getMessage() and line.levelno == logging.INFO


# ------------------------------------------------------------ spool hardening

def _entry(key="k-1"):
    return spool.SpoolEntry(
        key=key, pbx_call_id="pbx-1", session_call_id=key, tenant_id=None, campaign_id=None,
        phone_hint=None, sample_rate=RATE, duration_seconds=1.0, disclosure_state="transmitted",
        callee_country="PK", callee_country_known=True, inbound=False, strict_notice=False,
        awaiting=None, transfer_recording_name=None, transfer_part=None, created_at=spool.utc_now_iso(),
    )


def test_two_writers_of_one_key_both_succeed_and_the_file_is_one_writers_whole(env, monkeypatch):
    real_fsync = spool.os.fsync
    first = []
    second_started = threading.Event()

    def slow_fsync(fd):
        if not first:                                          # only to force the overlap
            first.append(1)
            second_started.wait(2)
            time.sleep(0.15)
        return real_fsync(fd)

    monkeypatch.setattr(spool.os, "fsync", slow_fsync)
    results = {}

    def writer(name, byte, seconds):
        entry = _entry()
        entry.duration_seconds = seconds
        try:
            if name == "b":
                second_started.set()
            spool.write_entry(entry, bytes([byte]) * 200_000)
            results[name] = "ok"
        except Exception as exc:
            results[name] = type(exc).__name__

    threads = [threading.Thread(target=writer, args=("a", 0xAA, 1.0)),
               threading.Thread(target=writer, args=("b", 0xBB, 2.0))]
    threads[0].start()
    time.sleep(0.02)
    threads[1].start()
    for t in threads:
        t.join(10)
    assert results == {"a": "ok", "b": "ok"}, "a writer was refused: that WAS the production fault"
    wav = spool.read_wav("k-1")
    whole = {bytes([0xAA]) * 200_000: 1.0, bytes([0xBB]) * 200_000: 2.0}
    assert wav in whole
    assert spool.read_entry("k-1").duration_seconds == whole[wav]
    assert [p.name for p in env.spool.iterdir() if p.name.endswith(".tmp")] == []


def test_a_failed_rename_removes_its_temp_file(env, monkeypatch):
    def boom(src, dst):
        raise OSError("rename refused")

    monkeypatch.setattr(spool.os, "replace", boom)
    with pytest.raises(OSError):
        spool.write_entry(_entry(), b"RIFFdata")
    monkeypatch.undo()
    assert [p.name for p in env.spool.iterdir() if ".tmp" in p.name] == []


def test_write_entry_names_the_step_that_failed(env, monkeypatch):
    real_replace = spool.os.replace

    def fail_json(src, dst):
        if str(dst).endswith(".json"):
            raise OSError("json refused")
        return real_replace(src, dst)

    monkeypatch.setattr(spool.os, "replace", fail_json)
    with pytest.raises(OSError) as info:
        spool.write_entry(_entry(), b"RIFFdata")
    assert getattr(info.value, "spool_step", None) == "json"


def test_write_entry_if_absent_writes_when_nothing_is_pending_and_refuses_otherwise(env):
    assert spool.write_entry_if_absent(_entry(), b"RIFFfirst") is True
    assert spool.read_wav("k-1") == b"RIFFfirst"
    assert spool.write_entry_if_absent(_entry(), b"RIFFsecond") is False
    assert spool.read_wav("k-1") == b"RIFFfirst"


def _wav_of(seconds):
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x01\x00\x02\x00" * int(RATE * seconds))
    return buf.getvalue()


def test_a_wav_with_a_good_json_is_never_replaced_and_nothing_is_written(env):
    full = _wav_of(5.0)
    spool.write_entry(_entry(), full)
    json_before = (env.spool / "k-1.json").read_bytes()
    assert spool.write_entry_if_absent(_entry(), _wav_of(4.0)) is False
    assert spool.read_wav("k-1") == full
    assert (env.spool / "k-1.json").read_bytes() == json_before


@pytest.mark.parametrize("damage", ["missing", "unreadable"])
def test_a_wav_without_a_usable_json_keeps_its_audio_and_gets_a_json_describing_it(env, damage):
    import os

    full = _wav_of(5.0)
    spool.write_entry(_entry(), full)
    if damage == "missing":
        os.unlink(env.spool / "k-1.json")
    else:
        (env.spool / "k-1.json").write_text("{not json")
    shorter = _entry()
    shorter.duration_seconds = 4.0
    assert spool.write_entry_if_absent(shorter, _wav_of(4.0)) is False
    assert spool.read_wav("k-1") == full                        # audio untouched
    entry = spool.read_entry("k-1")
    assert entry is not None and [e.key for e in spool.list_entries()] == ["k-1"]
    assert entry.duration_seconds == pytest.approx(5.0)         # from the EXISTING wav's header
    assert entry.sample_rate == RATE


# ------------------------------------------------ id re-use (inbound gate)

async def test_a_new_inbound_session_with_a_reused_id_never_deletes_the_old_calls_last_copy(env, caplog):
    await _call_that_ends(env)                                  # call A: checkpoint on disk, then vanished
    await cp.checkpoint_pass()                                  # noted vanished (state kept)
    [(key, st_a)] = list(cp._STATE.items())
    assert (env.spool / "live" / SID / "caller.pcm").exists()
    gw_b = Gw()
    gw_b.caller += [b"\x05\x00" * 800 for _ in range(3)]
    vs_b = SimpleNamespace(call_id="sess-new-inbound", media_gateway=gw_b, _dialer_tenant_id=TENANT,
                           config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
                           call_session=None, _inbound_admission={"x": 1})   # gate closed
    cp._STATE.pop(key)
    cp._STATE[id(vs_b)] = st_a                                  # python re-used A's id() for B
    env.live["pbx-new"] = vs_b
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()
    assert spool.has_audio(SID), "the abandoned call's last copy was deleted"
    [line] = _recovered_lines(caplog)
    assert "source=id_reuse" in line.getMessage() and line.levelno == logging.WARNING
    assert not (env.spool / "live" / "sess-new-inbound").exists()
    assert cp._STATE == {}


# ------------------------------------------------- created_at after a recovery

async def test_a_pass_recovery_is_stamped_now_and_the_pending_loop_waits_for_it(env, monkeypatch):
    await _call_that_ends(env)
    meta = env.spool / "live" / SID / "meta.json"
    data = json.loads(meta.read_text())
    data["created_at"] = "2020-01-01T00:00:00+00:00"
    meta.write_text(json.dumps(data))
    monkeypatch.setattr(cp, "utc_now_iso", lambda: "2026-11-25T12:00:00+00:00", raising=False)
    await cp.checkpoint_pass()
    env.clock[0] += cp._VANISHED_GRACE_S + 1
    await cp.checkpoint_pass()
    assert spool.read_entry(SID).created_at == "2026-11-25T12:00:00+00:00"
    # the pending loop: an entry with no attempts waits tr._ORPHAN_AGE_S from created_at
    processed = []

    async def seen(entry, **kw):
        processed.append(entry.key)
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", seen)
    monkeypatch.setattr(tr, "_age_seconds", lambda iso, now: 30.0)       # 30 s after the stamp
    await tr._spool_pass()
    assert processed == []
    monkeypatch.setattr(tr, "_age_seconds", lambda iso, now: tr._ORPHAN_AGE_S + 1)
    await tr._spool_pass()
    assert processed == [SID]


async def test_a_startup_recovery_keeps_the_original_created_at(env):
    d = env.spool / "live" / "old-call"
    d.mkdir(parents=True)
    (d / "caller.pcm").write_bytes(b"\x01\x00" * 8000)
    (d / "agent.bin").write_bytes(b"")
    entry = _entry("old-call")
    entry.created_at = "2020-01-01T00:00:00+00:00"
    (d / "meta.json").write_text(json.dumps(spool.asdict(entry)))
    assert await cp.recover_orphans() == 1
    assert spool.read_entry("old-call").created_at == "2020-01-01T00:00:00+00:00"


# --------------------------------------------- clean-ups: the app's own loop

def _age(path, seconds):
    import os

    past = time.time() - seconds
    os.utime(path, (past, past))


def test_the_sweep_removes_old_stray_json_and_old_tmp_and_keeps_fresh_ones(env):
    import os

    spool.write_entry(_entry("fresh"), b"x")
    spool.write_entry(_entry("old"), b"x")
    spool.write_entry(_entry("good"), b"x")
    for key in ("fresh", "old"):
        os.unlink(env.spool / f"{key}.wav")
    _age(env.spool / "old.json", spool._ORPHAN_WAV_AGE_S + 60)
    (env.spool / "a.wav.abc123.tmp").write_bytes(b"audio")
    (env.spool / "b.wav.def456.tmp").write_bytes(b"audio")
    _age(env.spool / "a.wav.abc123.tmp", 3600 + 60)
    assert spool.sweep_leftovers() == 2
    assert not (env.spool / "old.json").exists() and not (env.spool / "a.wav.abc123.tmp").exists()
    assert (env.spool / "fresh.json").exists() and (env.spool / "b.wav.def456.tmp").exists()
    assert (env.spool / "good.json").exists() and (env.spool / "good.wav").exists()


def test_listing_deletes_nothing(env):
    import os

    spool.write_entry(_entry("old"), b"x")
    os.unlink(env.spool / "old.wav")
    (env.spool / "a.wav.abc123.tmp").write_bytes(b"audio")
    _age(env.spool / "old.json", 10_000)
    _age(env.spool / "a.wav.abc123.tmp", 10_000)
    assert spool.list_entries() == [] and spool.pending_summary()["count"] == 0
    assert sorted(p.name for p in env.spool.iterdir()) == ["a.wav.abc123.tmp", "old.json"]


async def test_the_application_spool_loop_runs_the_sweep(env, monkeypatch):
    calls = []
    monkeypatch.setattr(spool, "sweep_leftovers", lambda *a: calls.append(1) or 0, raising=False)
    monkeypatch.setattr(tr, "_LOOP_FIRST_DELAY_S", 0.01)

    async def no_pass():
        return 0

    monkeypatch.setattr(tr, "_spool_pass", no_pass)
    stop = asyncio.Event()
    task = asyncio.create_task(tr.recording_spool_loop(stop))
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await task
    assert calls


# ------------------------------------------------------- locks, no deadlock

def test_a_write_followed_by_update_and_remove_of_one_key_does_not_deadlock(env):
    done = []

    def work():
        entry = _entry()
        spool.write_entry(entry, b"RIFFdata")
        entry.attempts = 2
        spool.update_entry(entry)
        assert spool.write_entry_if_absent(entry, b"other") is False
        spool.remove_entry("k-1")
        done.append(1)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(5)
    assert done == [1], "a spool function took the (non-re-entrant) lock twice"
    assert spool.read_entry("k-1") is None


# ------------------------------- empty wav, hand-over entry, pending_has_audio

def _put_wav_only(env, data):
    env.spool.mkdir(parents=True, exist_ok=True)
    (env.spool / "k-1.wav").write_bytes(data)


@pytest.mark.parametrize("existing", [b"", _wav_of(0.0)], ids=["0-byte", "44-byte-header-only"])
def test_an_empty_wav_without_a_json_counts_as_absent_and_is_written_over(env, existing):
    assert len(existing) in (0, 44)
    _put_wav_only(env, existing)
    audio = _wav_of(4.0)
    assert spool.write_entry_if_absent(_entry(), audio) is True
    assert spool.read_wav("k-1") == audio
    assert spool.read_entry("k-1") is not None


def test_a_good_json_with_an_empty_wav_is_never_touched(env):
    awaiting = _entry()
    awaiting.awaiting = "transfer_part"
    spool.write_entry(awaiting, b"")
    before = ((env.spool / "k-1.wav").read_bytes(), (env.spool / "k-1.json").read_bytes())
    assert spool.write_entry_if_absent(_entry(), _wav_of(4.0)) is False
    assert ((env.spool / "k-1.wav").read_bytes(), (env.spool / "k-1.json").read_bytes()) == before


def test_pending_has_audio_is_size_only(env):
    assert spool.pending_has_audio("k-1") is False              # missing
    for size, expected in ((0, False), (44, False), (45, True)):
        _put_wav_only(env, b"x" * size)
        assert spool.pending_has_audio("k-1") is expected
    assert spool.pending_has_audio("../x") is False


async def _awaiting_entry_and_a_checkpoint(env):
    await _call_that_ends(env)                                  # live/<SID> folder holds 4 s of audio
    awaiting = _entry(SID)
    awaiting.awaiting = "transfer_part"
    spool.write_entry(awaiting, b"")                            # lifecycle.py: the hand-over's empty entry


async def test_a_checkpoint_is_kept_when_the_pending_entry_has_no_audio(env, caplog):
    await _awaiting_entry_and_a_checkpoint(env)
    before = ((env.spool / f"{SID}.wav").read_bytes(), (env.spool / f"{SID}.json").read_bytes())
    assert cp._recover_sync(SID, "pass")[0] == "kept"
    assert (env.spool / "live" / SID).exists()
    caplog.set_level(logging.INFO)
    assert await cp._recover_key(SID, "pass") is False
    assert [r.getMessage() for r in caplog.records if "recording_checkpoint_kept" in r.getMessage()] == [
        f"recording_checkpoint_kept call={SID[:12]} source=pass reason=pending_entry_has_no_audio"
    ]
    assert [r for r in caplog.records if "recording_checkpoint_kept" in r.getMessage()][0].levelno == logging.WARNING
    assert _recovered_lines(caplog) == []
    assert ((env.spool / f"{SID}.wav").read_bytes(), (env.spool / f"{SID}.json").read_bytes()) == before


async def test_a_kept_checkpoint_is_not_retried_on_every_pass(env, caplog):
    await _awaiting_entry_and_a_checkpoint(env)
    await cp.checkpoint_pass()
    env.clock[0] += cp._VANISHED_GRACE_S + 1
    caplog.set_level(logging.INFO)
    await cp.checkpoint_pass()                                  # the one attempt: kept
    assert cp._STATE == {}
    for _ in range(3):
        env.clock[0] += 10
        await cp.checkpoint_pass()
    assert len([r for r in caplog.records if "recording_checkpoint_kept" in r.getMessage()]) == 1
    assert (env.spool / "live" / SID).exists()


async def test_a_checkpoint_is_removed_when_the_pending_entry_really_has_audio(env):
    await _call_that_ends(env)
    spool.write_entry(_entry(SID), _wav_of(5.0))
    assert cp._recover_sync(SID, "pass")[0] == "already_pending"
    assert not (env.spool / "live" / SID).exists()
