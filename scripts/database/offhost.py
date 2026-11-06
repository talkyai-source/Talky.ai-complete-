#!/usr/bin/env python3
"""Encrypted, verified off-host DB+recordings copies of the existing flat dump.

Nothing is uploaded without an explicit owner-pinned remote mount destination.
No DB credentials, application environment file, SSH or provider API is used.
The mount is an owner-provisioned NFS/CIFS/SSHFS filesystem, not a local directory
labelled off-host. Encryption reuses the selected a0bd0353 recovery primitive.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recovery import Gpg, RecoveryError, digest, sync_file, write_json_new  # noqa: E402

DUMP = re.compile(r"talky-(\d{8}T\d{6}Z)\.dump\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
REMOTE_FS = {"nfs", "nfs4", "cifs", "fuse.sshfs"}
MAX_FILES = 100000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def _path(path: Path, *, directory=False) -> Path:
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise RecoveryError("backup paths must not contain symbolic links")
    if not (path.is_dir() if directory else path.is_file()):
        raise RecoveryError("required backup input or destination is missing")
    return path.resolve(strict=True)


def _now(now):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise RecoveryError("backup time must include a UTC offset")
    return value.astimezone(timezone.utc)


def _age(snapshot, maximum, now):
    if not isinstance(maximum, (float, int)) or not math.isfinite(maximum) or maximum <= 0:
        raise RecoveryError("maximum backup age must be finite and positive")
    try:
        stamp = datetime.fromisoformat(snapshot)
        if stamp.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise RecoveryError("backup snapshot timestamp is invalid") from exc
    age = (_now(now) - stamp).total_seconds() / 3600
    if age < -1 / 60 or age > maximum:
        raise RecoveryError("backup snapshot is stale or in the future")
    return age


def _json(path, maximum=65536):
    path = _path(path)
    if path.stat().st_size > maximum:
        raise RecoveryError("backup metadata exceeds its size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RecoveryError("backup metadata is invalid") from exc
    if not isinstance(value, dict):
        raise RecoveryError("backup metadata must be an object")
    return value


def _flat(dump):
    dump = _path(dump)
    match = DUMP.fullmatch(dump.name)
    if not match:
        raise RecoveryError("dump must have the verified producer's timestamped filename")
    try:
        stamp = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RecoveryError("dump timestamp is invalid") from exc
    sidecar = _path(Path(str(dump) + ".sha256"))
    if sidecar.stat().st_size > 8192:
        raise RecoveryError("dump checksum sidecar is invalid")
    parts = sidecar.read_text(encoding="utf-8").strip().split(maxsplit=1)
    if (len(parts) != 2 or not HEX.fullmatch(parts[0])
            or parts[1].lstrip("*") not in {str(dump), dump.as_posix(), dump.name}):
        raise RecoveryError("dump checksum sidecar does not identify this dump")
    size = dump.stat().st_size
    if size <= 0 or digest(dump) != parts[0]:
        raise RecoveryError("local dump failed checksum verification")
    return {"snapshot_time": stamp.isoformat(), "bytes": size, "sha256": parts[0]}


def local_health(root, maximum_age_hours=26, now=None):
    root = _path(root, directory=True)
    candidates = sorted(p for p in root.iterdir() if DUMP.fullmatch(p.name))
    if not candidates:
        raise RecoveryError("no published local dump exists")
    result = _flat(candidates[-1])
    return {"status": "local_verified", **result,
            "age_hours": _age(result["snapshot_time"], maximum_age_hours, now)}


def inspect_remote(destination, expected_source, expected_fstype):
    destination = _path(destination, directory=True)
    if not expected_source or expected_fstype not in REMOTE_FS:
        raise RecoveryError("an explicit supported remote mount source and filesystem type are required")
    if any(ch in expected_source for ch in "\r\n\x00"):
        raise RecoveryError("remote mount source is invalid")
    host = expected_source.lstrip("/").split("/", 1)[0] if expected_fstype == "cifs" else expected_source.split(":/", 1)[0]
    host = host.rsplit("@", 1)[-1].strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or not host:
        raise RecoveryError("loopback is not an off-host destination")
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback or address.is_unspecified:
            raise RecoveryError("loopback is not an off-host destination")
    except ValueError:
        pass
    if expected_fstype == "cifs":
        valid_source = expected_source.startswith("//") and "/" in expected_source[2:]
    else:
        valid_source = ":/" in expected_source and not expected_source.startswith("/")
    if not valid_source:
        raise RecoveryError("remote mount source is not a network location")
    try:
        result = subprocess.run(["findmnt", "--json", "--target", str(destination),
                                 "--output", "TARGET,SOURCE,FSTYPE,OPTIONS"],
                                capture_output=True, text=True, timeout=5, check=False)
        if result.returncode or len(result.stdout) > 65536:
            raise RecoveryError("could not establish the destination mount identity")
        mounts = json.loads(result.stdout)["filesystems"]
        if len(mounts) != 1:
            raise RecoveryError("destination mount identity is ambiguous")
        mount = mounts[0]
        target = _path(Path(mount["target"]), directory=True)
        if (mount["source"] != expected_source or mount["fstype"] != expected_fstype
                or "rw" not in mount["options"].split(",")
                or target != destination and target not in destination.parents):
            raise RecoveryError("destination is not the configured writable remote mount")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise RecoveryError("could not establish the destination mount identity") from exc
    identity = json.dumps([str(target), expected_source, expected_fstype], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


class _HashReader:
    def __init__(self, stream):
        self.stream, self.hasher, self.size = stream, hashlib.sha256(), 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.hasher.update(data)
        self.size += len(data)
        return data


def _signature(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise RecoveryError("backup inputs must be regular files")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _payload(dump, recordings, archive, snapshot):
    recordings = _path(recordings, directory=True)
    files = [("database.dump", _path(dump))]
    directories = {}
    captured = {}
    def walk_failed(error):
        raise RecoveryError("recording inventory could not be read completely") from error
    for directory, subdirs, names in os.walk(recordings, followlinks=False, onerror=walk_failed):
        directory_path = _path(Path(directory), directory=True)
        info = directory_path.stat()
        directories[directory_path] = (info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
        for name in subdirs:
            _path(Path(directory) / name, directory=True)
        for name in names:
            path = _path(Path(directory) / name)
            relative = path.relative_to(recordings).as_posix()
            if "\\" in relative or any(":" in part or part in {"", ".", ".."} for part in relative.split("/")):
                raise RecoveryError("recording filename is not portable or safe")
            files.append(("recordings/" + relative, path))
            if len(files) > MAX_FILES:
                raise RecoveryError("recording inventory exceeds the explicit file-count limit")
    manifest = {"format": 1, "snapshot_time": snapshot["snapshot_time"], "files": {}}
    required = sum(path.stat().st_size for _, path in files) + MAX_MANIFEST_BYTES
    if shutil.disk_usage(archive.parent).free < required:
        raise RecoveryError("insufficient private staging capacity for the backup payload")
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
        for name, path in files:
            before = _signature(path)
            info = tarfile.TarInfo(name)
            info.size, info.mode = before[2], 0o600
            with path.open("rb") as stream:
                reader = _HashReader(stream)
                tar.addfile(info, reader)
            if before != _signature(path) or reader.size != before[2]:
                raise RecoveryError("a backup input changed during capture; quiesce writers and retry")
            captured[path] = before
            manifest["files"][name] = {"bytes": reader.size, "sha256": reader.hasher.hexdigest()}
        if manifest["files"]["database.dump"]["sha256"] != snapshot["sha256"]:
            raise RecoveryError("database dump changed after its verification")
        encoded = json.dumps(manifest, sort_keys=True).encode()
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise RecoveryError("backup inventory exceeds its metadata limit")
        info = tarfile.TarInfo("manifest.json")
        info.size, info.mode = len(encoded), 0o600
        tar.addfile(info, io.BytesIO(encoded))
    for path, signature in captured.items():
        if _signature(path) != signature:
            raise RecoveryError("a backup input changed during capture; quiesce writers and retry")
    for path, signature in directories.items():
        info = _path(path, directory=True).stat()
        if (info.st_ino, info.st_mtime_ns, info.st_ctime_ns) != signature:
            raise RecoveryError("recording inventory changed during capture; quiesce writers and retry")
    return len(files) - 1


def publish(*, dump, recordings, destination=None, passphrase_file=None,
            expected_source=None, expected_fstype=None, now=None):
    if not destination:
        return {"status": "unconfigured", "uploaded": False}
    staging = None
    locked = False
    destination = _path(destination, directory=True)
    lock = destination / ".talky-offhost-publish.lock"
    try:
        identity = inspect_remote(destination, expected_source, expected_fstype)
        if passphrase_file is None:
            raise RecoveryError("an encryption passphrase file is required")
        cipher = Gpg(Path(passphrase_file))
        snapshot = _flat(dump)
        _age(snapshot["snapshot_time"], 26, now)
        target = destination / Path(dump).stem
        with lock.open("x"):
            locked = True
        if target.exists() or target.is_symlink():
            raise RecoveryError("refusing to overwrite a published remote backup")
        staging = Path(tempfile.mkdtemp(prefix=".talky-offhost-", dir=destination))
        with tempfile.TemporaryDirectory(prefix=".talky-payload-", dir=Path(dump).parent) as temporary:
            payload = Path(temporary) / "payload.tar"
            count = _payload(dump, recordings, payload, snapshot)
            plaintext_hash, plaintext_size = digest(payload), payload.stat().st_size
            encrypted = staging / "payload.tar.gpg"
            cipher.encrypt(payload, encrypted)
            if cipher.decrypted_digest(encrypted) != (plaintext_hash, plaintext_size):
                raise RecoveryError("remote encrypted readback does not match the captured payload")
            receipt = {"format": 1, "status": "remote_verified", "encrypted": True,
                       "snapshot_time": snapshot["snapshot_time"], "published_at": _now(now).isoformat(),
                       "recording_files": count, "database_sha256": snapshot["sha256"],
                       "payload_sha256": plaintext_hash, "payload_bytes": plaintext_size,
                       "ciphertext_sha256": digest(encrypted), "ciphertext_bytes": encrypted.stat().st_size,
                       "mount_fingerprint": identity}
            write_json_new(staging / "receipt.json", receipt)
            sync_file(encrypted)
            if inspect_remote(destination, expected_source, expected_fstype) != identity:
                raise RecoveryError("destination mount changed before publication")
            _verified(staging)
            staging.rename(target)
            staging = None
            return {**receipt, "uploaded": True}
    except RecoveryError:
        raise
    except Exception as exc:
        raise RecoveryError("encrypted off-host backup failed; inspect private inputs and mount health") from exc
    finally:
        if staging is not None and staging.parent == destination and staging.name.startswith(".talky-offhost-"):
            shutil.rmtree(staging)
        if locked:
            lock.unlink()


def _verified(bundle):
    bundle = _path(bundle, directory=True)
    if {p.name for p in bundle.iterdir()} != {"receipt.json", "payload.tar.gpg"}:
        raise RecoveryError("remote bundle contains missing or unexpected files")
    receipt = _json(bundle / "receipt.json")
    encrypted = _path(bundle / "payload.tar.gpg")
    if (receipt.get("format") != 1 or receipt.get("status") != "remote_verified" or receipt.get("encrypted") is not True
            or not HEX.fullmatch(str(receipt.get("payload_sha256", "")))
            or not HEX.fullmatch(str(receipt.get("mount_fingerprint", "")))
            or not isinstance(receipt.get("payload_bytes"), int) or receipt["payload_bytes"] <= 0
            or encrypted.stat().st_size != receipt.get("ciphertext_bytes")
            or digest(encrypted) != receipt.get("ciphertext_sha256")):
        raise RecoveryError("remote bundle failed receipt/checksum verification")
    return receipt


def remote_health(destination, *, expected_source, expected_fstype, maximum_age_hours=26, now=None):
    destination = _path(destination, directory=True)
    identity = inspect_remote(destination, expected_source, expected_fstype)
    candidates = sorted(p for p in destination.iterdir() if DUMP.fullmatch(p.name + ".dump"))
    if not candidates:
        raise RecoveryError("no published remote backup exists")
    receipt = _verified(candidates[-1])
    if receipt["mount_fingerprint"] != identity:
        raise RecoveryError("remote backup belongs to a different mount identity")
    return {**receipt, "age_hours": _age(receipt["snapshot_time"], maximum_age_hours, now)}


def restore_payload(bundle, destination, passphrase_file):
    receipt = _verified(bundle)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise RecoveryError("restore destination must be new; existing files are never overwritten")
    _path(destination.parent, directory=True)
    created = False
    try:
        with tempfile.TemporaryDirectory(prefix=".talky-decrypt-", dir=destination.parent) as temporary:
            plaintext = Path(temporary) / "payload.tar"
            Gpg(Path(passphrase_file)).decrypt(Path(bundle) / "payload.tar.gpg", plaintext)
            if plaintext.stat().st_size != receipt["payload_bytes"] or digest(plaintext) != receipt["payload_sha256"]:
                raise RecoveryError("decrypted backup failed payload integrity verification")
            with tarfile.open(plaintext, "r:") as tar:
                members = tar.getmembers()
                names = set()
                if len(members) > MAX_FILES + 1:
                    raise RecoveryError("restored archive exceeds the file-count limit")
                for member in members:
                    name = member.name
                    path = PurePosixPath(name)
                    if (not member.isfile() or name in names or not name or "\\" in name or ":" in name
                            or path.is_absolute() or ".." in path.parts
                            or str(path) != name or name not in {"database.dump", "manifest.json"}
                            and not name.startswith("recordings/")):
                        raise RecoveryError("restored archive contains an unsafe or unexpected member")
                    names.add(name)
                if not {"database.dump", "manifest.json"}.issubset(names):
                    raise RecoveryError("restored archive is incomplete")
                manifest_member = tar.getmember("manifest.json")
                if manifest_member.size > MAX_MANIFEST_BYTES:
                    raise RecoveryError("restored manifest exceeds its limit")
                manifest = json.load(tar.extractfile(manifest_member))
                if set(manifest["files"]) != names - {"manifest.json"}:
                    raise RecoveryError("restored archive and manifest differ")
                destination.mkdir(mode=0o700)
                created = True
                for member in members:
                    target = destination / member.name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with target.open("xb") as output, tar.extractfile(member) as source:
                        os.chmod(target, 0o600)
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    if member.name != "manifest.json":
                        expected = manifest["files"][member.name]
                        if target.stat().st_size != expected["bytes"] or digest(target) != expected["sha256"]:
                            raise RecoveryError("restored member failed manifest verification")
            return {"status": "payload_restored", "files": len(members) - 1}
    except Exception as exc:
        if created:
            shutil.rmtree(destination)
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("encrypted payload restore failed; no partial output retained") from exc


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--dump", type=Path, required=True)
    health_parser = commands.add_parser("health")
    health_parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/talky"))
    health_parser.add_argument("--maximum-age-hours", type=float, default=26)
    restore_parser = commands.add_parser("restore-payload")
    restore_parser.add_argument("--bundle", type=Path, required=True)
    restore_parser.add_argument("--destination", type=Path, required=True)
    restore_parser.add_argument("--passphrase-file", type=Path, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        os.umask(0o077)
        destination = os.getenv("TALKY_OFFHOST_ROOT") or None
        source, fstype = os.getenv("TALKY_OFFHOST_SOURCE"), os.getenv("TALKY_OFFHOST_FSTYPE")
        if args.command == "publish":
            recordings = os.getenv("TALKY_RECORDINGS_DIR") or str(Path(__file__).resolve().parents[2] / "backend/recordings")
            result = publish(dump=args.dump, recordings=Path(recordings), destination=destination,
                             passphrase_file=os.getenv("TALKY_OFFHOST_PASSPHRASE_FILE"),
                             expected_source=source, expected_fstype=fstype)
        elif args.command == "health":
            local = local_health(args.backup_root, args.maximum_age_hours)
            if not destination:
                raise RecoveryError("local dump verified but off-host protection is not configured")
            result = {"local": local, "remote": remote_health(destination, expected_source=source,
                        expected_fstype=fstype, maximum_age_hours=args.maximum_age_hours)}
        else:
            result = restore_payload(args.bundle, args.destination, args.passphrase_file)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RecoveryError, OSError, ValueError, TypeError):
        print("OFFHOST FAILED: verify configured mount, private key, input integrity and capacity.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
