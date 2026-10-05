"""Actual temporary-file controls; POSIX mode proof is explicitly platform gated."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.domain.services.recording_service import RecordingBuffer, RecordingService, _write_wav_file


def test_new_recording_is_readable_and_removable_by_its_owner(tmp_path):
    root = tmp_path / "recordings"
    path = root / "tenant" / "campaign" / "call.wav"
    payload = b"RIFF\x00\x01\r\n\x1a\xffsynthetic audio"

    _write_wav_file(str(root), str(path), payload)

    assert path.read_bytes() == payload
    path.unlink()
    assert not path.exists()


def test_existing_regular_recording_can_be_replaced_without_touching_neighbor(tmp_path):
    root = tmp_path / "recordings"
    root.mkdir()
    path = root / "call.wav"
    path.write_bytes(b"long old recording")
    neighbor = root / "neighbor.wav"
    neighbor.write_bytes(b"preserve")

    _write_wav_file(str(root), str(path), b"new")

    assert path.read_bytes() == b"new"
    assert neighbor.read_bytes() == b"preserve"


def test_writer_rejects_outside_root_before_creating_or_changing_files(tmp_path):
    root = tmp_path / "recordings"
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"preserve")

    with pytest.raises(ValueError, match="outside"):
        _write_wav_file(str(root), str(outside), b"unexpected")

    assert outside.read_bytes() == b"preserve"
    assert not root.exists()


@pytest.mark.parametrize("link_is_directory", [True, False])
def test_writer_rejects_symlink_escape_without_touching_target(tmp_path, link_is_directory):
    root = tmp_path / "recordings"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "call.wav"
    target.write_bytes(b"preserve")
    link = root / ("tenant" if link_is_directory else "call.wav")
    try:
        link.symlink_to(outside if link_is_directory else target,
                        target_is_directory=link_is_directory)
    except OSError as exc:
        pytest.skip(f"OS cannot create a symlink: {exc}")
    path = link / "call.wav" if link_is_directory else link

    with pytest.raises(ValueError, match="outside"):
        _write_wav_file(str(root), str(path), b"unexpected")

    assert target.read_bytes() == b"preserve"


def test_parent_file_failure_preserves_existing_bytes(tmp_path):
    root = tmp_path / "recordings"
    root.write_bytes(b"not a directory")

    with pytest.raises(OSError):
        _write_wav_file(str(root), str(root / "call.wav"), b"unexpected")

    assert root.read_bytes() == b"not a directory"


def test_configured_storage_root_may_itself_be_a_symlink(tmp_path):
    storage = tmp_path / "mounted-storage"
    storage.mkdir()
    configured = tmp_path / "recordings"
    try:
        configured.symlink_to(storage, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"OS cannot create a symlink: {exc}")

    _write_wav_file(str(configured), str(configured / "tenant" / "call.wav"), b"audio")

    assert (storage / "tenant" / "call.wav").read_bytes() == b"audio"


@pytest.mark.asyncio
async def test_denied_file_open_does_not_register_success_or_fallback_elsewhere(tmp_path, monkeypatch):
    import app.domain.services.recording_service as recording

    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(recording, "RECORDING_AUDIO_CODEC", "wav")
    svc = RecordingService(db_pool=AsyncMock())
    monkeypatch.setattr(svc, "_retention_allowed", AsyncMock(return_value=True))
    inserted = AsyncMock()
    linked = AsyncMock()
    monkeypatch.setattr(svc, "_insert_recording_record", inserted)
    monkeypatch.setattr(svc, "_update_call_recording_url", linked)
    original_open = os.open
    attempted = []

    def deny_recording(path, flags, mode=0o777, **kwargs):
        if str(path).endswith("call.wav"):
            attempted.append(Path(path))
            raise PermissionError("synthetic storage denial")
        return original_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", deny_recording)
    buffer = RecordingBuffer(call_id="call", sample_rate=16000, channels=1, bit_depth=16)
    buffer.add_chunk(b"\x01\x02" * 500)
    result = await svc._save_local("call", buffer, "tenant", "campaign")

    assert result is None
    assert attempted == [tmp_path / "tenant" / "campaign" / "call.wav"]
    assert list(tmp_path.rglob("*.wav")) == []
    inserted.assert_not_awaited()
    linked.assert_not_awaited()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX permissions; Windows ACLs are not mode-bit proof")
@pytest.mark.parametrize("creation_mask", [0o000, 0o022, 0o077])
def test_new_hierarchy_and_audio_are_private_without_changing_process_umask(tmp_path, creation_mask):
    # Set umask only in a child; the application writer must never mutate the
    # process-wide mask while other threads create unrelated files.
    script = """
import json, os, stat, sys
from pathlib import Path
from app.domain.services.recording_service import _write_wav_file
base = Path(sys.argv[1])
mask = int(sys.argv[2])
os.umask(mask)
root = base / 'new-root'
path = root / 'tenant' / 'campaign' / 'call.wav'
_write_wav_file(str(root), str(path), b'synthetic audio')
after = os.umask(mask)
print(json.dumps({'modes': [stat.S_IMODE(p.stat().st_mode) for p in
    (root, root / 'tenant', root / 'tenant' / 'campaign', path)],
    'mask': after, 'readable': path.read_bytes() == b'synthetic audio'}))
path.unlink()
assert not path.exists()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(creation_mask)],
        cwd=Path(__file__).resolve().parents[2],
        text=True, capture_output=True, timeout=20, check=True,
    )
    observed = json.loads(result.stdout)
    assert observed == {"modes": [0o700, 0o700, 0o700, 0o600],
                        "mask": creation_mask, "readable": True}


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX permissions; Windows ACLs are not mode-bit proof")
def test_existing_directory_and_file_modes_are_not_migrated(tmp_path):
    root = tmp_path / "recordings"
    tenant = root / "tenant"
    tenant.mkdir(parents=True)
    root.chmod(0o755)
    tenant.chmod(0o750)
    old = tenant / "old.wav"
    old.write_bytes(b"old")
    old.chmod(0o640)
    fresh = tenant / "new-campaign" / "new.wav"

    _write_wav_file(str(root), str(old), b"replacement")
    _write_wav_file(str(root), str(fresh), b"fresh")

    assert [stat.S_IMODE(p.stat().st_mode) for p in (root, tenant, old)] == [0o755, 0o750, 0o640]
    assert stat.S_IMODE(fresh.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600
    assert old.read_bytes() == b"replacement"
