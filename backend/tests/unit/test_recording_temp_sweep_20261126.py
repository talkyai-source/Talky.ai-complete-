"""Stale ``<name>.<12 hex>.tmp`` files left by a crash are removed, and only those."""
import asyncio
import logging
import os
import time
import unittest.mock as mock
from types import SimpleNamespace

import pytest

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_service as rs
from app.domain.services import recording_spool as rsp
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import recording as tr

HOUR = 3600.0


def _file(path, age_s, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def test_stale_matching_tmp_is_removed_fresh_one_is_kept(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    old = _file(tmp_path / "acct" / "call.mp3.0123456789ab.tmp", 2 * HOUR)
    fresh = _file(tmp_path / "acct" / "call2.mp3.0123456789ab.tmp", 60)
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 1
    assert not old.exists() and fresh.exists()
    assert [r.getMessage() for r in caplog.records] == ["recording_temp_sweep removed=1"]


def test_nothing_removed_means_no_log_line(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    _file(tmp_path / "a.mp3.0123456789ab.tmp", 60)
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 0
    assert [r for r in caplog.records if "recording_temp_sweep" in r.getMessage()] == []


@pytest.mark.parametrize("name", [
    "notes.tmp", "call.wav", "x.0123456789ab.tmp.bak", "x.0123456789AB.tmp",
    "x.0123456789a.tmp", "x.0123456789abc.tmp", ".0123456789ab.tmp",
])
def test_non_matching_names_are_kept(tmp_path, name):
    f = _file(tmp_path / "acct" / name, 5 * HOUR)
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 0
    assert f.exists()


def test_a_directory_with_a_matching_name_is_kept(tmp_path):
    d = tmp_path / "acct" / "dir.0123456789ab.tmp"
    d.mkdir(parents=True)
    t = time.time() - 5 * HOUR
    os.utime(d, (t, t))
    inner = _file(d / "inner.txt", 5 * HOUR)
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 0
    assert d.is_dir() and inner.exists()


def test_nothing_outside_the_root_is_touched(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = _file(tmp_path / "outside" / "a.mp3.0123456789ab.tmp", 5 * HOUR)
    inside = _file(root / "a.mp3.0123456789ab.tmp", 5 * HOUR)
    assert rs.sweep_stale_temp_recordings(str(root)) == 1
    assert outside.exists() and not inside.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_posix_symlinks_are_not_followed(tmp_path):
    # Linux: a symlinked directory is not descended into and a symlinked file with
    # a matching name is not unlinked (its target stays).
    root = tmp_path / "root"
    root.mkdir()
    outside_dir = tmp_path / "outside"
    target = _file(outside_dir / "a.mp3.0123456789ab.tmp", 5 * HOUR)
    (root / "linkdir").symlink_to(outside_dir, target_is_directory=True)
    file_target = _file(tmp_path / "elsewhere.dat", 5 * HOUR)
    link = root / "b.mp3.0123456789ab.tmp"
    link.symlink_to(file_target)
    assert rs.sweep_stale_temp_recordings(str(root)) == 0
    assert target.exists() and file_target.exists() and os.path.islink(link)


def test_at_most_200_per_run(tmp_path):
    for i in range(230):
        _file(tmp_path / "a" / f"c{i}.mp3.{i:012x}.tmp", 2 * HOUR)
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 200
    assert len(list((tmp_path / "a").iterdir())) == 30
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 30


def test_no_local_root_does_nothing(tmp_path, monkeypatch):
    assert rs.sweep_stale_temp_recordings(str(tmp_path / "missing")) == 0
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path / "also-missing"))
    assert rs.sweep_stale_temp_recordings() == 0


def test_default_root_is_the_configured_local_recordings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    old = _file(tmp_path / "a.mp3.0123456789ab.tmp", 2 * HOUR)
    assert rs.sweep_stale_temp_recordings() == 1 and not old.exists()


def test_an_error_on_one_file_does_not_stop_the_run_or_raise(tmp_path, monkeypatch):
    a = _file(tmp_path / "a.mp3.000000000001.tmp", 2 * HOUR)
    b = _file(tmp_path / "b.mp3.000000000002.tmp", 2 * HOUR)
    real = os.unlink

    def flaky(p, *args, **kw):
        if str(p).endswith("a.mp3.000000000001.tmp"):
            raise PermissionError("busy")
        return real(p, *args, **kw)

    monkeypatch.setattr(rs.os, "unlink", flaky)
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 1
    assert a.exists() and not b.exists()


def test_an_error_listing_never_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("listing failed")

    monkeypatch.setattr(rs.os, "walk", boom)
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 0


def test_the_pattern_matches_what_write_wav_file_creates(tmp_path):
    """The sweep's pattern lives beside the code that makes the names."""
    seen = []
    real = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        return real(src, dst)

    with mock.patch.object(rs.os, "replace", spy):
        rs._write_wav_file(str(tmp_path), str(tmp_path / "acct" / "c.wav"), b"RIFF")
    assert len(seen) == 1 and rs._TEMP_NAME_RE.match(seen[0])


# ---- the loop

@pytest.fixture
def loop_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    backend = SimpleNamespace(owner=True)
    backend.is_telephony_owner = lambda: backend.owner
    monkeypatch.setattr(lifecycle, "_state", lambda: backend)
    cp._LOOP_IDLE.clear()
    monkeypatch.setattr(tr, "_LOOP_FIRST_DELAY_S", 0.01)
    monkeypatch.setattr(tr, "_env_float", lambda n, d, minimum=0.0: 0.02 if "INTERVAL" in n else d)
    monkeypatch.setattr(tr, "_TEMP_SWEEP_LAST", None)
    clock = [5000.0]
    monkeypatch.setattr(tr, "_monotonic", lambda: clock[0])
    runs = []
    monkeypatch.setattr(rs, "sweep_stale_temp_recordings", lambda *a, **k: runs.append(clock[0]) or 0)
    monkeypatch.setattr(rsp, "sweep_leftovers", lambda *a: 0)
    yield SimpleNamespace(backend=backend, clock=clock, runs=runs)
    cp._LOOP_IDLE.clear()


async def _loop_for(seconds):
    stop = asyncio.Event()
    task = asyncio.create_task(tr.recording_spool_loop(stop))
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, 5)


async def test_the_loop_sweeps_at_most_every_ten_minutes(loop_env):
    await _loop_for(0.2)                               # many rounds, clock frozen
    assert loop_env.runs == [5000.0]
    loop_env.clock[0] = 5000.0 + 599
    await _loop_for(0.15)
    assert loop_env.runs == [5000.0]
    loop_env.clock[0] = 5000.0 + 601
    await _loop_for(0.15)
    assert loop_env.runs == [5000.0, 5601.0]


async def test_a_non_owner_does_not_sweep(loop_env):
    loop_env.backend.owner = False
    await _loop_for(0.2)
    assert loop_env.runs == []


async def test_an_error_in_the_sweep_does_not_reach_the_loop(loop_env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(rs, "sweep_stale_temp_recordings", boom)
    await _loop_for(0.2)                               # the loop still starts and stops cleanly


# ---- the pending folder and dot-directories are never swept here

def test_the_default_pending_folder_is_left_to_its_own_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.delenv("RECORDING_SPOOL_DIR", raising=False)
    direct = _file(tmp_path / ".pending" / "k1.wav.0123456789ab.tmp", 5 * HOUR)
    live = _file(tmp_path / ".pending" / "live" / "k1" / "caller.pcm.0123456789ab.tmp", 5 * HOUR)
    account = _file(tmp_path / "t1" / "c1" / "call.mp3.0123456789ab.tmp", 5 * HOUR)
    assert rs.sweep_stale_temp_recordings() == 1
    assert direct.exists() and live.exists() and not account.exists()


def test_a_configured_pending_folder_without_a_dot_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "waiting"))
    waiting = _file(tmp_path / "waiting" / "k1.wav.0123456789ab.tmp", 5 * HOUR)
    nested = _file(tmp_path / "waiting" / "live" / "k1" / "a.pcm.0123456789ab.tmp", 5 * HOUR)
    account = _file(tmp_path / "t1" / "c1" / "call.mp3.0123456789ab.tmp", 5 * HOUR)
    assert rs.sweep_stale_temp_recordings() == 1
    assert waiting.exists() and nested.exists() and not account.exists()


