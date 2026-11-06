"""Item20: real file/mount-boundary tests; cipher double is NOT encryption proof.

The optional real-GPG case separately verifies the selected a0bd0353 primitive.
No test connects to a database, SSH endpoint, mount or provider.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
SOURCE = "backup.example.invalid:/talky"
FSTYPE = "nfs4"


@pytest.fixture
def mod(monkeypatch):
    path = ROOT / "offhost.py"
    assert path.is_file(), "F8-01/F8-02 encrypted DB+recordings off-host adapter is missing"
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location("qa_round3_offhost", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CipherDouble:
    """Only packages/readback/failure behavior; deliberately not cryptography."""

    fail_encrypt = False
    fail_readback = False
    plaintexts = []

    def __init__(self, passphrase_file, executable=None, **kwargs):
        assert Path(passphrase_file).read_text().strip() == "synthetic-test-passphrase-" * 2

    def encrypt(self, source, target):
        if self.fail_encrypt:
            raise RuntimeError("secret-diagnostic-must-not-escape")
        payload = Path(source).read_bytes()
        self.plaintexts.append(payload)
        Path(target).write_bytes(b"QA-ONLY-NOT-CRYPTO\n" + payload[::-1])

    def decrypt(self, source, target):
        if self.fail_readback:
            raise RuntimeError("secret-diagnostic-must-not-escape")
        data = Path(source).read_bytes()
        assert data.startswith(b"QA-ONLY-NOT-CRYPTO\n")
        Path(target).write_bytes(data.split(b"\n", 1)[1][::-1])

    def decrypted_digest(self, source):
        if self.fail_readback:
            raise RuntimeError("secret-diagnostic-must-not-escape")
        payload = Path(source).read_bytes().split(b"\n", 1)[1][::-1]
        return hashlib.sha256(payload).hexdigest(), len(payload)


@pytest.fixture
def fixture(mod, tmp_path, monkeypatch):
    dump = tmp_path / "local" / "talky-20261106T110000Z.dump"
    dump.parent.mkdir()
    dump.write_bytes(b"PGDMP-synthetic-data\x00" * 64)
    Path(str(dump) + ".sha256").write_text(hashlib.sha256(dump.read_bytes()).hexdigest() + "  " + str(dump) + "\n")
    recordings = tmp_path / "recordings"
    (recordings / "tenant-a").mkdir(parents=True)
    (recordings / "tenant-a" / "call.wav").write_bytes(b"RIFF synthetic private recording")
    (recordings / "empty-directory").mkdir()
    destination = tmp_path / "remote"
    destination.mkdir()
    key = tmp_path / "synthetic-passphrase"
    key.write_text("synthetic-test-passphrase-" * 2)
    key.chmod(0o600)
    monkeypatch.setattr(mod, "Gpg", CipherDouble)
    CipherDouble.fail_encrypt = False
    CipherDouble.fail_readback = False
    CipherDouble.plaintexts = []
    mount = {"target": str(destination), "source": SOURCE, "fstype": FSTYPE, "options": "rw,nodev,nosuid"}
    calls = []

    def findmnt(argv, **kwargs):
        calls.append((list(argv), kwargs))
        assert Path(argv[0]).name in {"findmnt", "findmnt.exe"}, "unexpected external command"
        assert "--json" in argv and "--target" in argv
        assert kwargs.get("timeout", 0) > 0 and kwargs["timeout"] <= 10
        assert not kwargs.get("shell", False)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"filesystems": [mount]}), "")

    monkeypatch.setattr(mod.subprocess, "run", findmnt)
    return dict(dump=dump, recordings=recordings, destination=destination,
                passphrase_file=key, expected_source=SOURCE, expected_fstype=FSTYPE,
                now=NOW), mount, calls


def publish(mod, fixture, **overrides):
    args = dict(fixture[0])
    args.update(overrides)
    return mod.publish(**args)


def test_unconfigured_destination_never_reads_data_key_or_starts_transport(mod, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("unconfigured off-host backup touched crypto or mount transport")
    monkeypatch.setattr(mod, "Gpg", forbidden)
    monkeypatch.setattr(mod.subprocess, "run", forbidden)
    result = mod.publish(dump=tmp_path / "missing", recordings=tmp_path / "missing-media",
                         destination=None, passphrase_file=None, expected_source=None,
                         expected_fstype=None, now=NOW)
    assert result["status"] == "unconfigured"
    assert result["uploaded"] is False
    assert list(tmp_path.iterdir()) == []


def test_database_and_all_recordings_share_one_encrypted_payload(mod, fixture, tmp_path):
    result = publish(mod, fixture)
    bundle = fixture[0]["destination"] / fixture[0]["dump"].stem
    assert result["status"] == "remote_verified"
    assert result["encrypted"] is True
    assert result["recording_files"] == 1
    assert sorted(p.name for p in bundle.iterdir()) == ["payload.tar.gpg", "receipt.json"]
    receipt = json.loads((bundle / "receipt.json").read_text())
    assert receipt["snapshot_time"] == "2026-11-06T11:00:00+00:00"
    assert receipt["ciphertext_sha256"] == hashlib.sha256((bundle / "payload.tar.gpg").read_bytes()).hexdigest()
    assert "tenant-a" not in (bundle / "receipt.json").read_text()
    assert SOURCE not in (bundle / "receipt.json").read_text(), "receipt must not expose remote credentials/host names"
    restored = tmp_path / "restored"
    mod.restore_payload(bundle, restored, fixture[0]["passphrase_file"])
    assert (restored / "database.dump").read_bytes() == fixture[0]["dump"].read_bytes()
    assert (restored / "recordings/tenant-a/call.wav").read_bytes() == b"RIFF synthetic private recording"
    manifest = json.loads((restored / "manifest.json").read_text())
    assert set(manifest["files"]) == {"database.dump", "recordings/tenant-a/call.wav"}
    assert fixture[0]["dump"].is_file() and fixture[0]["recordings"].is_dir()
    assert fixture[2], "actual mount boundary must be consulted"


@pytest.mark.parametrize("field,value", [
    ("fstype", "ext4"), ("fstype", "overlay"),
    ("source", "/dev/sdb1"), ("source", "other.example.invalid:/talky"),
    ("options", "ro,nodev"),
])
def test_remote_mount_identity_mismatch_refuses_before_encrypt(mod, fixture, field, value):
    fixture[1][field] = value
    with pytest.raises(mod.RecoveryError):
        publish(mod, fixture)
    assert CipherDouble.plaintexts == []
    assert list(fixture[0]["destination"].iterdir()) == []


@pytest.mark.parametrize("source", ["localhost:/talky", "127.0.0.1:/talky", "[::1]:/talky", "[::ffff:127.0.0.1]:/talky"])
def test_loopback_mount_is_not_offhost(mod, fixture, source):
    fixture[1]["source"] = source
    with pytest.raises(mod.RecoveryError):
        publish(mod, fixture, expected_source=source)
    assert not CipherDouble.plaintexts


def test_mount_disappearing_during_upload_cannot_publish(mod, fixture, monkeypatch):
    original = CipherDouble.encrypt
    def encrypt(self, source, target):
        original(self, source, target)
        fixture[1]["fstype"] = "ext4"
    monkeypatch.setattr(CipherDouble, "encrypt", encrypt)
    with pytest.raises(mod.RecoveryError):
        publish(mod, fixture)
    assert list(fixture[0]["destination"].iterdir()) == []


@pytest.mark.parametrize("mutation", ["dump", "sidecar", "missing-media"])
def test_unverified_inputs_never_publish(mod, fixture, mutation):
    if mutation == "dump":
        fixture[0]["dump"].write_bytes(b"truncated")
    elif mutation == "sidecar":
        Path(str(fixture[0]["dump"]) + ".sha256").write_text("0" * 64 + "  unrelated.dump\n")
    else:
        fixture[0]["recordings"] = fixture[0]["recordings"] / "missing"
    with pytest.raises(mod.RecoveryError):
        publish(mod, fixture)
    assert list(fixture[0]["destination"].iterdir()) == []


@pytest.mark.parametrize("failure", ["fail_encrypt", "fail_readback"])
def test_encryption_or_readback_failure_cleans_only_owned_staging(mod, fixture, failure):
    sentinel = fixture[0]["destination"] / "owner-file"
    sentinel.write_bytes(b"keep")
    setattr(CipherDouble, failure, True)
    with pytest.raises(mod.RecoveryError) as caught:
        publish(mod, fixture)
    assert "secret-diagnostic" not in str(caught.value)
    assert list(fixture[0]["destination"].iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"


def test_existing_remote_bundle_is_never_overwritten(mod, fixture):
    publish(mod, fixture)
    bundle = fixture[0]["destination"] / fixture[0]["dump"].stem
    before = (bundle / "payload.tar.gpg").read_bytes()
    with pytest.raises(mod.RecoveryError):
        publish(mod, fixture)
    assert (bundle / "payload.tar.gpg").read_bytes() == before


def test_remote_health_rehashes_ciphertext_and_uses_original_snapshot(mod, fixture):
    publish(mod, fixture)
    args = fixture[0]
    result = mod.remote_health(args["destination"], expected_source=SOURCE, expected_fstype=FSTYPE,
                               maximum_age_hours=26, now=NOW)
    assert result["age_hours"] == 1
    bundle = args["destination"] / args["dump"].stem
    (bundle / "payload.tar.gpg").write_bytes(b"corrupted-after-upload")
    with pytest.raises(mod.RecoveryError):
        mod.remote_health(args["destination"], expected_source=SOURCE, expected_fstype=FSTYPE,
                          maximum_age_hours=26, now=NOW)


@pytest.mark.parametrize("delta", [timedelta(hours=27), -timedelta(hours=2)])
def test_remote_health_refuses_stale_or_future_snapshot_even_with_fresh_mtime(mod, fixture, delta):
    publish(mod, fixture)
    args = fixture[0]
    with pytest.raises(mod.RecoveryError):
        mod.remote_health(args["destination"], expected_source=SOURCE, expected_fstype=FSTYPE,
                          maximum_age_hours=26, now=NOW + delta)


def test_flat_local_health_matches_real_producer_format_not_bundle_layout(mod, fixture):
    result = mod.local_health(fixture[0]["dump"].parent, maximum_age_hours=26, now=NOW)
    assert result["age_hours"] == 1
    assert result["bytes"] == fixture[0]["dump"].stat().st_size
    fixture[0]["dump"].write_bytes(b"changed")
    with pytest.raises(mod.RecoveryError):
        mod.local_health(fixture[0]["dump"].parent, maximum_age_hours=26, now=NOW)


def test_source_recording_symlink_is_refused_not_followed(mod, fixture, tmp_path):
    outside = tmp_path / "private-outside"
    outside.write_bytes(b"never-copy")
    try:
        (fixture[0]["recordings"] / "escape.wav").symlink_to(outside)
    except OSError:
        pytest.skip("host cannot create test symlink")
    with pytest.raises(mod.RecoveryError):
        publish(mod, fixture)
    assert list(fixture[0]["destination"].iterdir()) == []


def test_restore_refuses_existing_destination(mod, fixture, tmp_path):
    publish(mod, fixture)
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "sentinel").write_text("keep")
    bundle = fixture[0]["destination"] / fixture[0]["dump"].stem
    with pytest.raises(mod.RecoveryError):
        mod.restore_payload(bundle, destination, fixture[0]["passphrase_file"])
    assert (destination / "sentinel").read_text() == "keep"


@pytest.mark.parametrize("maximum_age_hours", [0, -1, float("nan"), float("inf")])
def test_health_rejects_unbounded_age_threshold(mod, fixture, maximum_age_hours):
    with pytest.raises(mod.RecoveryError):
        mod.local_health(fixture[0]["dump"].parent, maximum_age_hours=maximum_age_hours, now=NOW)


def test_production_cli_has_no_clear_copy_escape_hatch(mod, capsys):
    with pytest.raises(SystemExit) as caught:
        mod.main(["publish", "--dump", "synthetic-never-read.dump", "--allow-unencrypted"])
    assert caught.value.code != 0
    assert "--allow-unencrypted" in capsys.readouterr().err


def test_real_gpg_integrity_and_plaintext_readback(mod, tmp_path):
    candidates = [shutil.which("gpg"), "C:/Program Files/Git/usr/bin/gpg.exe"]
    executable = next((value for value in candidates if value and Path(value).is_file()), None)
    if executable is None:
        pytest.skip("GPG unavailable: crypto proof remains pending")
    key = tmp_path / "synthetic-key"
    key.write_text("synthetic-test-passphrase-" * 2)
    key.chmod(0o600)
    plaintext = tmp_path / "payload"
    plaintext.write_bytes(b"PGDMP-synthetic-tenant-and-audio\x00" * 1024)
    cipher = mod.Gpg(key, executable)
    encrypted = tmp_path / "payload.gpg"
    cipher.encrypt(plaintext, encrypted)
    assert plaintext.read_bytes()[:64] not in encrypted.read_bytes()
    assert cipher.decrypted_digest(encrypted) == (hashlib.sha256(plaintext.read_bytes()).hexdigest(), plaintext.stat().st_size)
    damaged = bytearray(encrypted.read_bytes())
    damaged[len(damaged) // 2] ^= 1
    encrypted.write_bytes(damaged)
    with pytest.raises(mod.RecoveryError):
        cipher.decrypted_digest(encrypted)
