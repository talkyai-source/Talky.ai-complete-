"""F8-02: call recordings in the encrypted FTP off-site backup (13 Nov 2026).

``sync-recordings`` / ``verify-recordings`` in scripts/database/offsite_ftp.py.
The FTP server and the cipher are doubles; nothing here talks to a real server.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "database"
SECRET = "netrc-password-must-never-appear"


@pytest.fixture
def mod(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("qa_offsite_ftp_rec", SCRIPTS / "offsite_ftp.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Cipher:
    def __init__(self, *, corrupt=False):
        self.corrupt = corrupt

    def encrypt(self, source: Path, target: Path) -> None:
        target.write_bytes(b"ENC:" + source.read_bytes())

    def decrypt(self, source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes()[len(b"ENC:"):])

    def decrypted_digest(self, source: Path):
        plain = source.read_bytes()[len(b"ENC:"):]
        if self.corrupt:
            plain += b"!"
        return hashlib.sha256(plain).hexdigest(), len(plain)


class Server:
    def __init__(self):
        self.files = {}
        self.uploads = []
        self.deleted = []

    def upload(self, local, name):
        self.files[name] = local.read_bytes()
        self.uploads.append(name)

    def size(self, name):
        return len(self.files[name])

    def names(self):
        return list(self.files)

    def delete(self, name):
        self.deleted.append(name)

    def download(self, name, target):
        target.write_bytes(self.files[name])


def _recordings(tmp_path, count=3, size=100):
    root = tmp_path / "recordings"
    root.mkdir()
    for i in range(count):
        sub = root / f"tenant{i % 2}"
        sub.mkdir(exist_ok=True)
        path = sub / f"call{i}.wav"
        path.write_bytes(bytes([i + 1]) * size)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))  # call{count-1} is newest
    return root


def _rec_uploads(server):
    return [n for n in server.uploads if n.startswith("rec-")]


def test_a_second_run_skips_files_already_uploaded_with_the_same_hash(mod, tmp_path):
    root, server = _recordings(tmp_path), Server()
    first = mod.sync_recordings(root, server, Cipher())
    assert len(_rec_uploads(server)) == 3 and "uploaded=3" in first and "remaining=0" in first
    assert all(server.files[n].startswith(b"ENC:") for n in _rec_uploads(server)), "only encrypted bytes leave"
    server.uploads.clear()
    second = mod.sync_recordings(root, server, Cipher())
    assert _rec_uploads(server) == [] and "uploaded=0" in second and "skipped=3" in second
    (root / "tenant0" / "call0.wav").write_bytes(b"changed")
    mod.sync_recordings(root, server, Cipher())
    assert len(_rec_uploads(server)) == 1, "a changed file is uploaded again, under a new remote name"


def test_the_byte_budget_stops_cleanly_and_the_next_run_resumes(mod, tmp_path):
    root, server = _recordings(tmp_path, count=5, size=100), Server()
    first = mod.sync_recordings(root, server, Cipher(), budget_bytes=250)
    assert len(_rec_uploads(server)) == 2 and "remaining=3" in first
    second = mod.sync_recordings(root, server, Cipher(), budget_bytes=250)
    assert "uploaded=2" in second and "skipped=2" in second and "remaining=1" in second
    third = mod.sync_recordings(root, server, Cipher(), budget_bytes=250)
    assert "uploaded=1" in third and "remaining=0" in third
    assert len(set(_rec_uploads(server))) == 5


def test_sync_never_deletes_locally_or_remotely(mod, tmp_path):
    root, server = _recordings(tmp_path), Server()
    before = sorted(p for p in root.rglob("*") if p.is_file())
    mod.sync_recordings(root, server, Cipher())
    assert server.deleted == []
    assert sorted(p for p in root.rglob("*") if p.is_file()) == before


def test_verify_passes_for_a_sample_of_the_newest_files(mod, tmp_path):
    root, server = _recordings(tmp_path, count=5), Server()
    mod.sync_recordings(root, server, Cipher())
    server.downloaded = []
    original = server.download
    server.download = lambda name, target: (server.downloaded.append(name), original(name, target))[1]
    receipt = mod.verify_recordings(server, Cipher(), sample=2)
    assert "verify ok sampled=2 total_uploaded=5" in receipt
    sampled = [n for n in server.downloaded if n.startswith("rec-")]
    expected = set()
    for i in (4, 3):  # call4 and call3 carry the newest mtimes
        relative = f"tenant{i % 2}/call{i}.wav"
        body = bytes([i + 1]) * 100
        expected.add(f"rec-{hashlib.sha256(relative.encode()).hexdigest()[:16]}-{hashlib.sha256(body).hexdigest()[:16]}.gpg")
    assert set(sampled) == expected


def test_verify_fails_when_a_downloaded_copy_does_not_match_the_manifest(mod, tmp_path):
    root, server = _recordings(tmp_path), Server()
    mod.sync_recordings(root, server, Cipher())
    with pytest.raises(mod.RecoveryError, match="failed verification"):
        mod.verify_recordings(server, Cipher(corrupt=True), sample=3)


def test_verify_without_any_manifest_fails(mod):
    with pytest.raises(mod.RecoveryError, match="no uploaded recordings"):
        mod.verify_recordings(Server(), Cipher())


def test_no_secret_in_curl_argv_or_error_text(mod, tmp_path, monkeypatch):
    netrc = tmp_path / "netrc"
    netrc.write_text(f"machine ftp.example.invalid login u password {SECRET}\n")
    netrc.chmod(0o600)
    seen = []

    def fake_run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 9, "", f"530 login incorrect for {SECRET}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    ftp = mod.Ftp("ftp://ftp.example.invalid/talky/", netrc, curl="curl")
    with pytest.raises(mod.RecoveryError) as raised:
        ftp.upload(tmp_path / "x.gpg", "rec-a.gpg")
    assert SECRET not in str(raised.value)
    assert all(SECRET not in part for command in seen for part in command)


def test_the_command_line_fails_non_zero_without_leaking_the_passphrase(mod, tmp_path, monkeypatch, capsys):
    root = _recordings(tmp_path)

    class Broken(Server):
        def upload(self, local, name):
            raise mod.RecoveryError("the FTP upload failed (curl exit 9)")

    monkeypatch.setattr(mod, "_settings", lambda: (Broken(), Cipher(), 30))
    monkeypatch.setenv("TALKY_RECORDINGS_DIR", str(root))
    assert mod.main(["sync-recordings"]) == 1
    captured = capsys.readouterr()
    assert "OFFSITE-FTP FAILED" in captured.err and SECRET not in captured.out + captured.err
    monkeypatch.setattr(mod, "_settings", lambda: (Server(), Cipher(), 30))
    assert mod.main(["verify-recordings"]) == 1, "nothing uploaded yet: verify must fail"


def test_the_budget_comes_from_the_environment(mod, tmp_path, monkeypatch, capsys):
    root, server = _recordings(tmp_path, count=4), Server()
    monkeypatch.setattr(mod, "_settings", lambda: (server, Cipher(), 30))
    monkeypatch.setenv("TALKY_RECORDINGS_DIR", str(root))
    monkeypatch.setenv("TALKY_OFFSITE_FTP_RECORDINGS_MAX_BYTES", "150")
    assert mod.main(["sync-recordings"]) == 0
    out = capsys.readouterr().out
    assert "uploaded=1" in out and "remaining=3" in out


def test_unchanged_size_and_mtime_files_are_never_read_again(mod, tmp_path, monkeypatch):
    root, server = _recordings(tmp_path), Server()
    mod.sync_recordings(root, server, Cipher())
    monkeypatch.setattr(mod, "digest", lambda path: pytest.fail("an unchanged file was read"))
    assert "skipped=3" in mod.sync_recordings(root, server, Cipher())


def test_same_size_different_mtime_is_rehashed_skipped_and_the_manifest_refreshed(mod, tmp_path):
    root, server = _recordings(tmp_path), Server()
    mod.sync_recordings(root, server, Cipher())
    server.uploads.clear()
    target = root / "tenant0" / "call0.wav"
    os.utime(target, (2_000_000, 2_000_000))
    assert "skipped=3" in mod.sync_recordings(root, server, Cipher())
    assert _rec_uploads(server) == []
    manifest = mod._load_manifest(server, Cipher(), tmp_path)
    assert manifest["tenant0/call0.wav"]["mtime_ns"] == 2_000_000 * 10**9


def test_remaining_counts_only_files_not_already_recorded(mod, tmp_path):
    root, server = _recordings(tmp_path, count=6), Server()
    mod.sync_recordings(root, server, Cipher(), budget_bytes=250)  # newest two: call5, call4
    (root / "tenant0" / "call0.wav").write_bytes(b"changed!")  # recorded file, now changed
    newer = root / "tenant1" / "new.wav"
    newer.write_bytes(b"z" * 100)
    os.utime(newer, (3_000_000, 3_000_000))
    out = mod.sync_recordings(root, server, Cipher(), budget_bytes=100)
    # new.wav uploads first; call3, call2, call1 and the changed call0 still need it; call5/call4 do not
    assert "uploaded=1" in out and "remaining=4" in out


def test_orphans_are_counted_and_nothing_is_removed(mod, tmp_path):
    root, server = _recordings(tmp_path), Server()
    mod.sync_recordings(root, server, Cipher())
    (root / "tenant0" / "call0.wav").unlink()
    out = mod.sync_recordings(root, server, Cipher())
    assert "orphans=1" in out and server.deleted == []
    assert "erase" in mod.__doc__


def test_a_missing_recordings_directory_is_a_clean_failure(mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "_settings", lambda: (Server(), Cipher(), 30))
    monkeypatch.setenv("TALKY_RECORDINGS_DIR", str(tmp_path / "nope"))
    assert mod.main(["sync-recordings"]) == 1
    err = capsys.readouterr().err
    assert "OFFSITE-FTP FAILED" in err and "missing or unreadable" in err and "Traceback" not in err


def test_the_manifest_is_stored_every_25_uploads_so_a_late_failure_keeps_progress(mod, tmp_path):
    root = _recordings(tmp_path, count=40, size=10)

    class Dies(Server):
        def upload(self, local, name):
            if name.startswith("rec-") and len(_rec_uploads(self)) >= 30:
                raise mod.RecoveryError("the FTP upload failed (curl exit 9)")
            super().upload(local, name)

    server = Dies()
    with pytest.raises(mod.RecoveryError):
        mod.sync_recordings(root, server, Cipher())
    assert len(mod._load_manifest(server, Cipher(), tmp_path)) >= 25
