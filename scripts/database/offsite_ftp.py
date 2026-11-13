#!/usr/bin/env python3
"""Encrypted copies of each nightly database dump on the owner's FTP server.

db-backup.sh calls ``publish --dump <dump>`` when TALKY_FTP_BACKUP_URL is set
(in /etc/talky/db-backup.env, readable by root only). Each dump is encrypted
with the recovery primitive (gpg AES256, passphrase file), the encrypted copy
is proved to decrypt to the dump's exact bytes, and only then uploaded over FTP
with TLS required (curl --ssl-reqd, certificate verified). Its size is checked
on the server, and remote copies older than the retention are deleted (the
newest few always stay). The login comes only from a 0600 netrc file: no secret
is on a command line, in an environment variable or in any output.

``verify`` downloads the newest (or a named) remote copy, decrypts it and
compares it with the local dump's .sha256: a restore from the FTP server works.

Settings (environment): TALKY_FTP_BACKUP_URL (ftp://host/dir/), TALKY_FTP_NETRC,
TALKY_FTP_PASSPHRASE_FILE, TALKY_FTP_KEEP_DAYS (default 30), TALKY_BACKUP_DIR.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recovery import Gpg, RecoveryError, digest, private_file  # noqa: E402

DUMP = re.compile(r"talky-(\d{8}T\d{6}Z)\.dump\Z")
REMOTE = re.compile(r"talky-(\d{8}T\d{6}Z)\.dump\.gpg\Z")
URL = re.compile(r"ftp://[A-Za-z0-9.-]+(:\d{1,5})?/([A-Za-z0-9._-]+/)*\Z")
KEEP_NEWEST = 3
CURL_TIMEOUT = 1800


def _stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


class Ftp:
    """curl over FTP with TLS required; the netrc file carries the login."""

    def __init__(self, url: str, netrc: Path, curl: str | None = None):
        if not URL.fullmatch(url or ""):
            raise RecoveryError("TALKY_FTP_BACKUP_URL must be ftp://host/directory/ (ending in /, no login in it)")
        self.url = url
        self.directory = urlsplit(url).path.lstrip("/")
        self.root = url[: len(url) - len(urlsplit(url).path)] + "/"
        self.netrc = private_file(Path(netrc))
        self.curl = curl or shutil.which("curl") or "curl"

    def _run(self, args: list[str], failure: str) -> str:
        command = [self.curl, "--ssl-reqd", "--fail", "--silent", "--show-error",
                   "--netrc-file", self.netrc.as_posix(), "--connect-timeout", "30",
                   "--max-time", str(CURL_TIMEOUT), *args]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=CURL_TIMEOUT + 60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RecoveryError(failure) from exc
        if result.returncode != 0:
            # curl's own message can quote the server; the exit code is enough to act on.
            raise RecoveryError(f"{failure} (curl exit {result.returncode})")
        return result.stdout

    def upload(self, local: Path, name: str) -> None:
        self._run(["--ftp-create-dirs", "--upload-file", local.as_posix(), self.url + name],
                  "the FTP upload failed")

    def size(self, name: str) -> int:
        out = self._run(["--head", self.url + name], "the uploaded copy could not be checked")
        match = re.search(r"Content-Length:\s*(\d+)", out, re.IGNORECASE)
        if not match:
            raise RecoveryError("the FTP server did not report the uploaded copy's size")
        return int(match.group(1))

    def names(self) -> list[str]:
        out = self._run(["--list-only", self.url], "the FTP backup directory could not be listed")
        return [line.strip() for line in out.splitlines() if line.strip()]

    def delete(self, name: str) -> None:
        # Quote commands run before the transfer, from the login directory, so
        # the path names the backup directory as the URL does.
        self._run(["--quote", f"DELE {self.directory}{name}", self.root],
                  "an old FTP copy could not be deleted")

    def download(self, name: str, target: Path) -> None:
        self._run(["--output", target.as_posix(), self.url + name], "the FTP download failed")


def _recorded_digest(dump: Path) -> str:
    checksum = dump.with_name(dump.name + ".sha256")
    try:
        recorded = checksum.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError) as exc:
        raise RecoveryError("the dump has no readable .sha256 beside it") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", recorded):
        raise RecoveryError("the dump's .sha256 is malformed")
    return recorded


def prune(ftp: Ftp, keep_days: int, now: datetime, *, keep: str) -> list[str]:
    """Delete remote copies older than keep_days; the newest KEEP_NEWEST and
    the copy just published always stay."""
    copies = sorted((_stamp(m.group(1)), name) for name in ftp.names() if (m := REMOTE.fullmatch(name)))
    protected = {name for _, name in copies[-KEEP_NEWEST:]} | {keep}
    cutoff = now - timedelta(days=keep_days)
    removed = []
    for stamp, name in copies:
        if name not in protected and stamp < cutoff:
            ftp.delete(name)
            removed.append(name)
    return removed


def publish(dump: Path, ftp: Ftp, cipher, keep_days: int = 30, now: datetime | None = None) -> str:
    match = DUMP.fullmatch(dump.name)
    if not match or not dump.is_file():
        raise RecoveryError("the dump to publish is missing or misnamed")
    recorded = _recorded_digest(dump)
    if digest(dump) != recorded:
        raise RecoveryError("the dump does not match its .sha256")
    remote = dump.name + ".gpg"
    work = Path(tempfile.mkdtemp(prefix="talky-offsite-"))
    try:
        encrypted = work / remote
        cipher.encrypt(dump, encrypted)
        decrypted, _size = cipher.decrypted_digest(encrypted)
        if decrypted != recorded:
            raise RecoveryError("the encrypted copy does not decrypt to the dump")
        local_size = encrypted.stat().st_size
        ftp.upload(encrypted, remote)
        if ftp.size(remote) != local_size:
            try:
                ftp.delete(remote)
            except RecoveryError:
                pass  # the size mismatch below is the error to report
            raise RecoveryError("the FTP copy's size does not match what was sent")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    removed = prune(ftp, keep_days, now or datetime.now(timezone.utc), keep=remote)
    return (f"offsite-ftp: ok {remote} bytes={local_size} sha256={recorded[:12]}… "
            f"kept_days={keep_days} pruned={len(removed)}")


def verify(ftp: Ftp, cipher, backup_dir: Path, name: str | None = None) -> str:
    copies = sorted(n for n in ftp.names() if REMOTE.fullmatch(n))
    if name is None:
        if not copies:
            raise RecoveryError("there is no encrypted copy on the FTP server")
        name = copies[-1]
    elif name not in copies:
        raise RecoveryError("that copy is not on the FTP server")
    work = Path(tempfile.mkdtemp(prefix="talky-offsite-verify-"))
    try:
        downloaded = work / name
        ftp.download(name, downloaded)
        decrypted, size = cipher.decrypted_digest(downloaded)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    local = backup_dir / name[: -len(".gpg")]
    if local.with_name(local.name + ".sha256").is_file():
        if decrypted != _recorded_digest(local):
            raise RecoveryError("the FTP copy decrypts, but not to the local dump's bytes")
        return f"offsite-ftp: verify ok {name} matches {local.name} sha256={decrypted[:12]}… bytes={size}"
    return f"offsite-ftp: verify ok {name} decrypts sha256={decrypted[:12]}… bytes={size} (no local copy to compare)"


def _settings() -> tuple[Ftp, Gpg, int]:
    url = os.getenv("TALKY_FTP_BACKUP_URL", "")
    netrc = os.getenv("TALKY_FTP_NETRC", "")
    passphrase = os.getenv("TALKY_FTP_PASSPHRASE_FILE", "")
    if not (url and netrc and passphrase):
        raise RecoveryError("TALKY_FTP_BACKUP_URL, TALKY_FTP_NETRC and TALKY_FTP_PASSPHRASE_FILE are all required")
    try:
        keep_days = int(os.getenv("TALKY_FTP_KEEP_DAYS", "30"))
    except ValueError as exc:
        raise RecoveryError("TALKY_FTP_KEEP_DAYS must be a whole number of days") from exc
    if not 1 <= keep_days <= 3650:
        raise RecoveryError("TALKY_FTP_KEEP_DAYS must be between 1 and 3650")
    return Ftp(url, Path(netrc)), Gpg(Path(passphrase)), keep_days


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish", help="encrypt, upload and verify one dump, then apply retention")
    pub.add_argument("--dump", required=True, type=Path)
    ver = sub.add_parser("verify", help="download, decrypt and check the newest (or a named) remote copy")
    ver.add_argument("--name")
    args = parser.parse_args(argv)
    try:
        ftp, cipher, keep_days = _settings()
        if args.command == "publish":
            print(publish(args.dump, ftp, cipher, keep_days))
        else:
            backup_dir = Path(os.getenv("TALKY_BACKUP_DIR", "/var/backups/talky"))
            print(verify(ftp, cipher, backup_dir, args.name))
    except RecoveryError as exc:
        print(f"OFFSITE-FTP FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
