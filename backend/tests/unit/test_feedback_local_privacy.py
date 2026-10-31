"""Local feedback creation boundaries; deployed permissions need POSIX acceptance."""

from __future__ import annotations

import builtins
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import app.domain.services.call_feedback_service as feedback


class UnavailableS3:
    def is_available(self):
        return False


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CALL_FEEDBACK_ALLOW_LOCAL_STORAGE", "true")
    return feedback.FeedbackAudioStorage(
        s3_client=UnavailableS3(), local_dir=str(tmp_path / "new" / "feedback")
    )


async def save(storage, feedback_id="44444444-4444-4444-4444-444444444444"):
    return await storage.save(
        tenant_id="11111111-1111-1111-1111-111111111111",
        call_id="22222222-2222-2222-2222-222222222222",
        feedback_id=feedback_id,
        mime_type="audio/webm",
        audio=b"\x00\r\n\x1a\xffsynthetic feedback",
    )


def test_each_new_directory_requests_owner_only_mode(tmp_path, monkeypatch):
    original = os.mkdir
    observed = []

    def mkdir(path, mode=0o777, **kwargs):
        observed.append((Path(path), mode))
        return original(path, mode, **kwargs)

    monkeypatch.setattr(os, "mkdir", mkdir)
    path = tmp_path / "new" / "nested" / "feedback.webm"
    feedback._write_local(str(path), b"audio")

    assert path.read_bytes() == b"audio"
    assert observed == [(tmp_path / "new", 0o700), (path.parent, 0o700)]


def test_audio_requests_owner_only_file_mode_without_losing_exclusivity(tmp_path, monkeypatch):
    path = tmp_path / "feedback.webm"
    original_open = os.open
    observed = []

    def descriptor_open(name, flags, mode=0o777, **kwargs):
        observed.append((flags, mode))
        return original_open(name, flags, mode, **kwargs)

    # Observe both the old builtin-open creation default and explicit os.open.
    # This checks requested OS permissions on Windows; it does not prove ACLs.
    def builtin_open(name, mode):
        return builtins.open(
            name, mode, opener=lambda name, flags: descriptor_open(name, flags, 0o666)
        )

    monkeypatch.setattr(os, "open", descriptor_open)
    monkeypatch.setattr(feedback, "open", builtin_open, raising=False)
    feedback._write_local(str(path), b"\x00\r\n\x1a\xffaudio")

    assert path.read_bytes() == b"\x00\r\n\x1a\xffaudio"
    assert len(observed) == 1
    flags, mode = observed[0]
    assert flags & os.O_EXCL and flags & os.O_CREAT and not flags & os.O_TRUNC
    assert mode == 0o600


async def test_saved_audio_can_be_read_and_deleted_by_owner(storage):
    stored = await save(storage)
    row = {"audio_storage_provider": stored.provider, "audio_key": stored.key}
    assert await storage.read(row) == b"\x00\r\n\x1a\xffsynthetic feedback"
    await storage.delete(stored)
    assert not Path(stored.key).exists()


async def test_existing_audio_is_never_replaced(storage):
    stored = await save(storage)
    Path(stored.key).write_bytes(b"preserved original")
    with pytest.raises(feedback.FeedbackStorageError, match="Could not store"):
        await save(storage)
    assert Path(stored.key).read_bytes() == b"preserved original"


def test_existing_directories_are_not_chmodded_or_process_umask_changed(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Existing permissions and process mask must be preserved")

    monkeypatch.setattr(os, "chmod", forbidden)
    monkeypatch.setattr(os, "umask", forbidden)
    feedback._write_local(str(tmp_path / "new" / "note.webm"), b"audio")
    assert (tmp_path / "new" / "note.webm").read_bytes() == b"audio"


def test_non_directory_parent_is_preserved(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_bytes(b"preserve")
    with pytest.raises(OSError):
        feedback._write_local(str(parent / "note.webm"), b"audio")
    assert parent.read_bytes() == b"preserve"


def test_unavailable_storage_root_fails_without_looping(tmp_path, monkeypatch):
    probes = []

    def unavailable(path):
        probes.append(path)
        assert len(probes) < 64, "Writer revisited unavailable root without failing"
        return False

    monkeypatch.setattr(os.path, "exists", unavailable)
    with pytest.raises(OSError, match="storage root is unavailable"):
        feedback._write_local(str(tmp_path / "note.webm"), b"audio")
    assert len(probes) == len(set(probes))


@pytest.mark.parametrize("competing_directory", [True, False])
def test_concurrent_directory_creation_only_tolerates_a_directory(tmp_path, monkeypatch, competing_directory):
    original = os.mkdir
    parent = tmp_path / "feedback"

    def create_competitor(path, mode=0o777, **kwargs):
        if Path(path) == parent:
            if competing_directory:
                original(path, mode, **kwargs)
            else:
                parent.write_bytes(b"preserve competitor")
            raise FileExistsError(str(path))
        return original(path, mode, **kwargs)

    monkeypatch.setattr(os, "mkdir", create_competitor)
    if competing_directory:
        feedback._write_local(str(parent / "note.webm"), b"audio")
        assert (parent / "note.webm").read_bytes() == b"audio"
    else:
        with pytest.raises(OSError):
            feedback._write_local(str(parent / "note.webm"), b"audio")
        assert parent.read_bytes() == b"preserve competitor"


async def test_denied_creation_returns_no_saved_receipt(storage, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("synthetic storage denial")

    monkeypatch.setattr(os, "mkdir", denied)
    with pytest.raises(feedback.FeedbackStorageError, match="Could not store"):
        await save(storage)
    assert not Path(storage._local_dir).exists()


async def test_production_opt_in_still_required(storage, monkeypatch):
    monkeypatch.delenv("CALL_FEEDBACK_ALLOW_LOCAL_STORAGE")
    with pytest.raises(feedback.FeedbackStorageError, match="not configured"):
        await save(storage)
    assert not Path(storage._local_dir).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits; Windows ACLs require separate acceptance")
@pytest.mark.parametrize("mask", [0o000, 0o022, 0o077])
def test_posix_creation_modes_are_private_and_umask_preserved(tmp_path, mask):
    script = """
import json, os, stat, sys
from pathlib import Path
from app.domain.services.call_feedback_service import _write_local
root = Path(sys.argv[1]) / 'new'
path = root / 'feedback' / 'note.webm'
mask = int(sys.argv[2])
os.umask(mask)
_write_local(str(path), b'audio')
print(json.dumps({'modes': [stat.S_IMODE(p.stat().st_mode) for p in
    (root, path.parent, path)], 'mask': os.umask(mask)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(mask)],
        cwd=Path(__file__).resolve().parents[2], capture_output=True,
        text=True, timeout=20, check=True,
    )
    assert json.loads(result.stdout) == {"modes": [0o700, 0o700, 0o600], "mask": mask}


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits; Windows ACLs require separate acceptance")
def test_posix_existing_modes_and_existing_audio_are_preserved(tmp_path):
    tmp_path.chmod(0o750)
    existing = tmp_path / "existing.webm"
    existing.write_bytes(b"preserve")
    existing.chmod(0o640)
    with pytest.raises(FileExistsError):
        feedback._write_local(str(existing), b"replacement")
    feedback._write_local(str(tmp_path / "new.webm"), b"new")
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o750
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640
    assert existing.read_bytes() == b"preserve"
    assert stat.S_IMODE((tmp_path / "new.webm").stat().st_mode) == 0o600
