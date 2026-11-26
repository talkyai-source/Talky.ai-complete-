"""A stored recording is never silently replaced by a shorter one.

Item 2: a failing "already saved" check must not let the save go ahead (a
duplicate shorter copy would overwrite the stored file in place while the row
keeps the old duration). Item 3: the local file write is atomic and durable
(temp file in the same directory, fsync, replace, directory fsync).

Real temp dirs and real files; only the database pool is a fake.
"""
import asyncio
import io
import logging
import os
import sys
import uuid
import wave
from contextlib import asynccontextmanager

import pytest

from app.domain.services import recording_service as rs


# ------------------------------------------------------------------ fakes

class FakeDB:
    def __init__(self):
        self.rows = []
        self.saved_check_error = None  # raised (once) by the already-saved query
        self.saved_check_calls = 0


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Conn:
    def __init__(self, fdb):
        self.fdb = fdb

    def transaction(self):
        return _Tx()

    async def execute(self, sql, *a):
        return "OK"

    async def fetchval(self, sql, *a):
        if "FROM recordings_s3 WHERE tenant_id = $1 AND call_id = $2" in sql:
            self.fdb.saved_check_calls += 1
            if self.fdb.saved_check_error is not None:
                err, self.fdb.saved_check_error = self.fdb.saved_check_error, None
                raise err
            for r in reversed(self.fdb.rows):
                if r["call_id"] == a[1] and r["status"] == "uploaded":
                    return r["id"]
            return None
        if "SELECT EXISTS" in sql:
            return any(r["s3_bucket"] == a[1] and r["s3_key"] == a[2] for r in self.fdb.rows)
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        if sql.strip().startswith("INSERT INTO recordings_s3"):
            bucket, key = a[3], a[4]
            if any(r["s3_bucket"] == bucket and r["s3_key"] == key for r in self.fdb.rows):
                raise Exception("duplicate key value violates unique constraint")
            rid = uuid.uuid4()
            self.fdb.rows.append(dict(id=rid, call_id=a[0], s3_bucket=bucket, s3_key=key,
                                      file_size_bytes=a[6], duration_seconds=a[7], status=a[8]))
            return {"id": rid}
        if "FROM calls" in sql:
            return {"id": uuid.uuid4()}
        raise AssertionError(sql)


class _NoS3:
    bucket = "b"
    region = "r"

    def is_available(self):
        return False


def mk_wav(seconds, rate=8000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00\x02\x00" * int(seconds * rate))
    return out.getvalue()


def buf_for(wav_bytes, rate=8000):
    b = rs.RecordingBuffer(call_id="pbx", sample_rate=rate, channels=2, bit_depth=16)
    b._wav_bytes_override = wav_bytes
    b.total_bytes = len(wav_bytes)
    return b


def on_disk_seconds(path):
    with wave.open(path, "rb") as w:
        return round(w.getnframes() / w.getframerate(), 1)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fdb = FakeDB()
    root = tmp_path / "recordings"
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(root))
    monkeypatch.setattr(rs, "RECORDING_AUDIO_CODEC", "wav")

    @asynccontextmanager
    async def acquire(pool, call_id, tenant_id, **kw):
        yield Conn(pool)

    async def keep(self, *a, **k):
        return rs.RetentionVerdict("keep", "delivered", "delivered")

    monkeypatch.setattr(rs, "_acquire_recording_connection", acquire)
    monkeypatch.setattr(rs.RecordingService, "_retention_decision", keep)
    service = rs.RecordingService(fdb, s3_client=_NoS3())
    return service, fdb, root


def _ids():
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


# ------------------------------------------------------------------ item 2