def test_a_dot_directory_is_not_entered(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.delenv("RECORDING_SPOOL_DIR", raising=False)
    hidden = _file(tmp_path / ".cache" / "a.mp3.0123456789ab.tmp", 5 * HOUR)
    assert rs.sweep_stale_temp_recordings() == 0
    assert hidden.exists()


def test_a_blank_recordings_setting_never_means_the_working_directory(tmp_path, monkeypatch):
    """LOCAL_RECORDINGS_DIR present but EMPTY must not turn the whole working
    directory (the application's own folder) into the sweep's root."""
    victim = _file(tmp_path / "venv" / "lib" / "pkg.0123456789ab.tmp", 7200)
    other = _file(tmp_path / "t1" / "c1" / "a.wav.0123456789ab.tmp", 7200)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "elsewhere-pending"))
    for blank in ("", "   "):
        monkeypatch.setenv("LOCAL_RECORDINGS_DIR", blank)
        assert rs.sweep_stale_temp_recordings() == 0
        assert rs.sweep_stale_temp_recordings(blank) == 0
    assert victim.exists() and other.exists()


def test_a_root_that_is_the_working_directory_is_never_swept(tmp_path, monkeypatch):
    victim = _file(tmp_path / "app" / "x.0123456789ab.tmp", 7200)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "elsewhere-pending"))
    assert rs.sweep_stale_temp_recordings(str(tmp_path)) == 0
    assert rs.sweep_stale_temp_recordings(".") == 0
    assert victim.exists()
