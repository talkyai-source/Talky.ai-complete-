"""F8-01: an encrypted copy of every nightly dump on the owner's FTP server (12 Nov 2026).

scripts/database/offsite_ftp.py encrypts the dump with the recovery primitive,
proves the copy decrypts to the dump's bytes, uploads it over FTP with TLS
required, checks its size on the server and prunes old copies. The FTP server
and the cipher are doubles here; curl's command line is checked directly.
"""
from __future__ import annotations

import hashlib
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "database"
NOW = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
URL = "ftp://ftp.example.invalid/talky-db-backups/"
SECRET = "netrc-password-must-never-appear"


@pytest.fixture
def mod(monkeypatch):
    path = SCRIPTS / "offsite_ftp.py"
    assert path.is_file(), "F8-01 FTP off-site copy is missing"
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("qa_offsite_ftp", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Cipher:
    """Reversible stand-in: not cryptography, only the publish contract."""

    def __init__(self, *, corrupt=False):
        self.corrupt = corrupt

    def encrypt(self, source: Path, target: Path) -> None:
        target.write_bytes(b"ENC:" + source.read_bytes())

    def decrypted_digest(self, source: Path):
        plain = source.read_bytes()[len(b"ENC:"):]
        if self.corrupt:
            plain += b"!"
        return hashlib.sha256(plain).hexdigest(), len(plain)


class Server:
    """An FTP directory: what was uploaded, listed, deleted and downloaded."""

    def __init__(self, names=(), *, short_by=0):
        self.files = {name: b"old" for name in names}
        self.deleted = []
        self.short_by = short_by

    def upload(self, local: Path, name: str) -> None:
        self.files[name] = local.read_bytes()

    def size(self, name: str) -> int:
        return len(self.files[name]) - self.short_by

    def names(self):
        return list(self.files)

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.files.pop(name, None)

    def download(self, name: str, target: Path) -> None:
        target.write_bytes(self.files[name])


def _dump(tmp_path, stamp="20261112T023000Z", body=b"PGDMP custom archive"):
    dump = tmp_path / f"talky-{stamp}.dump"
    dump.write_bytes(body)
    dump.with_name(dump.name + ".sha256").write_text(f"{hashlib.sha256(body).hexdigest()}  {dump}\n")
    return dump


def test_publish_uploads_a_copy_that_decrypts_to_the_dump(mod, tmp_path):
    dump = _dump(tmp_path)
    server = Server()
    receipt = mod.publish(dump, server, Cipher(), keep_days=30, now=NOW)
    copy = server.files["talky-20261112T023000Z.dump.gpg"]
    assert copy == b"ENC:" + dump.read_bytes(), "only the encrypted copy leaves the server"
    assert receipt.startswith("offsite-ftp: ok talky-20261112T023000Z.dump.gpg bytes=")
    assert dump.read_bytes() not in receipt.encode()


def test_a_dump_that_does_not_match_its_checksum_is_never_uploaded(mod, tmp_path):
    dump = _dump(tmp_path)
    dump.write_bytes(b"changed after the checksum")
    server = Server()
    with pytest.raises(mod.RecoveryError, match="does not match its .sha256"):
        mod.publish(dump, server, Cipher(), now=NOW)
    assert server.files == {}


def test_a_copy_that_does_not_decrypt_to_the_dump_is_never_uploaded(mod, tmp_path):
    server = Server()
    with pytest.raises(mod.RecoveryError, match="does not decrypt to the dump"):
        mod.publish(_dump(tmp_path), server, Cipher(corrupt=True), now=NOW)
    assert server.files == {}


def test_a_short_upload_is_deleted_and_reported(mod, tmp_path):
    server = Server(short_by=1)
    with pytest.raises(mod.RecoveryError, match="size does not match"):
        mod.publish(_dump(tmp_path), server, Cipher(), now=NOW)
    assert server.deleted == ["talky-20261112T023000Z.dump.gpg"]


def test_retention_keeps_the_newest_copies_and_deletes_only_old_ones(mod, tmp_path):
    old = [f"talky-202609{day:02d}T023000Z.dump.gpg" for day in (1, 2, 3)]
    recent = ["talky-20261110T023000Z.dump.gpg", "talky-20261111T023000Z.dump.gpg"]
    server = Server(old + recent + ["notes.txt", "talky-20260101T000000Z.dump"])
    mod.publish(_dump(tmp_path), server, Cipher(), keep_days=30, now=NOW)
    assert sorted(server.deleted) == old
    assert "notes.txt" in server.files and "talky-20260101T000000Z.dump" in server.files, "only our encrypted copies"
    assert set(recent) <= set(server.files)


def test_the_newest_copies_stay_even_when_all_are_old(mod, tmp_path):
    old = [f"talky-202601{day:02d}T023000Z.dump.gpg" for day in range(1, 6)]
    server = Server(old)
    mod.prune(server, 30, NOW, keep="talky-20260101T023000Z.dump.gpg")
    assert sorted(server.deleted) == ["talky-20260102T023000Z.dump.gpg"], "newest 3 and the kept name stay"


def test_verify_downloads_decrypts_and_matches_the_local_dump(mod, tmp_path):
    dump = _dump(tmp_path)
    server = Server()
    mod.publish(dump, server, Cipher(), now=NOW)
    assert mod.verify(server, Cipher(), tmp_path).startswith("offsite-ftp: verify ok talky-20261112T023000Z.dump.gpg matches")
    with pytest.raises(mod.RecoveryError, match="not to the local dump"):
        mod.verify(server, Cipher(corrupt=True), tmp_path)


def test_curl_requires_tls_and_never_carries_the_login(mod, tmp_path, monkeypatch):
    netrc = tmp_path / "ftp.netrc"
    netrc.write_text(f"machine ftp.example.invalid login talky password {SECRET}\n")
    netrc.chmod(0o600)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        stdout = "Content-Length: 12\r\n" if "--head" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", run)
    ftp = mod.Ftp(URL, netrc, curl="curl")
    payload = tmp_path / "talky-20261112T023000Z.dump.gpg"
    payload.write_bytes(b"x" * 12)
    ftp.upload(payload, payload.name)
    assert ftp.size(payload.name) == 12
    ftp.delete("talky-20260101T023000Z.dump.gpg")
    for command in commands:
        assert "--ssl-reqd" in command, "TLS is required, never optional"
        assert "--netrc-file" in command
        assert "-k" not in command and "--insecure" not in command, "the certificate is always checked"
        assert all(SECRET not in part for part in command)
    assert commands[0][-1] == URL + payload.name and "--ftp-create-dirs" in commands[0]
    assert commands[2][-2:] == ["DELE talky-db-backups/talky-20260101T023000Z.dump.gpg", "ftp://ftp.example.invalid/"]


@pytest.mark.parametrize("url", [
    "ftps://ftp.example.invalid/backups/",
    "ftp://talky:pw@ftp.example.invalid/backups/",
    "ftp://ftp.example.invalid/backups",
    "http://ftp.example.invalid/backups/",
    "",
])
def test_only_a_plain_ftp_directory_url_without_a_login_is_accepted(mod, tmp_path, url):
    netrc = tmp_path / "ftp.netrc"
    netrc.write_text("machine x login y password z\n")
    netrc.chmod(0o600)
    with pytest.raises(mod.RecoveryError, match="TALKY_FTP_BACKUP_URL"):
        mod.Ftp(url, netrc)


def test_missing_settings_fail_without_printing_anything_secret(mod, monkeypatch, capsys):
    for name in ("TALKY_FTP_BACKUP_URL", "TALKY_FTP_NETRC", "TALKY_FTP_PASSPHRASE_FILE"):
        monkeypatch.delenv(name, raising=False)
    assert mod.main(["publish", "--dump", "talky-20261112T023000Z.dump"]) == 1
    assert "OFFSITE-FTP FAILED" in capsys.readouterr().err