async def test_d_failed_saved_check_does_not_overwrite_the_stored_file(svc, caplog):
    service, fdb, _ = svc
    call, tenant, campaign = _ids()
    first = await service.save_and_link_outcome(call, buf_for(mk_wav(49.8)), tenant, campaign)
    assert first.status == rs.SAVE_SAVED
    path = fdb.rows[0]["s3_key"]
    with open(path, "rb") as fh:
        before = fh.read()
    assert on_disk_seconds(path) == 49.8

    fdb.saved_check_error = ConnectionResetError("simulated secret path /x/y")
    with caplog.at_level(logging.WARNING, logger=rs.logger.name):
        out = await service.save_and_link_outcome(call, buf_for(mk_wav(40.3)), tenant, campaign)

    assert (out.status, out.recording_id, out.reason) == (rs.SAVE_RETRY, None, "saved_check_unavailable")
    with open(path, "rb") as fh:
        assert fh.read() == before  # byte-identical: still the 49.8 s recording
    assert on_disk_seconds(path) == 49.8
    assert len(fdb.rows) == 1
    warnings = [r for r in caplog.records if "recording_saved_check_unavailable" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    text = warnings[0].getMessage()
    assert f"call_id={call}" in text and "error_type=ConnectionResetError" in text
    assert "simulated" not in text and "secret" not in text


async def test_e_next_try_with_a_working_check_says_already_saved_and_leaves_the_file(svc):
    service, fdb, _ = svc
    call, tenant, campaign = _ids()
    await service.save_and_link_outcome(call, buf_for(mk_wav(49.8)), tenant, campaign)
    path = fdb.rows[0]["s3_key"]
    with open(path, "rb") as fh:
        before = fh.read()
    fdb.saved_check_error = ConnectionResetError("x")
    await service.save_and_link_outcome(call, buf_for(mk_wav(40.3)), tenant, campaign)

    out = await service.save_and_link_outcome(call, buf_for(mk_wav(40.3)), tenant, campaign, mark_failure=False)

    assert out.status == rs.SAVE_SAVED and out.reason == "already_saved"
    assert out.recording_id == str(fdb.rows[0]["id"])
    with open(path, "rb") as fh:
        assert fh.read() == before
    assert len(fdb.rows) == 1


async def test_f_a_non_uuid_call_id_still_saves_as_before(svc):
    service, fdb, _ = svc
    _, tenant, campaign = _ids()
    out = await service.save_and_link_outcome("talky-out-da0f496d", buf_for(mk_wav(1.0)), tenant, campaign)
    assert out.status == rs.SAVE_SAVED
    assert len(fdb.rows) == 1
    assert os.path.isfile(fdb.rows[0]["s3_key"])
    assert fdb.saved_check_calls == 0  # the check was skipped, not failed


async def test_f2_a_non_uuid_tenant_id_returns_none_from_the_check(svc):
    service, fdb, _ = svc
    assert await service._already_saved_id(str(uuid.uuid4()), "not-a-uuid") is None
    assert fdb.saved_check_calls == 0


async def test_g_cancellation_from_the_check_propagates(svc):
    service, fdb, root = svc
    call, tenant, campaign = _ids()
    fdb.saved_check_error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service.save_and_link_outcome(call, buf_for(mk_wav(1.0)), tenant, campaign)
    assert fdb.rows == []
    assert not root.exists() or not any(p.is_file() for p in root.rglob("*"))


async def test_failed_check_writes_nothing_when_nothing_was_stored_before(svc):
    service, fdb, root = svc
    call, tenant, campaign = _ids()
    fdb.saved_check_error = OSError("boom")
    out = await service.save_and_link_outcome(call, buf_for(mk_wav(1.0)), tenant, campaign)
    assert out.status == rs.SAVE_RETRY and out.reason == "saved_check_unavailable"
    assert fdb.rows == []
    assert not root.exists() or not any(p.is_file() for p in root.rglob("*"))


# ------------------------------------------------------------------ item 3

def _dest(tmp_path, name="t/c/call.wav"):
    root = tmp_path / "recs"
    root.mkdir(exist_ok=True)
    return str(root), str(root / name)


def _files_in(directory):
    return sorted(os.listdir(directory)) if os.path.isdir(directory) else []


def test_h_a_failed_write_over_an_existing_file_leaves_exactly_the_old_bytes(tmp_path, monkeypatch):
    root, dest = _dest(tmp_path)
    old = mk_wav(2.0)
    rs._write_wav_file(root, dest, old)
    assert open(dest, "rb").read() == old

    real_fsync = os.fsync
    state = {"n": 0}

    def failing_fsync(fd):
        state["n"] += 1
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError):
        rs._write_wav_file(root, dest, mk_wav(1.0))
    monkeypatch.setattr(os, "fsync", real_fsync)

    assert open(dest, "rb").read() == old
    assert _files_in(os.path.dirname(dest)) == ["call.wav"]  # no temporary file left


