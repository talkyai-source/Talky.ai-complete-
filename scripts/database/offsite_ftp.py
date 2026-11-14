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

Recordings (F8-02, off by default): ``sync-recordings`` encrypts each file in
TALKY_RECORDINGS_DIR (else LOCAL_RECORDINGS_DIR, the app's setting) with the
same passphrase and uploads it as rec-<id>.gpg; an encrypted manifest on the
server (recordings-manifest.json.gpg) makes runs incremental. It never deletes,
stops after TALKY_OFFSITE_FTP_RECORDINGS_MAX_BYTES (default 524288000) and
reports how many files remain and how many manifest entries have no local
file (orphans). Recordings deleted locally stay on the FTP server until someone
removes them there, which matters for erase requests. ``verify-recordings`` downloads the newest N
(--sample, default 3), decrypts and compares with the manifest. db-backup.sh
runs the sync only when TALKY_OFFSITE_FTP_RECORDINGS=1, after the DB upload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
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
RECORDINGS_MANIFEST = "recordings-manifest.json.gpg"
DEFAULT_RECORDINGS_BUDGET = 500 * 1024 * 1024
DEFAULT_VERIFY_SAMPLE = 3
MANIFEST_EVERY = 25


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


def _recordings_dir() -> Path:
    # Same directory the app writes to (LOCAL_RECORDINGS_DIR); offhost.py's
    # TALKY_RECORDINGS_DIR wins when set so both routes read the same tree.
    configured = os.getenv("TALKY_RECORDINGS_DIR") or os.getenv("LOCAL_RECORDINGS_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "backend" / "recordings"


def _inventory(root: Path) -> list[tuple[str, Path, int, int]]:
    """Regular files under root (symlinks ignored), newest first."""
    root = root.resolve(strict=True)
    found = []

    def walk_failed(error):
        raise RecoveryError("the recordings directory could not be read completely") from error

    for directory, _subdirs, names in os.walk(root, followlinks=False, onerror=walk_failed):
        for name in names:
            path = Path(directory) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            relative = path.relative_to(root).as_posix()
            found.append((relative, path, info.st_size, info.st_mtime_ns))
    found.sort(key=lambda item: (-item[3], item[0]))
    return found


def _load_manifest(ftp: Ftp, cipher, work: Path) -> dict:
    if RECORDINGS_MANIFEST not in ftp.names():
        return {}
    encrypted, plain = work / "manifest.in", work / "manifest.json"
    ftp.download(RECORDINGS_MANIFEST, encrypted)
    cipher.decrypt(encrypted, plain)
    try:
        manifest = json.loads(plain.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RecoveryError("the remote recordings manifest is unreadable") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict):
        raise RecoveryError("the remote recordings manifest is malformed")
    return files


def _store_manifest(ftp: Ftp, cipher, files: dict, work: Path) -> None:
    plain, encrypted = work / "manifest.out", work / RECORDINGS_MANIFEST
    plain.write_text(json.dumps({"format": 1, "files": files}, sort_keys=True), encoding="utf-8")
    cipher.encrypt(plain, encrypted)
    ftp.upload(encrypted, RECORDINGS_MANIFEST)
    if ftp.size(RECORDINGS_MANIFEST) != encrypted.stat().st_size:
        raise RecoveryError("the FTP copy of the recordings manifest does not match what was sent")


def sync_recordings(root: Path, ftp: Ftp, cipher, budget_bytes: int = DEFAULT_RECORDINGS_BUDGET) -> str:
    """Upload new or changed recordings, encrypted, newest first, until the byte
    budget is spent. Never deletes anything. A file already on the server with
    the same relative path and content hash (per the remote manifest) is skipped.
    """
    try:
        inventory = _inventory(root)
    except OSError as exc:
        raise RecoveryError("the recordings directory is missing or unreadable") from exc
    present = {item[0] for item in inventory}
    work = Path(tempfile.mkdtemp(prefix="talky-offsite-rec-"))
    uploaded = skipped = failed = sent = 0
    remaining = 0
    orphans = 0
    dirty = False
    error: RecoveryError | None = None
    try:
        files = _load_manifest(ftp, cipher, work)
        orphans = sum(1 for name in files if name not in present)

        def recorded(relative: str, size: int, mtime_ns: int) -> bool:
            entry = files.get(relative)
            return isinstance(entry, dict) and entry.get("bytes") == size and entry.get("mtime_ns") == mtime_ns

        for position, (relative, path, size, mtime_ns) in enumerate(inventory):
            if recorded(relative, size, mtime_ns):
                skipped += 1  # same size and mtime as when uploaded: not read again
                continue
            try:
                plain_sha = digest(path)
            except OSError:
                failed += 1
                continue
            entry = files.get(relative)
            if isinstance(entry, dict) and entry.get("sha256") == plain_sha:
                entry.update(bytes=size, mtime_ns=mtime_ns)  # touched, not changed
                dirty = True
                skipped += 1
                continue
            if uploaded and sent + size > budget_bytes:
                remaining = sum(1 for r, _p, sz, mt in inventory[position:] if not recorded(r, sz, mt))
                break  # budget spent: the next run continues from here
            remote = f"rec-{hashlib.sha256(relative.encode()).hexdigest()[:16]}-{plain_sha[:16]}.gpg"
            encrypted = work / "file.gpg"
            try:
                cipher.encrypt(path, encrypted)
                decrypted, _size = cipher.decrypted_digest(encrypted)
                if decrypted != plain_sha:
                    raise RecoveryError("an encrypted recording does not decrypt to the original")
                local_size = encrypted.stat().st_size
                ftp.upload(encrypted, remote)
                if ftp.size(remote) != local_size:
                    raise RecoveryError("an FTP recording copy's size does not match what was sent")
            except OSError:
                failed += 1
                continue
            except RecoveryError as exc:
                error = exc
                break
            finally:
                encrypted.unlink(missing_ok=True)
            files[relative] = {"sha256": plain_sha, "bytes": size, "remote": remote, "mtime_ns": mtime_ns}
            uploaded += 1
            sent += size
            if uploaded % MANIFEST_EVERY == 0:
                try:
                    _store_manifest(ftp, cipher, files, work)
                except RecoveryError as exc:
                    error = exc
                    break
        if uploaded or dirty:
            try:
                _store_manifest(ftp, cipher, files, work)
            except RecoveryError as exc:
                error = error or exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if error is not None:
        raise error
    if failed:
        raise RecoveryError(f"{failed} recording(s) could not be read or encrypted; uploaded={uploaded}")
    return (f"offsite-ftp: recordings ok uploaded={uploaded} skipped={skipped} bytes={sent} "
            f"remaining={remaining} orphans={orphans} budget={budget_bytes}")


def verify_recordings(ftp: Ftp, cipher, sample: int = DEFAULT_VERIFY_SAMPLE) -> str:
    """Download the newest `sample` uploaded recordings, decrypt, compare to the manifest hash."""
    work = Path(tempfile.mkdtemp(prefix="talky-offsite-rec-verify-"))
    try:
        files = _load_manifest(ftp, cipher, work)
        if not files:
            raise RecoveryError("there are no uploaded recordings in the remote manifest")
        newest = sorted(files.items(), key=lambda item: (-int(item[1].get("mtime_ns", 0)), item[0]))[:max(1, sample)]
        bad = 0
        for _relative, entry in newest:
            target = work / "sample.gpg"
            try:
                ftp.download(entry["remote"], target)
                decrypted, _size = cipher.decrypted_digest(target)
            except (RecoveryError, KeyError):
                bad += 1
                continue
            finally:
                target.unlink(missing_ok=True)
            if decrypted != entry.get("sha256"):
                bad += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if bad:
        raise RecoveryError(f"{bad} of {len(newest)} sampled recordings failed verification")
    return f"offsite-ftp: recordings verify ok sampled={len(newest)} total_uploaded={len(files)}"


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
    sub.add_parser("sync-recordings", help="encrypt and upload new recordings, within a byte budget")
    rver = sub.add_parser("verify-recordings", help="download, decrypt and check the newest uploaded recordings")
    rver.add_argument("--sample", type=int, default=DEFAULT_VERIFY_SAMPLE)
    args = parser.parse_args(argv)
    try:
        ftp, cipher, keep_days = _settings()
        if args.command == "sync-recordings":
            try:
                budget = int(os.getenv("TALKY_OFFSITE_FTP_RECORDINGS_MAX_BYTES", str(DEFAULT_RECORDINGS_BUDGET)))
            except ValueError as exc:
                raise RecoveryError("TALKY_OFFSITE_FTP_RECORDINGS_MAX_BYTES must be a whole number of bytes") from exc
            if budget < 1:
                raise RecoveryError("TALKY_OFFSITE_FTP_RECORDINGS_MAX_BYTES must be positive")
            print(sync_recordings(_recordings_dir(), ftp, cipher, budget))
        elif args.command == "verify-recordings":
            print(verify_recordings(ftp, cipher, args.sample))
        elif args.command == "publish":
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