def test_h2_a_failed_replace_leaves_old_bytes_and_no_temporary_file(tmp_path, monkeypatch):
    root, dest = _dest(tmp_path)
    old = mk_wav(2.0)
    rs._write_wav_file(root, dest, old)

    def failing_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        rs._write_wav_file(root, dest, mk_wav(1.0))
    monkeypatch.undo()

    assert open(dest, "rb").read() == old
    assert _files_in(os.path.dirname(dest)) == ["call.wav"]


def test_h3_the_old_bytes_stay_whole_while_the_new_ones_are_being_written(tmp_path, monkeypatch):
    root, dest = _dest(tmp_path)
    old = mk_wav(2.0)
    rs._write_wav_file(root, dest, old)
    seen = []
    real_fsync = os.fsync

    def spy_fsync(fd):
        seen.append(open(dest, "rb").read())
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    new = mk_wav(1.0)
    rs._write_wav_file(root, dest, new)
    # The first flush is the new data's own file: the destination is still whole-old.
    # On Linux a second flush follows the rename (the directory): whole-new by then.
    assert seen and seen[0] == old
    assert all(content in (old, new) for content in seen)
    assert open(dest, "rb").read() == new


def test_i_a_successful_write_has_exactly_the_new_bytes_and_fsync_comes_before_replace(tmp_path, monkeypatch):
    root, dest = _dest(tmp_path)
    calls = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd):
        calls.append("fsync")
        return real_fsync(fd)

    def spy_replace(src, dst):
        calls.append(("replace", src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)
    data = mk_wav(1.5)
    rs._write_wav_file(root, dest, data)

    assert open(dest, "rb").read() == data
    assert _files_in(os.path.dirname(dest)) == ["call.wav"]
    idx = [i for i, c in enumerate(calls) if isinstance(c, tuple)]
    assert len(idx) == 1
    assert "fsync" in calls[: idx[0]]  # the file was flushed to disk before the replace
    replace = calls[idx[0]]
    assert replace[2] == os.path.realpath(dest)
    assert os.path.dirname(replace[1]) == os.path.dirname(os.path.realpath(dest))
    if sys.platform != "win32":
        assert "fsync" in calls[idx[0] + 1:]  # the directory was flushed after


def test_i2_a_new_file_gets_mode_0600_where_modes_exist(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX file modes")
    root, dest = _dest(tmp_path)
    rs._write_wav_file(root, dest, mk_wav(0.5))
    assert (os.stat(dest).st_mode & 0o777) == 0o600


def test_j_containment_refuses_a_destination_outside_the_root(tmp_path):
    root, _ = _dest(tmp_path)
    outside = tmp_path / "elsewhere" / "x.wav"
    with pytest.raises(ValueError):
        rs._write_wav_file(root, str(outside), mk_wav(0.5))
    assert not (tmp_path / "elsewhere").exists()
    with pytest.raises(ValueError):
        rs._write_wav_file(root, os.path.join(root, "..", "up.wav"), mk_wav(0.5))
    assert not (tmp_path / "up.wav").exists()


def test_j2_containment_refuses_a_destination_through_a_symlink(tmp_path):
    root, _ = _dest(tmp_path)
    target = tmp_path / "outside_dir"
    target.mkdir()
    link = os.path.join(root, "link")
    try:
        os.symlink(str(target), link, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("cannot create a symlink on this platform")
    with pytest.raises(ValueError):
        rs._write_wav_file(root, os.path.join(link, "x.wav"), mk_wav(0.5))
    assert os.listdir(str(target)) == []  # nothing, not even a temporary file, went outside


def test_k_two_saves_do_not_share_a_temporary_name(tmp_path, monkeypatch):
    root, _ = _dest(tmp_path)
    sources = []
    real_replace = os.replace

    def spy_replace(src, dst):
        sources.append(src)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    d = os.path.join(root, "t", "c")
    rs._write_wav_file(root, os.path.join(d, "a.wav"), mk_wav(0.2))
    rs._write_wav_file(root, os.path.join(d, "b.wav"), mk_wav(0.2))
    rs._write_wav_file(root, os.path.join(d, "a.wav"), mk_wav(0.3))
    assert len(sources) == 3 and len(set(sources)) == 3
    assert all(os.path.dirname(s) == os.path.realpath(d) for s in sources)
